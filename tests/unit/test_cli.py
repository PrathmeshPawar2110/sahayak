import io

import pytest
import typer
from rich.console import Console
from typer.testing import CliRunner

from sahayak.cli import app
from sahayak.cli import ingest as ingest_cli
from sahayak.cli.ingest import print_reports, select_schemes
from sahayak.config import Settings
from sahayak.ingestion.index import IndexStats
from sahayak.ingestion.manifest import SchemeSpec
from sahayak.ingestion.pipeline import SchemeReport

runner = CliRunner()


def spec(scheme_id: str) -> SchemeSpec:
    return SchemeSpec.model_validate(
        {
            "id": scheme_id,
            "names": {"en": scheme_id},
            "level": "central",
            "sources": [{"url": "https://example.gov.in/"}],
        }
    )


def test_help_lists_the_commands() -> None:
    result = runner.invoke(app, ["--help"])
    rules = runner.invoke(app, ["rules", "--help"])

    assert result.exit_code == 0
    assert "ingest" in result.output
    assert "rules" in result.output
    assert "review" in rules.output
    assert "list" in rules.output


def test_select_schemes_keeps_order_and_drops_repeats() -> None:
    specs = [spec("a"), spec("b"), spec("c")]

    assert select_schemes(specs, None) == specs
    assert [s.id for s in select_schemes(specs, ["c", "a", "c"])] == ["c", "a"]
    with pytest.raises(typer.BadParameter, match="not in the manifest: x"):
        select_schemes(specs, ["a", "x"])


def test_ingest_without_llm_settings_explains_skip_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(_env_file=None, llm_provider="azure_openai", log_format="json")
    monkeypatch.setattr(ingest_cli, "get_settings", lambda: settings)

    result = runner.invoke(app, ["ingest", "--scheme", "pm-kisan"])

    assert result.exit_code == 2
    assert "AZURE_OPENAI_ENDPOINT" in result.output
    assert "--skip-rules" in result.output


def test_ingest_rejects_unknown_scheme_ids() -> None:
    result = runner.invoke(app, ["ingest", "--scheme", "no-such-scheme", "--skip-rules"])

    assert result.exit_code == 2
    assert "not in the manifest" in result.output


def test_print_reports_shows_failures_and_warnings() -> None:
    out = io.StringIO()
    reports = [
        SchemeReport("ok", fetched=1, index=IndexStats(added=3), rule="created", rule_version=1),
        SchemeReport("bad", failed_sources=["https://x.gov.in/: HTTP 500"], warnings=["w1"]),
    ]

    print_reports(Console(file=out, width=200), reports)

    text = out.getvalue()
    assert "+3 =0 -0" in text
    assert "created (v1)" in text
    assert "bad: fetch failed: https://x.gov.in/: HTTP 500" in text
    assert "bad: w1" in text
    assert [r.ok for r in reports] == [True, False]
