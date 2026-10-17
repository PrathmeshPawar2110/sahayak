"""Draft eligibility rules from scheme text with the LLM (TRD §8 step 6, §6).

The LLM only drafts: every rule is saved as `draft` and a human approves it (`sahayak rules
review`) before the rule engine uses it. The draft keeps the exact excerpts it was extracted from,
so the reviewer sees the same text the model saw.

Structured output from the LLM follows `RuleExtraction`, a fixed-depth form of the DSL (items or
one level of groups): Anthropic's structured outputs do not accept recursive schemas. It is turned
into the TRD's JSON rule tree (`sahayak.eligibility.rules`) before saving.
"""

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from sahayak.db.models import EligibilityRule, RuleStatus
from sahayak.eligibility.fields import PROFILE_FIELDS, ProfileFieldName
from sahayak.eligibility.rules import Operator, dump_logic, parse_logic, validate_logic
from sahayak.ingestion.manifest import SchemeSpec
from sahayak.ingestion.parse import Section
from sahayak.llm.base import ChatMessage, LLMProvider
from sahayak.llm.prompts import load_prompt

PROMPT_NAME = "rule_extraction"
# About 15-20k tokens of source text: enough for every M1 scheme's eligibility sections.
MAX_SOURCE_CHARS = 60_000
# Headings or text that usually carry eligibility criteria (English, Hindi, Marathi).
ELIGIBILITY_RE = re.compile(
    r"eligib|who can|criteri|exclu|beneficiar|coverage|entitle|not be covered|"
    r"पात्र|अपात्र|लाभार्थी|निकष|कौन",
    re.IGNORECASE,
)


# ------------------------------------------------------------------ LLM output schema
# No defaults and no numeric constraints: both providers' strict modes require every field and
# reject some JSON Schema keywords. Values are checked after parsing instead.


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExtractedCondition(_Strict):
    field: ProfileFieldName
    op: Operator
    value: bool | int | float | str | list[int | float | str] | None = Field(
        description="null only for `exists`; a list for `in` / `not_in`"
    )


class ExtractedGroup(_Strict):
    negate: bool = Field(description="true: the group must NOT hold")
    combinator: Literal["all", "any"]
    conditions: list[ExtractedCondition]


class ExtractedSourceRef(_Strict):
    url: str
    section: str


class RuleExtraction(_Strict):
    combinator: Literal["all", "any"]
    items: list[ExtractedCondition | ExtractedGroup]
    source_refs: list[ExtractedSourceRef]
    unmapped_criteria: list[str] = Field(
        description="Criteria the fields and operators cannot express, in plain English"
    )
    notes: str
    confidence: float = Field(description="0 to 1")


# ------------------------------------------------------------------ source text


@dataclass(frozen=True)
class SourceText:
    """The parsed sections of one fetched source."""

    url: str
    sections: Sequence[Section]


@dataclass(frozen=True)
class Excerpt:
    url: str
    section: str
    text: str


def select_excerpts(
    sources: Sequence[SourceText], max_chars: int = MAX_SOURCE_CHARS
) -> tuple[list[Excerpt], bool]:
    """The text to extract from, and whether some of it was left out.

    Everything is sent when it fits. Otherwise only sections whose heading or text looks like
    eligibility criteria are kept, in document order, up to `max_chars`.
    """
    excerpts = [
        Excerpt(source.url, section.heading, section.text)
        for source in sources
        for section in source.sections
        if section.text.strip()
    ]
    if sum(len(e.text) for e in excerpts) <= max_chars:
        return excerpts, False
    relevant = [
        e for e in excerpts if ELIGIBILITY_RE.search(e.section) or ELIGIBILITY_RE.search(e.text)
    ]
    chosen: list[Excerpt] = []
    size = 0
    for excerpt in relevant:
        if size + len(excerpt.text) > max_chars:
            remaining = max_chars - size
            if remaining > 500:  # a useful piece of the section still fits
                chosen.append(Excerpt(excerpt.url, excerpt.section, excerpt.text[:remaining]))
            break
        chosen.append(excerpt)
        size += len(excerpt.text)
    return chosen, True


