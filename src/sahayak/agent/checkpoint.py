"""Durable conversation state: LangGraph's Postgres checkpointer (TRD §7), one thread per session.

The checkpointer talks to Postgres through psycopg (the rest of the app uses asyncpg), so it has
its own small connection pool. Its tables are created by Alembic (revision 0003), not by
`AsyncPostgresSaver.setup()`, so the API never changes the schema on start (D-006).

Checkpoints are deserialised with an explicit allowlist: by default LangGraph would import and
call any class named in a stored checkpoint.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel
from sqlalchemy import make_url

from sahayak.agent.state import (
    AgentReply,
    Contradiction,
    DisplayResults,
    DocumentSummary,
    EvidenceChunk,
    PendingQuestion,
    TurnUnderstanding,
)
from sahayak.eligibility.engine import EligibilityResult
from sahayak.eligibility.profile import UserProfile

# Every Pydantic model stored directly as an `AgentState` value (or as an item of one). Nested
# models are saved as plain dicts by `model_dump()` and rebuilt by their parent's validation.
STATE_TYPES: tuple[type[BaseModel], ...] = (
    AgentReply,
    Contradiction,
    DisplayResults,
    DocumentSummary,
    EligibilityResult,
    EvidenceChunk,
    PendingQuestion,
    TurnUnderstanding,
    UserProfile,
)

# Number of `AsyncPostgresSaver.MIGRATIONS` that Alembic revision 0003 applies. A test fails when
# a library upgrade adds migrations, so that a new Alembic revision applies them.
CHECKPOINT_MIGRATIONS = 10


def checkpoint_serializer() -> JsonPlusSerializer:
    return JsonPlusSerializer(
        allowed_msgpack_modules=[(cls.__module__, cls.__name__) for cls in STATE_TYPES]
    )


def psycopg_conninfo(database_url: str) -> str:
    """The app's SQLAlchemy URL (`postgresql+asyncpg://...`) as a libpq connection string."""
    url = make_url(database_url).set(drivername="postgresql")
    return url.render_as_string(hide_password=False)


@asynccontextmanager
async def postgres_checkpointer(
    database_url: str, *, max_connections: int = 5
) -> AsyncIterator[AsyncPostgresSaver]:
    """A checkpointer on its own psycopg pool, closed on exit."""
    async with AsyncConnectionPool(
        psycopg_conninfo(database_url),
        min_size=0,  # connect on first use, not at startup
        max_size=max_connections,
        # What AsyncPostgresSaver expects: autocommit, dict rows, no server-side prepares
        # (they break behind transaction-mode poolers such as PgBouncer).
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
        open=False,
    ) as pool:
        yield AsyncPostgresSaver(pool, serde=checkpoint_serializer())  # type: ignore[arg-type]
