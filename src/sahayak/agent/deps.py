"""What the agent's nodes need besides the state, given to each run as LangGraph's runtime context.

Nothing here is saved in checkpoints. Nodes reach the database only through `CatalogueSource`
and `PassageSearch`, so the graph runs in tests without Postgres.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sahayak.agent.catalogue import CatalogueSource
from sahayak.agent.passages import PassageSearch
from sahayak.llm.base import LLMProvider


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class AgentConfig:
    max_questions: int = 8  # MAX_QUESTIONS_PER_SESSION (FR-5)
    show_provisional: bool = True  # SHOW_PROVISIONAL_SCHEMES (TRD §8C)
    history_messages: int = 6  # recent messages the LLM sees for context
    shortlist_passages: int = 20  # passages searched to shortlist schemes by need
    evidence_schemes: int = 5  # schemes a reply cites evidence for
    evidence_per_scheme: int = 2  # passages per scheme


@dataclass(frozen=True)
class AgentDeps:
    llm: LLMProvider
    catalogue: CatalogueSource
    passages: PassageSearch
    config: AgentConfig = field(default_factory=AgentConfig)
    clock: Callable[[], datetime] = _now
