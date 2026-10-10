"""Async SQLAlchemy engine construction."""

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from sahayak.config import Settings


def create_engine(settings: Settings, database_url: str | None = None) -> AsyncEngine:
    return create_async_engine(
        database_url or settings.database_url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
    )
