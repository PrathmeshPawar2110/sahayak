import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from sahayak.api.deps import DbSession
from sahayak.db.base import Base


@pytest.fixture
def app(app: FastAPI) -> FastAPI:
    @app.get("/session-info")
    async def session_info(session: DbSession) -> dict[str, object]:
        # Creating a session does not open a connection, so this works without Postgres.
        return {
            "is_async_session": isinstance(session, AsyncSession),
            "expire_on_commit": session.sync_session.expire_on_commit,
            "dialect": session.get_bind().dialect.name,
        }

    return app


async def test_request_gets_an_async_session_bound_to_postgres(client: httpx.AsyncClient) -> None:
    response = await client.get("/session-info")

    assert response.json() == {
        "is_async_session": True,
        "expire_on_commit": False,
        "dialect": "postgresql",
    }


def test_metadata_uses_naming_convention() -> None:
    assert Base.metadata.naming_convention["pk"] == "pk_%(table_name)s"
