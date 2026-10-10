"""Readiness checks for `/readyz`: each dependency gets a named async probe."""

import asyncio
from collections.abc import Awaitable, Callable, Mapping

import structlog
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

ReadinessCheck = Callable[[], Awaitable[None]]

CHECK_TIMEOUT_SEC = 2.0

log = structlog.get_logger(__name__)


def postgres_check(engine: AsyncEngine) -> ReadinessCheck:
    async def check() -> None:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

    return check


def redis_check(redis: Redis) -> ReadinessCheck:
    async def check() -> None:
        await redis.ping()

    return check


async def run_checks(checks: Mapping[str, ReadinessCheck]) -> dict[str, bool]:
    """Run all probes concurrently, each with a timeout. Failures are logged, never raised."""

    async def run_one(name: str, check: ReadinessCheck) -> tuple[str, bool]:
        try:
            await asyncio.wait_for(check(), timeout=CHECK_TIMEOUT_SEC)
        except Exception as exc:
            log.warning("readiness_check_failed", check=name, exc_type=type(exc).__name__)
            return name, False
        return name, True

    results = await asyncio.gather(*(run_one(name, check) for name, check in checks.items()))
    return dict(results)
