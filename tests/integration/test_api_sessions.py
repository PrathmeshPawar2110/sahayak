"""The session API end to end on real Postgres, with the agent on fakes (no LLM, no index)."""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from langgraph.checkpoint.memory import InMemorySaver
from redis.asyncio import Redis
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from sahayak.agent.checkpoint import checkpoint_serializer
from sahayak.agent.graph import build_graph
from sahayak.agent.service import AgentService
from sahayak.api.app import create_app
from sahayak.config import Settings
from sahayak.db.models import Message, Profile, Session
from sahayak.llm.base import LLMTransientError
from tests.agent_support import AgentFakeLLM, agent_llm, make_deps, understood

Client = httpx.AsyncClient


def _app(settings: Settings, llm: AgentFakeLLM | None) -> FastAPI:
    saver = InMemorySaver(serde=checkpoint_serializer())

    @asynccontextmanager
    async def agent(
        _settings: Settings, _sessions: async_sessionmaker[AsyncSession], _redis: Redis
    ) -> AsyncIterator[AgentService]:
        yield AgentService(build_graph(saver), make_deps(llm) if llm else None)

    app = create_app(settings, agent=agent)
    app.state.saver = saver
    return app


@pytest.fixture
def llm() -> AgentFakeLLM:
    return agent_llm()


