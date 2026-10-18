"""Checks on what a reply says about schemes (TRD §10 guardrails, §8C).

The LLM writes a reply as structured data: an opening, points, a closing. Each point is about at
most one scheme, cites the passages (`chunk_ids`) it relies on, and labels the eligibility it
states. `check_reply` lists every way a draft breaks the rules:

- a point about a scheme the reply may not discuss, or text naming one anywhere;
- a point without citations, or citing passages that weren't retrieved or that belong to a
  different scheme;
- a stated verdict that differs from the rule engine's.

Schemes a reply may discuss are the displayed ones (all ELIGIBLE) and the ones the user named,
with their true verdicts. The caveat for provisional schemes and the list of sources are added by
`render_reply`, never left to the LLM.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from sahayak.agent.catalogue import Catalogue
from sahayak.agent.replies import format_date, message
from sahayak.agent.state import Citation, EvidenceChunk
from sahayak.eligibility.engine import EligibilityResult, Verdict
from sahayak.eligibility.profile import Language

StatedVerdict = Literal["eligible", "not_eligible", "needs_info", "none"]
KnownVerdict = Literal["eligible", "not_eligible", "needs_info", "unknown"]

_VERDICTS: dict[Verdict, KnownVerdict] = {
    Verdict.ELIGIBLE: "eligible",
    Verdict.NOT_ELIGIBLE: "not_eligible",
    Verdict.NEEDS_INFO: "needs_info",
}
MAX_REPLY_CHARS = 2500


class ReplyPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scheme_id: str
    text: str
    chunk_ids: list[str]
    verdict: StatedVerdict


class ComposedReply(BaseModel):
    """The LLM's reply (prompt `response_composition`). No defaults: strict output modes."""

    model_config = ConfigDict(extra="forbid")

    opening: str
    points: list[ReplyPoint]
    closing: str


@dataclass(frozen=True)
class SchemeFacts:
    """What a reply may say about one scheme: the engine's verdict and why."""

    scheme_id: str
    name: str
    verdict: KnownVerdict
    displayed: bool
    provisional: bool
    official_url: str | None = None
    last_verified_at: datetime | None = None
    met: list[str] = field(default_factory=list)
    unmet: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


def scheme_facts(
    catalogue: Catalogue,
    language: Language,
    eligibility: Mapping[str, EligibilityResult],
    displayed: Iterable[str],
    named: Iterable[str],
) -> dict[str, SchemeFacts]:
    """The schemes a reply may discuss: displayed ones first, then the ones the user named."""
    shown = list(displayed)
    facts: dict[str, SchemeFacts] = {}
    for scheme_id in [*shown, *named]:
        scheme = catalogue.get(scheme_id)
        if scheme is None or scheme_id in facts:
            continue
        result = eligibility.get(scheme_id)
        facts[scheme_id] = SchemeFacts(
            scheme_id=scheme_id,
            name=scheme.name(language),
            verdict=_VERDICTS[result.verdict] if result else "unknown",
            displayed=scheme_id in shown,
            provisional=scheme.provisional,
            official_url=scheme.official_url,
            last_verified_at=scheme.last_verified_at,
            met=[check.text() for check in result.passed] if result else [],
            unmet=[check.text() for check in result.failed] if result else [],
            missing=list(result.missing_fields) if result else [],
        )
    return facts


def check_reply(
    draft: ComposedReply,
    *,
    allowed: Mapping[str, SchemeFacts],
    evidence: Mapping[str, EvidenceChunk],
    catalogue: Catalogue,
) -> list[str]:
    """Every rule the draft breaks, as sentences the LLM can act on when it tries again."""
    problems: list[str] = []
    texts = [draft.opening, draft.closing, *(point.text for point in draft.points)]
    if not draft.opening.strip() and not draft.points:
        problems.append("The reply is empty.")
    if sum(len(text) for text in texts) > MAX_REPLY_CHARS:
        problems.append(f"The reply is longer than {MAX_REPLY_CHARS} characters.")
    for number, point in enumerate(draft.points, start=1):
        where = f"Point {number}"
        facts = allowed.get(point.scheme_id) if point.scheme_id else None
        if point.scheme_id and facts is None:
            problems.append(f"{where} is about `{point.scheme_id}`, which you may not discuss.")
        if not point.chunk_ids:
            problems.append(f"{where} cites no passage.")
        for chunk_id in point.chunk_ids:
            chunk = evidence.get(chunk_id)
            if chunk is None:
                problems.append(f"{where} cites `{chunk_id}`, which is not a given passage.")
            elif point.scheme_id and chunk.scheme_id != point.scheme_id:
                problems.append(f"{where} cites a passage about a different scheme.")
        if point.verdict != "none":
            if facts is None:
                problems.append(f"{where} states eligibility without naming the scheme.")
            elif point.verdict != facts.verdict:
                problems.append(
                    f"{where} says `{point.verdict}` for `{point.scheme_id}`, but the verdict "
                    f"is `{facts.verdict}`."
                )
    for scheme_id in catalogue.mentions("\n".join(texts)):
        if scheme_id not in allowed:
            problems.append(f"The reply names `{scheme_id}`, which you may not mention.")
    return list(dict.fromkeys(problems))


