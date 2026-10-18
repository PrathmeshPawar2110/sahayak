import uuid
from typing import cast

from langchain_core.messages import HumanMessage
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from sahayak.agent.checkpoint import CHECKPOINT_MIGRATIONS, postgres_checkpointer
from sahayak.agent.graph import build_graph
from sahayak.agent.state import AgentState
from tests.agent_support import agent_llm, make_deps


async def _turn(database_url: str, thread_id: str) -> AgentState:
    async with postgres_checkpointer(database_url) as saver:
        graph = build_graph(saver)
        state = await graph.ainvoke(
            {"session_id": thread_id, "messages": [HumanMessage("नमस्ते")]},
            {"configurable": {"thread_id": thread_id}},
            context=make_deps(agent_llm()),
            durability="exit",
        )
        return cast(AgentState, state)


async def test_alembic_created_the_checkpoint_tables(db_engine: AsyncEngine) -> None:
    async with db_engine.connect() as connection:
        result = await connection.execute(text("SELECT v FROM checkpoint_migrations ORDER BY v"))
        versions: list[int] = list(result.scalars())
    assert versions == list(range(CHECKPOINT_MIGRATIONS))


async def test_setup_finds_nothing_left_to_migrate(database_url: str) -> None:
    async with postgres_checkpointer(database_url) as saver:
        await saver.setup()  # would fail on CREATE INDEX CONCURRENTLY if it ran anything


async def test_state_survives_a_new_checkpointer(database_url: str) -> None:
    thread_id = str(uuid.uuid4())
    try:
        await _turn(database_url, thread_id)
        second = await _turn(database_url, thread_id)  # a fresh pool and saver
        assert second["turn_count"] == 2
        assert [m.content for m in second["messages"]] == ["नमस्ते", "नमस्ते"]
    finally:
        async with postgres_checkpointer(database_url) as saver:
            await saver.adelete_thread(thread_id)
            assert await saver.aget_tuple({"configurable": {"thread_id": thread_id}}) is None
