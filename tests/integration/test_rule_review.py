import io
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from rich.console import Console
from sqlalchemy.ext.asyncio import AsyncSession

from sahayak.cli.rules import review_rule, rules_table
from sahayak.db.models import EligibilityRule, RuleStatus, Scheme, SchemeStatus
from sahayak.ingestion.index import upsert_scheme
from sahayak.ingestion.manifest import SchemeSpec
from sahayak.ingestion.review import (
    ReviewError,
    apply_edit,
    approve_rule,
    current_issues,
    editable_json,
    reject_rule,
)

URL = "https://example.gov.in/scheme"
GOOD: dict[str, Any] = {"all": [{"field": "age", "op": ">=", "value": 18}]}
BAD: dict[str, Any] = {"all": [{"field": "age", "op": ">=", "value": "adult"}]}
EXCERPTS = [{"url": URL, "section": "Eligibility", "text": "Citizens aged 18 or more."}]


@pytest.fixture
async def scheme(db_session: AsyncSession) -> Scheme:
    spec = SchemeSpec.model_validate(
        {"id": "s1", "names": {"en": "Scheme One"}, "level": "central", "sources": [{"url": URL}]}
    )
    return await upsert_scheme(db_session, spec, verified_at=datetime.now(UTC), content_hash=None)


async def add_rule(
    session: AsyncSession,
    version: int,
    logic: dict[str, Any],
    status: RuleStatus = RuleStatus.DRAFT,
) -> EligibilityRule:
    rule = EligibilityRule(
        scheme_id="s1",
        version=version,
        status=status,
        logic=logic,
        source_refs=[{"url": URL, "section": "Eligibility"}],
        extraction={"excerpts": EXCERPTS, "confidence": 0.7, "model": "fake-model"},
    )
    session.add(rule)
    await session.flush()
    return rule


def scripted(*answers: str) -> Iterator[str]:
    return iter(answers)


async def test_approve_archives_live_rule_and_activates_scheme(
    db_session: AsyncSession, scheme: Scheme
) -> None:
    live = await add_rule(db_session, 1, GOOD, RuleStatus.APPROVED)
    draft = await add_rule(db_session, 2, GOOD)

    await approve_rule(db_session, draft, reviewer="Prathmesh")

    await db_session.refresh(live)
    await db_session.refresh(scheme)
    assert live.status is RuleStatus.ARCHIVED
    assert draft.status is RuleStatus.APPROVED
    assert draft.reviewed_by == "Prathmesh"
    assert draft.reviewed_at is not None
    assert scheme.status is SchemeStatus.ACTIVE


@pytest.mark.usefixtures("scheme")
async def test_rule_with_issues_cannot_be_approved(db_session: AsyncSession) -> None:
    draft = await add_rule(db_session, 1, BAD)

    with pytest.raises(ReviewError, match="fix 1 issue"):
        await approve_rule(db_session, draft, reviewer="r")
    assert draft.status is RuleStatus.DRAFT


@pytest.mark.usefixtures("scheme")
async def test_only_drafts_can_be_reviewed(db_session: AsyncSession) -> None:
    rule = await add_rule(db_session, 1, GOOD, RuleStatus.ARCHIVED)

    with pytest.raises(ReviewError, match="not a draft"):
        await approve_rule(db_session, rule, reviewer="r")
    with pytest.raises(ReviewError, match="not a draft"):
        apply_edit(rule, editable_json(rule))


@pytest.mark.usefixtures("scheme")
async def test_edit_fixes_issues_and_marks_the_rule_edited(db_session: AsyncSession) -> None:
    draft = await add_rule(db_session, 1, BAD)
    document = json.loads(editable_json(draft))
    document["logic"] = GOOD
    document["notes"] = "Fixed the age."

    issues = apply_edit(draft, json.dumps(document))

    assert issues == []
    assert draft.logic == GOOD
    assert draft.notes == "Fixed the age."
    assert draft.review_edited is True
    assert draft.extraction is not None
    assert draft.extraction["issues"] == []
    assert draft.extraction["confidence"] == 0.7  # provenance kept


@pytest.mark.usefixtures("scheme")
@pytest.mark.parametrize(
    "text",
    [
        "not json",
        '{"logic": {"every": []}, "source_refs": []}',
        '{"logic": {"all": []}, "source_refs": [], "status": "approved"}',
        '{"logic": {"all": []}}',
        '{"logic": {"all": []}, "source_refs": [], "notes": 5}',
    ],
)
async def test_invalid_edits_change_nothing(db_session: AsyncSession, text: str) -> None:
    draft = await add_rule(db_session, 1, BAD)

    with pytest.raises(ReviewError):
        apply_edit(draft, text)
    assert draft.logic == BAD
    assert draft.review_edited is False


@pytest.mark.usefixtures("scheme")
async def test_reject_records_reviewer_and_reason(db_session: AsyncSession) -> None:
    draft = await add_rule(db_session, 1, GOOD)

    await reject_rule(db_session, draft, reviewer="r", reason="  Wrong scheme text. ")

    assert draft.status is RuleStatus.REJECTED
    assert draft.extraction is not None
    assert draft.extraction["rejection_reason"] == "Wrong scheme text."


@pytest.mark.usefixtures("scheme")
async def test_review_loop_edit_then_approve(db_session: AsyncSession) -> None:
    draft = await add_rule(db_session, 1, BAD)
    out = io.StringIO()
    answers = scripted("a", "e", "a")  # approve fails (issue), edit fixes it, approve works

    def edit(text: str) -> str:
        document = json.loads(text)
        document["logic"] = GOOD
        return json.dumps(document)

    result = await review_rule(
        db_session,
        "s1",
        reviewer="r",
        console=Console(file=out, width=160),
        ask=lambda _prompt: next(answers),
        edit=edit,
    )

    shown = out.getvalue()
    assert result == "approved"
    assert draft.status is RuleStatus.APPROVED
    assert "Citizens aged 18 or more." in shown  # source text beside the rule
    assert 'age >= "adult"' in shown
    assert "fix 1 issue" in shown
    assert current_issues(draft) == []


@pytest.mark.usefixtures("scheme")
async def test_review_loop_reject_and_quit(db_session: AsyncSession) -> None:
    draft = await add_rule(db_session, 1, GOOD)
    console = Console(file=io.StringIO())
    answers = scripted("x", "r", "duplicate")

    result = await review_rule(
        db_session, "s1", reviewer="r", console=console, ask=lambda _p: next(answers), edit=str
    )

    assert result == "rejected"
    assert draft.status is RuleStatus.REJECTED
    assert (
        await review_rule(db_session, "s1", reviewer="r", console=console, ask=str, edit=str)
        == "no draft"
    )


async def test_review_unknown_scheme(db_session: AsyncSession) -> None:
    with pytest.raises(ReviewError, match="unknown scheme"):
        await review_rule(
            db_session, "nope", reviewer="r", console=Console(file=io.StringIO()), ask=str, edit=str
        )


@pytest.mark.usefixtures("scheme")
async def test_rules_table_summarises_live_and_draft(db_session: AsyncSession) -> None:
    await add_rule(db_session, 1, GOOD, RuleStatus.APPROVED)
    await add_rule(db_session, 2, BAD)
    out = io.StringIO()

    Console(file=out, width=120).print(await rules_table(db_session))

    row = next(line for line in out.getvalue().splitlines() if "s1" in line)
    assert "provisional" in row
    assert "v1" in row
    assert "v2" in row
    assert "0.70" in row