def render_reply(
    draft: ComposedReply,
    *,
    allowed: Mapping[str, SchemeFacts],
    evidence: Mapping[str, EvidenceChunk],
    catalogue: Catalogue,
    language: Language,
) -> tuple[str, list[Citation]]:
    """The reply text with numbered citations, provisional caveats and a sources list."""
    numbers: dict[str, int] = {}
    lines = [draft.opening.strip()] if draft.opening.strip() else []
    bullets = []
    for point in draft.points:
        markers = ""
        for chunk_id in point.chunk_ids:
            numbers.setdefault(chunk_id, len(numbers) + 1)
            markers += f"[{numbers[chunk_id]}]"
        bullets.append(f"• {point.text.strip()} {markers}".rstrip())
    if bullets:
        lines.append("\n".join(bullets))
    if draft.closing.strip():
        lines.append(draft.closing.strip())

    discussed = [
        *(point.scheme_id for point in draft.points if point.scheme_id),
        *catalogue.mentions("\n".join([draft.opening, draft.closing])),
        *(facts.scheme_id for facts in allowed.values() if facts.displayed),
    ]
    lines += caveats(allowed, discussed, language)

    citations = [
        Citation(
            number=number,
            chunk_id=chunk_id,
            scheme_id=evidence[chunk_id].scheme_id,
            section=evidence[chunk_id].section,
            source_url=evidence[chunk_id].source_url,
            last_verified_at=evidence[chunk_id].last_verified_at,
        )
        for chunk_id, number in numbers.items()
    ]
    if citations:
        lines.append(sources(citations, catalogue, language))
    return "\n\n".join(lines), citations


def caveats(
    allowed: Mapping[str, SchemeFacts], discussed: Iterable[str], language: Language
) -> list[str]:
    """One "not yet verified" caveat per provisional scheme discussed (TRD §8A)."""
    lines = []
    for scheme_id in dict.fromkeys(discussed):
        facts = allowed.get(scheme_id)
        if facts is None or not facts.provisional:
            continue
        if facts.official_url:
            text = message("provisional_caveat", language, name=facts.name, url=facts.official_url)
        else:
            text = message("provisional_caveat_no_link", language, name=facts.name)
        lines.append(f"⚠️ {text}")
    return lines


def sources(citations: Iterable[Citation], catalogue: Catalogue, language: Language) -> str:
    lines = [f"{message('sources', language)}:"]
    for citation in citations:
        scheme = catalogue.get(citation.scheme_id)
        name = scheme.name(language) if scheme else citation.scheme_id
        verified = (
            f" ({message('verified_on', language, date=format_date(citation.last_verified_at))})"
            if citation.last_verified_at
            else ""
        )
        lines.append(
            f"[{citation.number}] {name}: {citation.section}, {citation.source_url}{verified}"
        )
    return "\n".join(lines)


def template_reply(allowed: Mapping[str, SchemeFacts], *, results: bool, language: Language) -> str:
    """The fixed reply used when no LLM reply passes the checks. It states only verdicts.

    `results` is true for a discover turn (list what is displayed); otherwise it gives the
    verdict for each scheme the user named.
    """

    def line(facts: SchemeFacts) -> str:
        verified = (
            f" ({message('verified_on', language, date=format_date(facts.last_verified_at))})"
            if facts.last_verified_at
            else ""
        )
        return f"• {facts.name}{verified}"

    lines: list[str] = []
    if results:
        ready = [f for f in allowed.values() if f.displayed and not f.provisional]
        provisional = [f for f in allowed.values() if f.displayed and f.provisional]
        if ready:
            lines.append("\n".join([message("results_intro", language), *map(line, ready)]))
        if provisional:
            lines.append(
                "\n".join([message("provisional_intro", language), *map(line, provisional)])
            )
        if not ready and not provisional:
            lines.append(message("no_results", language))
    named = [f for f in allowed.values() if not (results and f.displayed)]
    for facts in named:
        if facts.verdict != "unknown":
            lines.append(message(f"verdict_{facts.verdict}", language, name=facts.name))
        if facts.official_url:
            lines.append(message("read_more", language, url=facts.official_url))
    if not lines:
        lines.append(message("smalltalk", language))
    discussed = [f.scheme_id for f in allowed.values() if f.displayed or not results]
    lines += caveats(allowed, discussed, language)
    return "\n\n".join(lines)
