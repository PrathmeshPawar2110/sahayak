import httpx
from alembic import command
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from tests.integration.conftest import alembic_config


async def test_readyz_ok_against_real_services(services: None, client: httpx.AsyncClient) -> None:
    response = await client.get("/readyz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"postgres": "ok", "redis": "ok"}}


async def test_baseline_migration_enables_pgvector(db_session: AsyncSession) -> None:
    result = await db_session.execute(
        text("SELECT extname FROM pg_extension WHERE extname = 'vector'")
    )

    assert result.scalar_one() == "vector"


async def test_committed_writes_are_rolled_back_after_the_test(
    db_session: AsyncSession, db_engine: AsyncEngine
) -> None:
    await db_session.execute(text("CREATE TABLE rollback_probe (id int)"))
    await db_session.commit()  # releases a savepoint only

    async with db_engine.connect() as other:
        visible = await other.execute(text("SELECT to_regclass('rollback_probe')"))
        assert visible.scalar_one() is None


async def test_redis_roundtrip(redis: Redis) -> None:
    await redis.set("probe", "ok", ex=60)

    assert await redis.get("probe") == b"ok"


def test_migrations_downgrade_and_upgrade_cleanly(database_url: str) -> None:
    config = alembic_config(database_url)

    command.downgrade(config, "base")
    command.upgrade(config, "head")
