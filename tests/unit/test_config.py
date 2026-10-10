import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from sahayak.config import Settings


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


def make_settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


def test_defaults_match_trd() -> None:
    settings = make_settings()

    assert settings.app_env == "dev"
    assert settings.max_questions_per_session == 8
    assert settings.signed_url_ttl_sec == 300
    assert settings.doc_expiry_offsets_days == [30, 7, 0]
    assert settings.deadline_offsets_days == [14, 3]
    assert settings.discovery_states == ["MH"]


def test_comma_separated_lists_parse_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOC_EXPIRY_OFFSETS_DAYS", "45, 10,0")
    monkeypatch.setenv("DEADLINE_OFFSETS_DAYS", "7")
    monkeypatch.setenv("DISCOVERY_STATES", "mh, ka")

    settings = make_settings()

    assert settings.doc_expiry_offsets_days == [45, 10, 0]
    assert settings.deadline_offsets_days == [7]
    assert settings.discovery_states == ["MH", "KA"]


def test_secrets_are_not_exposed_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SARVAM_API_KEY", "sk-very-secret")

    settings = make_settings()

    assert "sk-very-secret" not in repr(settings)
    assert settings.sarvam_api_key.get_secret_value() == "sk-very-secret"


def test_signed_url_ttl_capped_at_five_minutes() -> None:
    with pytest.raises(ValidationError):
        make_settings(signed_url_ttl_sec=301)


def test_review_threshold_must_not_exceed_auto_threshold() -> None:
    with pytest.raises(ValidationError, match="REVIEW_THRESHOLD"):
        make_settings(discovery_match_auto_threshold=0.6, discovery_match_review_threshold=0.8)


@pytest.mark.parametrize("value", ["21:00", "25:00-08:00", "9:00-08:00"])
def test_quiet_hours_format_is_validated(value: str) -> None:
    with pytest.raises(ValidationError):
        make_settings(quiet_hours=value)


@pytest.mark.parametrize(
    ("app_env", "explicit", "expected"),
    [("dev", None, "console"), ("prod", None, "none"), ("dev", "none", "none")],
)
def test_trace_exporter_defaults_to_console_only_in_dev(
    app_env: str, explicit: str | None, expected: str
) -> None:
    overrides: dict[str, object] = {"app_env": app_env}
    if explicit is not None:
        overrides["otel_traces_exporter"] = explicit

    assert make_settings(**overrides).otel_traces_exporter == expected


def test_env_example_lists_every_setting() -> None:
    env_example = Path(__file__).parents[2] / ".env.example"
    keys = set(re.findall(r"^([A-Z][A-Z0-9_]*)=", env_example.read_text("utf-8"), re.MULTILINE))

    missing = {name.upper() for name in Settings.model_fields} - keys

    assert not missing, f".env.example is missing: {sorted(missing)}"
