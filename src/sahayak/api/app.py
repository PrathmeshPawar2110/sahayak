"""FastAPI application factory. Run with `uvicorn sahayak.api.app:create_app --factory`."""

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from fastapi import FastAPI
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sahayak import __version__
from sahayak.agent.service import AgentService, agent_service
from sahayak.api import health, schemes, sessions
from sahayak.api import voice as voice_api
from sahayak.api.errors import ErrorEnvelope, register_error_handlers
from sahayak.api.readiness import postgres_check, redis_check
from sahayak.channels import web_ws
from sahayak.config import Settings, get_settings
from sahayak.db.session import create_engine, create_session_factory
from sahayak.observability.logging import configure_logging
from sahayak.observability.middleware import RequestIdMiddleware
from sahayak.observability.tracing import configure_tracing, instrument_app
from sahayak.voice.pipeline import VoiceServices, voice_services

# Builds the agent for the app's lifetime (tests pass one with fakes).
AgentFactory = Callable[
    [Settings, async_sessionmaker[AsyncSession], Redis],
    AbstractAsyncContextManager[AgentService],
]
# Builds the speech providers (tests pass fakes).
VoiceFactory = Callable[[Settings, Redis], VoiceServices]


def create_app(
    settings: Settings | None = None,
    *,
    agent: AgentFactory | None = None,
    voice: VoiceFactory | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    agent_factory = agent or agent_service
    voice_factory = voice or voice_services
    configure_logging(settings)
    tracer_provider = configure_tracing(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Neither call opens a connection; the first query / readiness probe does.
        engine = create_engine(settings)
        redis = Redis.from_url(settings.redis_url)
        session_factory = create_session_factory(engine)
        app.state.db_engine = engine
        app.state.session_factory = session_factory
        app.state.redis = redis
        app.state.readiness_checks = {
            "postgres": postgres_check(engine),
            "redis": redis_check(redis),
        }
        app.state.voice = voice_factory(settings, redis)
        try:
            async with agent_factory(settings, session_factory, redis) as service:
                app.state.agent = service
                yield
        finally:
            await app.state.voice.aclose()
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
    app.include_router(sessions.router)
    app.include_router(voice_api.router)
    app.include_router(web_ws.router)
    app.include_router(schemes.router)

    instrument_app(app, tracer_provider)
    return app
