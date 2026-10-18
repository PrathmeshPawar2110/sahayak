"""Shared helpers for agent tests: a small catalogue, passages and deps, with no database."""

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any

from sahayak.agent.catalogue import Catalogue, SchemeInfo, StaticCatalogueSource
from sahayak.agent.deps import AgentConfig, AgentDeps
from sahayak.agent.passages import StaticPassageSearch
from sahayak.agent.state import EvidenceChunk, TurnUnderstanding
from sahayak.db.models import SchemeLevel, SchemeStatus
from sahayak.eligibility.rules import SchemeRule
from sahayak.llm.base import LLMProvider
from sahayak.llm.fake import FakeCall, FakeLLMProvider, Reply

VERIFIED = datetime(2026, 10, 17, 9, 0, tzinfo=UTC)
NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)


def rule(scheme_id: str, logic: dict[str, Any], *, status: str = "approved") -> SchemeRule:
    return SchemeRule.model_validate(
        {"scheme_id": scheme_id, "version": 1, "status": status, "logic": logic}
    )


def scheme(
    scheme_id: str,
    name: str,
    logic: dict[str, Any] | None,
    *,
    status: SchemeStatus = SchemeStatus.ACTIVE,
    state: str | None = None,
    aliases: Sequence[str] = (),
    name_hi: str | None = None,
) -> SchemeInfo:
    return SchemeInfo(
        id=scheme_id,
        name_en=name,
        name_hi=name_hi,
        level=SchemeLevel.STATE if state else SchemeLevel.CENTRAL,
        state=state,
        status=status,
        aliases=tuple(aliases),
        benefits=f"Benefits of {name}.",
        official_url=f"https://example.gov.in/{scheme_id}",
        last_verified_at=VERIFIED,
        rule=None
        if logic is None
        else rule(
            scheme_id, logic, status="draft" if status is SchemeStatus.PROVISIONAL else "approved"
        ),
    )


# Three schemes that need different facts, one of them state-only and one provisional.
FARMER = scheme(
    "farm-aid",
    "Farm Aid Scheme (FAS)",
    {
        "all": [
            {"field": "occupation", "op": "==", "value": "farmer"},
            {"field": "land_holding_acres", "op": ">", "value": 0},
        ]
    },
    aliases=["Farm Aid", "FAS"],
    name_hi="किसान सहायता योजना",
)
PENSION = scheme(
    "old-pension",
    "Old Age Pension",
    {"all": [{"field": "age", "op": ">=", "value": 60}]},
    aliases=["OAP"],
)
STATE_GIRLS = scheme(
    "mh-girls",
    "Maharashtra Girls Scheme",
    {"all": [{"field": "gender", "op": "==", "value": "female"}]},
    state="MH",
)
NEW_HOUSING = scheme(
    "new-housing",
    "New Housing Mission",
    {"all": [{"field": "residence", "op": "==", "value": "rural"}]},
    status=SchemeStatus.PROVISIONAL,
)
CLOSED = scheme(
    "closed-scheme",
    "Closed Scheme",
    {"all": [{"field": "age", "op": ">=", "value": 0}]},
    status=SchemeStatus.DISCONTINUED,
)
CATALOGUE = Catalogue.of([FARMER, PENSION, STATE_GIRLS, NEW_HOUSING, CLOSED])


def chunk(scheme_id: str, section: str, text: str, number: int = 1) -> EvidenceChunk:
    return EvidenceChunk(
        chunk_id=f"00000000-0000-0000-0000-{number:012d}",
        scheme_id=scheme_id,
        source_url=f"https://example.gov.in/{scheme_id}.pdf",
        section=section,
        language="en",
        text=text,
        last_verified_at=VERIFIED,
    )


CHUNKS = [
    chunk("farm-aid", "Benefits", "Farmers with land get Rs 6000 a year income support.", 1),
    chunk("farm-aid", "Eligibility", "All landholding farmer families are eligible.", 2),
    chunk("old-pension", "Benefits", "Pension of Rs 3000 a month for people aged 60.", 3),
    chunk("mh-girls", "Benefits", "Support for girls in Maharashtra families.", 4),
    chunk("new-housing", "Benefits", "Money to build a house in rural areas.", 5),
]


def make_deps(
    llm: LLMProvider | Sequence[Reply] = (),
    *,
    catalogue: Catalogue = CATALOGUE,
    chunks: Sequence[EvidenceChunk] = CHUNKS,
    config: AgentConfig | None = None,
) -> AgentDeps:
    provider = llm if isinstance(llm, LLMProvider) else FakeLLMProvider(llm)
    return AgentDeps(
        llm=provider,
        catalogue=StaticCatalogueSource(catalogue),
        passages=StaticPassageSearch(chunks, catalogue),
        config=config or AgentConfig(),
        clock=lambda: NOW,
    )


def understood(
    intent: str = "discover",
    *,
    language: str = "en",
    facts: dict[str, str] | None = None,
    declined: Sequence[str] = (),
    schemes: Sequence[str] = (),
    query: str = "",
) -> dict[str, Any]:
    """A `TurnUnderstanding` reply for the fake LLM."""
    return {
        "intent": intent,
        "language": language,
        "facts": [{"field": k, "value": v} for k, v in (facts or {}).items()],
        "declined_fields": list(declined),
        "scheme_names": list(schemes),
        "search_query": query,
    }


Responder = Callable[[FakeCall], Reply]


def agent_llm(
    understandings: Sequence[dict[str, Any]] = (),
    *,
    others: dict[type, Responder] | None = None,
) -> FakeLLMProvider:
    """A fake LLM for the agent: turn-understanding replies in order, other schemas by responder.

    With no understanding left, every turn reads as a bare "discover".
    """
    queue = list(understandings)
    responders = others or {}

    def reply(call: FakeCall) -> Reply:
        if call.schema is TurnUnderstanding:
            return queue.pop(0) if queue else understood()
        if call.schema in responders:
            return responders[call.schema](call)
        raise AssertionError(f"unexpected LLM call for {call.schema}")

    return FakeLLMProvider(reply)
