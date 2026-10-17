from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from sahayak.db.models import EligibilityRule, RuleStatus, Scheme
from sahayak.eligibility.rules import RuleValidationError
from sahayak.eligibility.store import load_approved_rules, load_latest_rules
from sahayak.ingestion.index import upsert_scheme
from sahayak.ingestion.manifest import SchemeSpec

URL = "https://example.gov.in/scheme"
ADULT: dict[str, Any] = {"all": [{"field": "age", "op": ">=", "value": 18}]}
SENIOR: dict[str, Any] = {"all": [{"field": "age", "op": ">=", "value": 60}]}
UNKNOWN_FIELD: dict[str, Any] = {"all": [{"field": "caste", "op": "==", "value": "x"}]}
NOT_A_TREE: dict[str, Any] = {"every": []}


async def add_scheme(session: AsyncSession, scheme_id: str) -> Scheme:
    spec = SchemeSpec.model_validate(
        {"id": scheme_id, "names": {"en": scheme_id}, "level": "central", "sources": [{"url": URL}]}
    )
    return await upsert_scheme(session, spec, verified_at=datetime.now(UTC), content_hash=None)


def make_rule(
    scheme_id: str, version: int, logic: dict[str, Any], status: RuleStatus
) -> EligibilityRule:
    return EligibilityRule(
        scheme_id=scheme_id,
        version=version,
        status=status,
        logic=logic,
        source_refs=[{"url": URL, "section": "Eligibility"}],
        reviewed_by="Reviewer" if status is RuleStatus.APPROVED else None,
    )


async def test_invalid_rules_cannot_be_saved_as_approved(db_session: AsyncSession) -> None:
    await add_scheme(db_session, "s1")
    db_session.add(make_rule("s1", 1, UNKNOWN_FIELD, RuleStatus.APPROVED))

    with pytest.raises(RuleValidationError, match="unknown field 'caste'"):
        await db_session.flush()


async def test_drafts_with_issues_are_kept_for_review_but_cannot_be_approved(
    db_session: AsyncSession,
) -> None:
    await add_scheme(db_session, "s1")
    draft = make_rule("s1", 1, UNKNOWN_FIELD, RuleStatus.DRAFT)
    db_session.add(draft)
    await db_session.flush()  # a draft may hold issues

    draft.status = RuleStatus.APPROVED
    with pytest.raises(RuleValidationError):
        await db_session.flush()


async def test_load_approved_rules_validates_each_live_rule(db_session: AsyncSession) -> None:
    for scheme_id in ("a", "b", "c"):
        await add_scheme(db_session, scheme_id)
    db_session.add_all(
        [
            make_rule("a", 1, ADULT, RuleStatus.ARCHIVED),
            make_rule("a", 2, SENIOR, RuleStatus.APPROVED),
            make_rule("a", 3, ADULT, RuleStatus.DRAFT),
            make_rule("b", 1, ADULT, RuleStatus.DRAFT),
            make_rule("c", 1, ADULT, RuleStatus.REJECTED),
        ]
    )
    await db_session.flush()

    rules = await load_approved_rules(db_session)

    assert list(rules) == ["a"]
    assert (rules["a"].version, rules["a"].status, rules["a"].reviewed_by) == (
        2,
        "approved",
        "Reviewer",
    )
    assert rules["a"].logic.model_dump(by_alias=True)["all"][0]["value"] == 60


async def test_load_latest_rules_previews_drafts(db_session: AsyncSession) -> None:
    for scheme_id in ("a", "b", "c", "d"):
        await add_scheme(db_session, scheme_id)
    db_session.add_all(
        [
            make_rule("a", 1, SENIOR, RuleStatus.APPROVED),
            make_rule("a", 2, ADULT, RuleStatus.DRAFT),
            make_rule("b", 1, UNKNOWN_FIELD, RuleStatus.DRAFT),  # issues: still previewed
            make_rule("c", 1, NOT_A_TREE, RuleStatus.DRAFT),  # can't be evaluated: left out
            make_rule("d", 1, ADULT, RuleStatus.REJECTED),
        ]
    )
    await db_session.flush()

    rules = await load_latest_rules(db_session)

    assert {k: (r.version, r.status) for k, r in rules.items()} == {
        "a": (2, "draft"),
        "b": (1, "draft"),
    }
