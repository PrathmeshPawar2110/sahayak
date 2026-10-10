import json
import logging

import pytest
import structlog

from sahayak.config import Settings
from sahayak.observability.logging import configure_logging


def _last_json_line(captured: str) -> dict[str, object]:
    lines = [line for line in captured.splitlines() if line.startswith("{")]
    result: dict[str, object] = json.loads(lines[-1])
    return result


def test_structlog_events_are_json_and_redacted(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(Settings(_env_file=None, log_format="json"))

    structlog.get_logger("test").info("user said", text="my number is 9876543210", caste_name="x")

    record = _last_json_line(capsys.readouterr().out)
    assert record["event"] == "user said"
    assert record["level"] == "info"
    assert record["text"] == "my number is XXXXXX3210"
    assert record["caste_name"] == "[REDACTED]"
    assert "timestamp" in record


def test_stdlib_logs_go_through_the_same_pipeline(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(Settings(_env_file=None, log_format="json"))

    logging.getLogger("thirdparty").warning("contact 9876543210")

    record = _last_json_line(capsys.readouterr().out)
    assert record["event"] == "contact XXXXXX3210"
    assert record["level"] == "warning"
    assert record["logger"] == "thirdparty"


def test_bound_context_is_included(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(Settings(_env_file=None, log_format="json"))

    with structlog.contextvars.bound_contextvars(request_id="abc123"):
        structlog.get_logger().info("hello")

    assert _last_json_line(capsys.readouterr().out)["request_id"] == "abc123"
