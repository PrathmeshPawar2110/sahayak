"""FastAPI application factory. Run with `uvicorn sahayak.api.app:create_app --factory`."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from redis.asyncio import Redis

from sahayak import __version__
from sahayak.api import health
from sahayak.api.errors import ErrorEnvelope, register_error_handlers
from sahayak.api.readiness import postgres_check, redis_check
from sahayak.config import Settings, get_settings
from sahayak.db.session import create_engine
from sahayak.observability.logging import configure_logging
from sahayak.observability.middleware import RequestIdMiddleware
from sahayak.observability.tracing import configure_tracing, instrument_app


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings)
    tracer_provider = configure_tracing(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Neither call opens a connection; the first query / readiness probe does.
        engine = create_engine(settings)
        redis = Redis.from_url(settings.redis_url)
        app.state.db_engine = engine
        app.state.redis = redis
        app.state.readiness_checks = {
            "postgres": postgres_check(engine),
            "redis": redis_check(redis),
        }
        try:
            yield
        finally:
            await redis.aclose()
            await engine.dispose()

    app = FastAPI(
        title="Sahayak API",
        version=__version__,
        description="Multilingual voice agent for Indian government schemes.",
        docs_url="/docs",
        redoc_url=None,
        lifespan=lifespan,
        responses={500: {"model": ErrorEnvelope, "description": "Unexpected server error"}},
    )
    app.state.settings = settings

    register_error_handlers(app)
    app.add_middleware(RequestIdMiddleware)
    app.include_router(health.router)

    instrument_app(app, tracer_provider)
    return app
