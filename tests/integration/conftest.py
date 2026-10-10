"""Fixtures backed by real Postgres and Redis.

Locally, tests here are skipped when the services are not reachable (start them with
`just up`). CI sets SAHAYAK_REQUIRE_SERVICES=1 so an unreachable service fails the run instead.
"""

import os
import socket
from collections.abc import AsyncIterator
from pathlib import Path
from typing import NoReturn

import pytest
from alembic import command
from alembic.config import Config
from redis.asyncio import Redis
from sqlalchemy import make_url, pool
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from tests.conftest import TEST_DATABASE_URL, TEST_REDIS_URL

REPO_ROOT = Path(__file__).parents[2]
REQUIRE_SERVICES = os.environ.get("SAHAYAK_REQUIRE_SERVICES") == "1"


def _unavailable(reason: str) -> NoReturn:
    if REQUIRE_SERVICES:
        pytest.fail(f"{reason} (SAHAYAK_REQUIRE_SERVICES=1)", pytrace=False)
    pytest.skip(reason)


def _reachable(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def alembic_config(database_url: str) -> Config:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    config.attributes["configure_logging"] = False
    return config


@pytest.fixture(scope="session")
def database_url() -> str:
    """URL of the test database, migrated to head once per test session."""
    url = make_url(TEST_DATABASE_URL)
    if not _reachable(url.host or "localhost", url.port or 5432):
        _unavailable(f"Postgres not reachable at {url.host}:{url.port}")
    # Sync fixture: Alembic's env.py calls asyncio.run(), which needs no running loop.
    command.upgrade(alembic_config(TEST_DATABASE_URL), "head")
    return TEST_DATABASE_URL


@pytest.fixture(scope="session")
def redis_url() -> str:
    url = make_url(TEST_REDIS_URL)
    if not _reachable(url.host or "localhost", url.port or 6379):
        _unavailable(f"Redis not reachable at {url.host}:{url.port}")
    return TEST_REDIS_URL


@pytest.fixture
async def db_engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(database_url, poolclass=pool.NullPool)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db_session(db_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """A session inside a transaction that is always rolled back.

    `session.commit()` in code under test only releases a SAVEPOINT, so nothing a test writes
    is ever visible to other tests.
    """
    async with db_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
        )
        try:
            yield session
        finally:
            await session.close()
            await transaction.rollback()


@pytest.fixture
async def redis(redis_url: str) -> AsyncIterator[Redis]:
    """Client on the dedicated test Redis DB, flushed after each test."""
    client = Redis.from_url(redis_url)
    try:
        yield client
    finally:
        await client.flushdb()
        await client.aclose()


@pytest.fixture
def services(database_url: str, redis_url: str) -> None:
    """Depend on this to require both Postgres and Redis."""