def excerpts_hash(excerpts: Sequence[Excerpt]) -> str:
    payload = json.dumps([asdict(e) for e in excerpts], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ conversion


def to_logic(extraction: RuleExtraction) -> dict[str, object]:
    """The extraction as a TRD §9.2 rule tree (validated for shape)."""
    items: list[dict[str, object]] = []
    for item in extraction.items:
        if isinstance(item, ExtractedCondition):
            items.append(_condition(item))
            continue
        group: dict[str, object] = {item.combinator: [_condition(c) for c in item.conditions]}
        items.append({"not": group} if item.negate else group)
    return dump_logic(parse_logic({extraction.combinator: items}))


def _condition(condition: ExtractedCondition) -> dict[str, object]:
    data: dict[str, object] = {"field": condition.field, "op": condition.op}
    if condition.value is not None:
        data["value"] = condition.value
    return data


def review_issues(
    logic: Mapping[str, object],
    source_refs: Sequence[Mapping[str, str]],
    excerpts: Sequence[Excerpt],
) -> list[str]:
    """Problems a reviewer must fix before approving. Empty means the rule is well-formed."""
    issues = validate_logic(parse_logic(logic))
    if not excerpts:  # a rule written by hand has nothing to check its refs against
        return issues
    known = {(e.url, e.section) for e in excerpts}
    issues.extend(
        f"source ref not among the excerpts: {ref['url']} / {ref['section']}"
        for ref in source_refs
        if (ref["url"], ref["section"]) not in known
    )
    return issues


# ------------------------------------------------------------------ drafting


@dataclass(frozen=True)
class DraftOutcome:
    status: Literal["created", "unchanged", "no_text"]
    rule: EligibilityRule | None = None


async def draft_rule(
    session: AsyncSession,
    llm: LLMProvider,
    spec: SchemeSpec,
    sources: Sequence[SourceText],
    *,
    force: bool = False,
) -> DraftOutcome:
    """Extract and save a new draft rule version, unless this exact text was already drafted.

    A new draft archives older unreviewed drafts of the scheme. The approved rule, if any, stays
    live until the new draft is approved (TRD §8A). Does not commit.
    """
    excerpts, truncated = select_excerpts(sources)
    if not excerpts:
        return DraftOutcome("no_text")
    source_hash = excerpts_hash(excerpts)
    if not force:
        existing = await session.scalar(
            select(EligibilityRule)
            .where(EligibilityRule.scheme_id == spec.id, EligibilityRule.source_hash == source_hash)
            .order_by(EligibilityRule.version.desc())
            .limit(1)
        )
        if existing is not None:
            return DraftOutcome("unchanged", existing)

    prompt = load_prompt(PROMPT_NAME).render(
        scheme=spec, fields=PROFILE_FIELDS.values(), excerpts=excerpts, truncated=truncated
    )
    response = await llm.structured(
        [ChatMessage("user", prompt.user)], RuleExtraction, system=prompt.system
    )
    extraction = response.value
    logic = to_logic(extraction)
    source_refs = [ref.model_dump() for ref in extraction.source_refs]

    latest = await session.scalar(
        select(func.max(EligibilityRule.version)).where(EligibilityRule.scheme_id == spec.id)
    )
    await session.execute(
        update(EligibilityRule)
        .where(EligibilityRule.scheme_id == spec.id, EligibilityRule.status == RuleStatus.DRAFT)
        .values(status=RuleStatus.ARCHIVED)
    )
    rule = EligibilityRule(
        scheme_id=spec.id,
        version=(latest or 0) + 1,
        status=RuleStatus.DRAFT,
        logic=logic,
        source_refs=source_refs,
        notes=extraction.notes or None,
        source_hash=source_hash,
        extraction={
            "provider": llm.name,
            "model": response.model,
            "prompt": prompt.id,
            "extracted_at": datetime.now(UTC).isoformat(),
            "confidence": extraction.confidence,
            "unmapped_criteria": extraction.unmapped_criteria,
            "issues": review_issues(logic, source_refs, excerpts),
            "truncated": truncated,
            "excerpts": [asdict(e) for e in excerpts],
        },
    )
    session.add(rule)
    await session.flush()
    return DraftOutcome("created", rule)
