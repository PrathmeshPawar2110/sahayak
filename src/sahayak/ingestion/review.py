"""Human review of draft rules (TRD §8 step 7). Only approved rules reach the rule engine.

Approving a rule archives the previously approved version and makes a `provisional` scheme
`active` (TRD §8A lifecycle). Nothing here commits.
"""

import json
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from sahayak.db.models import EligibilityRule, RuleStatus, Scheme, SchemeStatus
from sahayak.eligibility.rules import SourceRef, dump_logic, parse_logic
from sahayak.ingestion.rule_extraction import Excerpt, review_issues


class ReviewError(Exception):
    """The requested review action is not allowed, or an edit is not a valid rule."""


async def latest_draft(session: AsyncSession, scheme_id: str) -> EligibilityRule | None:
    rule: EligibilityRule | None = await session.scalar(
        select(EligibilityRule)
        .where(EligibilityRule.scheme_id == scheme_id, EligibilityRule.status == RuleStatus.DRAFT)
        .order_by(EligibilityRule.version.desc())
        .limit(1)
    )
    return rule


async def approved_rule(session: AsyncSession, scheme_id: str) -> EligibilityRule | None:
    rule: EligibilityRule | None = await session.scalar(
        select(EligibilityRule).where(
            EligibilityRule.scheme_id == scheme_id, EligibilityRule.status == RuleStatus.APPROVED
        )
    )
    return rule


def excerpts_of(rule: EligibilityRule) -> list[Excerpt]:
    return [Excerpt(**excerpt) for excerpt in (rule.extraction or {}).get("excerpts", [])]


def current_issues(rule: EligibilityRule) -> list[str]:
    try:
        return review_issues(rule.logic, rule.source_refs, excerpts_of(rule))
    except ValidationError as exc:
        return [f"logic is not a valid rule tree: {exc.error_count()} error(s)"]


def editable_json(rule: EligibilityRule) -> str:
    """The parts of a rule a reviewer may edit, as pretty JSON."""
    document = {"logic": rule.logic, "source_refs": rule.source_refs, "notes": rule.notes}
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def apply_edit(rule: EligibilityRule, text: str) -> list[str]:
    """Replace the rule's logic, source refs and notes from edited JSON. Returns the new issues.

    Raises ReviewError (and leaves the rule unchanged) if the text is not a rule document.
    """
    _require_draft(rule)
    try:
        document = json.loads(text)
        if not isinstance(document, dict) or set(document) - {"logic", "source_refs", "notes"}:
            raise ReviewError("expected an object with only logic, source_refs and notes")
        logic = dump_logic(parse_logic(document["logic"]))
        refs = [SourceRef.model_validate(ref).model_dump() for ref in document["source_refs"]]
        notes = document.get("notes")
        if notes is not None and not isinstance(notes, str):
            raise ReviewError("notes must be a string or null")
    except (json.JSONDecodeError, KeyError, TypeError, ValidationError) as exc:
        raise ReviewError(f"not a valid rule document: {exc}") from exc

    rule.logic, rule.source_refs, rule.notes = logic, refs, notes or None
    rule.review_edited = True
    issues = current_issues(rule)
    rule.extraction = {**(rule.extraction or {}), "issues": issues}  # new dict: JSONB change
    return issues


async def approve_rule(session: AsyncSession, rule: EligibilityRule, *, reviewer: str) -> None:
    _require_draft(rule)
    issues = current_issues(rule)
    if issues:
        raise ReviewError(f"fix {len(issues)} issue(s) before approving: {'; '.join(issues)}")
    # Archive the live version first: at most one approved rule per scheme (partial unique index).
    await session.execute(
        update(EligibilityRule)
        .where(
            EligibilityRule.scheme_id == rule.scheme_id,
            EligibilityRule.status == RuleStatus.APPROVED,
        )
        .values(status=RuleStatus.ARCHIVED)
    )
    _mark_reviewed(rule, RuleStatus.APPROVED, reviewer)
    await session.execute(
        update(Scheme)
        .where(Scheme.id == rule.scheme_id, Scheme.status == SchemeStatus.PROVISIONAL)
        .values(status=SchemeStatus.ACTIVE)
    )
    await session.flush()


async def reject_rule(
    session: AsyncSession, rule: EligibilityRule, *, reviewer: str, reason: str
) -> None:
    _require_draft(rule)
    _mark_reviewed(rule, RuleStatus.REJECTED, reviewer)
    if reason.strip():
        extraction: dict[str, Any] = rule.extraction or {}
        rule.extraction = {**extraction, "rejection_reason": reason.strip()}
    await session.flush()


def _require_draft(rule: EligibilityRule) -> None:
    if rule.status is not RuleStatus.DRAFT:
        raise ReviewError(f"rule v{rule.version} is {rule.status.value}, not a draft")


def _mark_reviewed(rule: EligibilityRule, status: RuleStatus, reviewer: str) -> None:
    rule.status = status
    rule.reviewed_by = reviewer
    rule.reviewed_at = datetime.now(UTC)
