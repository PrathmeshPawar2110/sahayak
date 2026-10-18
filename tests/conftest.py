import asyncio
import os
import sys
from collections.abc import AsyncIterator

import httpx
import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI

from sahayak.api.app import create_app
from sahayak.config import Settings

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://sahayak:sahayak@localhost:5432/sahayak_test"
)
TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/15")

if sys.platform == "win32":
    # psycopg (the agent's checkpointer) can't run on Windows' default Proactor event loop.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        log_format="json",
        otel_traces_exporter="none",
        database_url=TEST_DATABASE_URL,
        redis_url=TEST_REDIS_URL,
    )


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """HTTP client against the ASGI app with its lifespan running. No network involved."""
    async with LifespanManager(app) as manager:
        transport = httpx.ASGITransport(app=manager.app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Mark tests by directory so `-m integration` / `-m "not e2e"` work without decorators."""
    for item in items:
        parts = item.path.parts
        if "integration" in parts:
            item.add_marker(pytest.mark.integration)
        elif "e2e" in parts:
            item.add_marker(pytest.mark.e2e)
