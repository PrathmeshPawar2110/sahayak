"""OpenTelemetry tracing setup. Console exporter in dev; exporters are chosen by config."""

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

from sahayak import __version__
from sahayak.config import Settings

_provider: TracerProvider | None = None


def configure_tracing(settings: Settings) -> TracerProvider:
    """Create the process-wide tracer provider once and register it globally."""
    # The OTel global provider can only be set once per process.
    global _provider
    if _provider is None:
        resource = Resource.create(
            {
                "service.name": "sahayak-api",
                "service.version": __version__,
                "deployment.environment": settings.app_env,
            }
        )
        _provider = TracerProvider(resource=resource)
        if settings.otel_traces_exporter == "console":
            _provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
        trace.set_tracer_provider(_provider)
    return _provider


def instrument_app(app: FastAPI, provider: TracerProvider) -> None:
    # Health probes are polled constantly; tracing them only adds noise.
    FastAPIInstrumentor.instrument_app(
        app, tracer_provider=provider, excluded_urls="/healthz,/readyz"
    )