@asynccontextmanager
async def _client(app: FastAPI) -> AsyncIterator[Client]:
    async with LifespanManager(app) as manager:
        transport = httpx.ASGITransport(app=manager.app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


@pytest.fixture
async def api(
    database_url: str, settings: Settings, db_engine: AsyncEngine, llm: AgentFakeLLM
) -> AsyncIterator[Client]:
    started = datetime.now(UTC)
    app = _app(settings, llm)
    async with _client(app) as client:
        client.app = app  # type: ignore[attr-defined]
        yield client
    async with db_engine.begin() as connection:  # the API commits for real: clean up
        await connection.execute(delete(Session).where(Session.created_at >= started))


async def _session(api: Client, *, consent: bool = True, **body: Any) -> str:
    response = await api.post("/v1/sessions", json=body)
    assert response.status_code == 201, response.text
    session_id: str = response.json()["session_id"]
    if consent:
        granted = await api.post(
            f"/v1/sessions/{session_id}/consent",
            json={"purposes": ["profile"], "policy_version": "2026-10"},
        )
        assert granted.status_code == 200, granted.text
    return session_id


async def _say(api: Client, session_id: str, text: str) -> dict[str, Any]:
    response = await api.post(f"/v1/sessions/{session_id}/messages", json={"text": text})
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _script(llm: AgentFakeLLM, *replies: Any) -> None:
    """Queue turn understandings (or errors) on the fake LLM."""
    llm.queue.extend(replies)


async def _rows(engine: AsyncEngine, model: Any, session_id: str) -> list[Any]:
    async with AsyncSession(engine) as db:
        return list((await db.scalars(select(model).where(model.session_id == session_id))).all())


async def test_create_session(api: Client) -> None:
    response = await api.post("/v1/sessions", json={"channel": "web", "language": "mr"})
    assert response.status_code == 201
    body = response.json()
    assert body["language"] == "mr"
    assert body["consents"] == {}
    expires = datetime.fromisoformat(body["expires_at"])
    assert timedelta(days=29) < expires - datetime.now(UTC) <= timedelta(days=30)


async def test_messages_need_consent(api: Client) -> None:
    session_id = await _session(api, consent=False)
    response = await api.post(f"/v1/sessions/{session_id}/messages", json={"text": "hi"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "consent_required"


async def test_a_conversation_through_the_api(
    api: Client, llm: AgentFakeLLM, db_engine: AsyncEngine
) -> None:
    _script(
        llm,
        understood(facts={"age": "65", "occupation": "farmer", "state": "UP"}, language="hi"),
        understood(facts={"land_holding_acres": "2"}, language="hi"),
        understood(facts={"residence": "rural"}, language="hi"),
    )
    session_id = await _session(api, language="hi")
    first = await _say(api, session_id, "मैं 65 साल का किसान हूँ, यूपी से")
    assert (first["kind"], first["question_field"]) == ("question", "land_holding_acres")
    # While asking, the strict list already holds what is settled (age 65 settles the pension).
    assert [s["scheme_id"] for s in first["eligibility"]["ready"]] == ["old-pension"]
    await _say(api, session_id, "2 एकड़")
    last = await _say(api, session_id, "गाँव में")

    assert last["kind"] == "answer"
    assert last["language"] == "hi"
    ready = last["eligibility"]["ready"]
    assert [s["scheme_id"] for s in ready] == ["farm-aid", "old-pension"]
    assert ready[0]["name"] == "किसान सहायता योजना"
    assert ready[0]["last_verified_at"].startswith("2026-10-17")
    assert [s["scheme_id"] for s in last["eligibility"]["provisional"]] == ["new-housing"]

    profile = (await api.get(f"/v1/sessions/{session_id}/profile")).json()
    assert profile["profile"] == {
        "state": "UP",
        "age": 65,
        "occupation": "farmer",
        "land_holding_acres": 2.0,
        "residence": "rural",
    }
    assert profile["field_sources"]["age"]["source"] == "self_declared"
    messages = await _rows(db_engine, Message, session_id)
    assert len(messages) == 6
    assert {m.lang.value for m in messages} == {"hi"}


async def test_identifiers_are_masked_before_anything_sees_them(
    api: Client, llm: AgentFakeLLM, db_engine: AsyncEngine
) -> None:
    _script(llm, understood("smalltalk"))
    session_id = await _session(api)
    body = await _say(api, session_id, "my aadhaar is 2345 6789 0123")
    assert body["reply_text"].startswith("Please don't share Aadhaar")
    assert "2345 6789" not in llm.calls[-1].messages[0].content
    stored = [m.text for m in await _rows(db_engine, Message, session_id)]
    assert stored[0] == "my aadhaar is XXXX-XXXX-0123"


async def test_withdrawing_profile_consent_erases_the_conversation(
    api: Client, llm: AgentFakeLLM, db_engine: AsyncEngine
) -> None:
    _script(llm, understood(facts={"age": "40"}))
    session_id = await _session(api)
    await _say(api, session_id, "I am 40")
    response = await api.post(
        f"/v1/sessions/{session_id}/consent",
        json={"purposes": ["profile"], "granted": False, "policy_version": "2026-10"},
    )
    assert response.status_code == 200
    assert response.json()["consents"] == {}
    assert await _rows(db_engine, Profile, session_id) == []
    assert await _rows(db_engine, Message, session_id) == []
    saver: InMemorySaver = api.app.state.saver  # type: ignore[attr-defined]
    assert await saver.aget_tuple({"configurable": {"thread_id": session_id}}) is None


async def test_withdrawing_sensitive_consent_erases_sensitive_fields(
    api: Client, llm: AgentFakeLLM, db_engine: AsyncEngine
) -> None:
    session_id = await _session(api)
    await api.post(
        f"/v1/sessions/{session_id}/consent",
        json={"purposes": ["sensitive"], "policy_version": "2026-10"},
    )
    _script(
        llm,
        understood(facts={"age": "20", "social_category": "obc"}),
        understood("smalltalk"),
    )
    await _say(api, session_id, "I am 20, OBC")
    profile = (await api.get(f"/v1/sessions/{session_id}/profile")).json()["profile"]
    assert profile == {"age": 20, "social_category": "obc"}

    await api.post(
        f"/v1/sessions/{session_id}/consent",
        json={"purposes": ["sensitive"], "granted": False, "policy_version": "2026-10"},
    )
    profile = (await api.get(f"/v1/sessions/{session_id}/profile")).json()["profile"]
    assert profile == {"age": 20}
    await _say(api, session_id, "thanks")  # the next turn saves the agent's (stripped) state
    profile = (await api.get(f"/v1/sessions/{session_id}/profile")).json()["profile"]
    assert profile == {"age": 20}


@pytest.mark.parametrize("how", ["delete", "message"])
async def test_deleting_a_session_removes_everything(
    api: Client, llm: AgentFakeLLM, db_engine: AsyncEngine, how: str
) -> None:
    _script(llm, understood(facts={"age": "40"}), understood("delete_account"))
    session_id = await _session(api)
    await _say(api, session_id, "I am 40")
    if how == "delete":
        assert (await api.delete(f"/v1/sessions/{session_id}")).status_code == 204
    else:
        body = await _say(api, session_id, "delete my data")
        assert body["session_deleted"] is True
        assert body["reply_text"].startswith("Your conversation and the details you shared")
    assert (await api.get(f"/v1/sessions/{session_id}/profile")).status_code == 404
    assert await _rows(db_engine, Message, session_id) == []
    saver: InMemorySaver = api.app.state.saver  # type: ignore[attr-defined]
    assert await saver.aget_tuple({"configurable": {"thread_id": session_id}}) is None


async def test_expired_sessions_are_gone(api: Client, db_engine: AsyncEngine) -> None:
    session_id = await _session(api)
    async with db_engine.begin() as connection:
        await connection.execute(
            update(Session)
            .where(Session.id == uuid.UUID(session_id))
            .values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
        )
    response = await api.post(f"/v1/sessions/{session_id}/messages", json={"text": "hi"})
    assert response.status_code == 410
    assert response.json()["error"]["code"] == "session_expired"
    assert (await api.delete(f"/v1/sessions/{session_id}")).status_code == 204  # still erasable


async def test_unknown_sessions(api: Client) -> None:
    missing = uuid.uuid4()
    assert (await api.get(f"/v1/sessions/{missing}/profile")).status_code == 404
    assert (await api.delete(f"/v1/sessions/{missing}")).status_code == 404


@pytest.mark.parametrize("text", ["", "x" * 2001])
async def test_message_length_is_validated(api: Client, text: str) -> None:
    session_id = await _session(api)
    response = await api.post(f"/v1/sessions/{session_id}/messages", json={"text": text})
    assert response.status_code == 422


async def test_an_llm_outage_gets_a_polite_reply_and_changes_nothing(
    api: Client, llm: AgentFakeLLM
) -> None:
    session_id = await _session(api)
    _script(llm, *[LLMTransientError("down")] * 3)
    body = await _say(api, session_id, "I am 40")
    assert body["kind"] == "error"
    assert (
        body["reply_text"] == "Sorry, I'm having trouble right now. Please try again in a moment."
    )
    assert (await api.get(f"/v1/sessions/{session_id}/profile")).json()["profile"] == {}


@pytest.mark.parametrize("llm", [None])
async def test_without_an_llm_chat_is_unavailable(api: Client) -> None:
    session_id = await _session(api)
    response = await api.post(f"/v1/sessions/{session_id}/messages", json={"text": "hi"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "agent_unavailable"
    assert (await api.delete(f"/v1/sessions/{session_id}")).status_code == 204


def test_openapi_lists_the_session_api(settings: Settings) -> None:
    paths = _app(settings, None).openapi()["paths"]
    assert {
        "/v1/sessions",
        "/v1/sessions/{session_id}/consent",
        "/v1/sessions/{session_id}/messages",
        "/v1/sessions/{session_id}/profile",
        "/v1/sessions/{session_id}",
        "/v1/schemes",
        "/v1/schemes/{scheme_id}",
    } <= set(paths)
