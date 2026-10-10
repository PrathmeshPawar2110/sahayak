"""Structured JSON logging via structlog, with stdlib (uvicorn, sqlalchemy) routed through it."""

import logging
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog
from opentelemetry import trace

from sahayak.config import Settings
from sahayak.privacy.redaction import redact

_HANDLER_MARKER = "_sahayak_handler"


def add_trace_context(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Attach the current OpenTelemetry trace/span IDs so logs and traces can be joined."""
    ctx = trace.get_current_span().get_span_context()
    if ctx.is_valid:
        event_dict["trace_id"] = format(ctx.trace_id, "032x")
        event_dict["span_id"] = format(ctx.span_id, "016x")
    return event_dict


def redact_pii(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Mask sensitive keys and PII patterns in every log event (TRD §5 Privacy)."""
    for key in list(event_dict):
        event_dict[key] = redact(event_dict[key], key)
    return event_dict


def configure_logging(settings: Settings) -> None:
    shared: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.ExtraAdder(),
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        add_trace_context,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        redact_pii,
    ]
    renderer: structlog.typing.Processor = (
        structlog.dev.ConsoleRenderer()
        if settings.log_format == "console"
        else structlog.processors.JSONRenderer(ensure_ascii=False)
    )

    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=settings.app_env == "prod",
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared,
            processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, renderer],
        )
    )
    setattr(handler, _HANDLER_MARKER, True)

    root = logging.getLogger()
    # Replace only our own handler so reconfiguring (and pytest's capture handlers) keep working.
    root.handlers = [h for h in root.handlers if not getattr(h, _HANDLER_MARKER, False)]
    root.addHandler(handler)
    root.setLevel(settings.log_level)

    # Uvicorn's access log is replaced by RequestIdMiddleware's structured request log.
    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).handlers.clear()
        logging.getLogger(name).propagate = True
    logging.getLogger("uvicorn.access").disabled = True
