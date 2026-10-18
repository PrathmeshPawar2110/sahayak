"""Running agent turns: what the API, and later the voice and WhatsApp channels, call.

`run_turn` masks identifiers out of the user's message first (TRD §10), so the graph, the LLM,
the checkpoint and the message log only ever see the masked text. A turn that fails on the LLM
or the embedder leaves the conversation state untouched (`durability="exit"`) and returns a
polite error reply (NFR Reliability).
"""

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, cast

import structlog
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sahayak.agent.catalogue import Catalogue, DbCatalogueSource
from sahayak.agent.checkpoint import postgres_checkpointer
from sahayak.agent.deps import AgentConfig, AgentDeps
from sahayak.agent.graph import AgentGraph, build_graph
from sahayak.agent.passages import RetrieverPassageSearch
from sahayak.agent.questions import SENSITIVE_FIELDS
from sahayak.agent.replies import message
from sahayak.agent.state import (
    AgentReply,
    AgentState,
    ConsentPurpose,
    DisplayItem,
    DisplayResults,
    Intent,
)
from sahayak.config import Settings
from sahayak.eligibility.profile import Language, UserProfile
from sahayak.llm.base import LLMError
from sahayak.llm.factory import LLMConfigError, create_llm_provider
from sahayak.privacy.pii import mask_pii
from sahayak.rag.embeddings import EmbeddingError, create_embedding_provider
from sahayak.rag.retriever import HybridRetriever

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class ShownScheme:
    """A displayed scheme with what a client needs to show it."""

    scheme_id: str
    name: str
    provisional: bool
    reasons: list[str]
    official_url: str | None
    last_verified_at: datetime | None


@dataclass(frozen=True)
class TurnResult:
    user_text: str  # the message as stored: identifiers masked
    reply: AgentReply
    language: Language
    intent: Intent | None  # None when the turn failed before it was understood
    profile: UserProfile | None
    ready: list[ShownScheme] = field(default_factory=list)
    action_needed: list[ShownScheme] = field(default_factory=list)
    provisional: list[ShownScheme] = field(default_factory=list)
    scheme_names: dict[str, str] = field(default_factory=dict)  # for citations
    pii_found: list[str] = field(default_factory=list)
    latency_ms: int = 0

    @property
    def deletes_session(self) -> bool:
        return self.intent == "delete_account"


def _shown(items: list[DisplayItem], catalogue: Catalogue, language: Language) -> list[ShownScheme]:
    shown = []
    for item in items:
        scheme = catalogue.get(item.scheme_id)
        if scheme is None:
            continue
        shown.append(
            ShownScheme(
                scheme_id=item.scheme_id,
                name=scheme.name(language),
                provisional=item.provisional,
                reasons=item.reasons,
                official_url=scheme.official_url,
                last_verified_at=scheme.last_verified_at,
            )
        )
    return shown


class AgentUnavailableError(RuntimeError):
    """No LLM is configured, so turns can't run (deleting state still works)."""


class AgentService:
    def __init__(self, graph: AgentGraph, deps: AgentDeps | None) -> None:
        self._graph = graph
        self._deps = deps

    @property
    def available(self) -> bool:
        return self._deps is not None

    @staticmethod
    def _config(session_id: str) -> RunnableConfig:
        return {"configurable": {"thread_id": session_id}}

    async def run_turn(
        self,
        session_id: str,
        text: str,
        *,
        language: Language,
        consents: list[ConsentPurpose],
    ) -> TurnResult:
        deps = self._deps
        if deps is None:
            raise AgentUnavailableError("no LLM is configured")
        started = time.perf_counter()
        masked = mask_pii(text)
        pii = [kind.value for kind in masked.kinds]
        turn: AgentState = {
            "session_id": session_id,
            "messages": [HumanMessage(masked.text)],
            "language": language,
            "consents": consents,
            "pii_found": pii,
        }
        try:
            state = cast(
                AgentState,
                await self._graph.ainvoke(
                    turn, self._config(session_id), context=deps, durability="exit"
                ),
            )
        except (LLMError, EmbeddingError) as exc:
            log.warning("agent_turn_failed", error=type(exc).__name__)
            return TurnResult(
                user_text=masked.text,
                reply=AgentReply(text=message("error", language), kind="error"),
                language=language,
                intent=None,
                profile=None,
                pii_found=pii,
                latency_ms=round((time.perf_counter() - started) * 1000),
            )
        reply = state.get("reply")
        if reply is None:  # every path through the graph ends in a reply
            raise RuntimeError("the agent finished a turn without a reply")
        reply_language = state.get("language", language)
        catalogue = await deps.catalogue.load()
        display = state.get("display_results", DisplayResults())
        return TurnResult(
            user_text=masked.text,
            reply=reply,
            language=reply_language,
            intent=state.get("intent"),
            profile=state.get("profile"),
            ready=_shown(display.ready, catalogue, reply_language),
            action_needed=_shown(display.action_needed, catalogue, reply_language),
            provisional=_shown(display.provisional, catalogue, reply_language),
            scheme_names={
                c.scheme_id: s.name(reply_language)
                for c in reply.citations
                if (s := catalogue.get(c.scheme_id)) is not None
            },
            pii_found=pii,
            latency_ms=round((time.perf_counter() - started) * 1000),
        )

    async def forget(self, session_id: str) -> None:
        """Delete the session's conversation state (all its checkpoints)."""
        checkpointer = self._graph.checkpointer
        if isinstance(checkpointer, BaseCheckpointSaver):
            await checkpointer.adelete_thread(session_id)

    async def forget_sensitive(self, session_id: str) -> None:
        """Remove sensitive fields from the saved profile (sensitive consent withdrawn)."""
        snapshot = await self._graph.aget_state(self._config(session_id))
        profile = cast(dict[str, Any], snapshot.values).get("profile")
        if not isinstance(profile, UserProfile):
            return
        stripped = UserProfile.model_validate(
            {
                **profile.model_dump(exclude={"field_sources"}),
                **dict.fromkeys(SENSITIVE_FIELDS),
                "field_sources": {
                    k: v for k, v in profile.field_sources.items() if k not in SENSITIVE_FIELDS
                },
            }
        )
        # As if `handle_reset` had written it: its only edge is to END, so nothing is left to run.
        await self._graph.aupdate_state(
            self._config(session_id), {"profile": stripped}, as_node="handle_reset"
        )


def agent_deps(
    settings: Settings, sessions: async_sessionmaker[AsyncSession], redis: Any
) -> AgentDeps | None:
    """The agent's dependencies from settings, or None when no LLM is configured."""
    try:
        llm = create_llm_provider(settings)
    except LLMConfigError as exc:
        log.warning("agent_disabled", reason=str(exc))
        return None
    retriever = HybridRetriever(create_embedding_provider(settings, redis))
    return AgentDeps(
        llm=llm,
        catalogue=DbCatalogueSource(sessions, show_provisional=settings.show_provisional_schemes),
        passages=RetrieverPassageSearch(sessions, retriever),
        config=AgentConfig(
            max_questions=settings.max_questions_per_session,
            show_provisional=settings.show_provisional_schemes,
        ),
    )


@asynccontextmanager
async def agent_service(
    settings: Settings, sessions: async_sessionmaker[AsyncSession], redis: Any
) -> AsyncIterator[AgentService]:
    """The app's agent on the Postgres checkpointer. Without an LLM it can still delete state."""
    async with postgres_checkpointer(settings.database_url) as checkpointer:
        yield AgentService(build_graph(checkpointer), agent_deps(settings, sessions, redis))
