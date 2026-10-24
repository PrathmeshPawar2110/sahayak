import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from sahayak.cli import app
from sahayak.evals.latency import percentile, read_events, render_table, stage_report


def test_percentile_interpolates() -> None:
    assert percentile([], 50) is None
    assert percentile([5.0], 95) == 5.0
    assert percentile([1.0, 2.0, 3.0, 4.0], 50) == 2.5
    assert percentile([float(v) for v in range(1, 101)], 95) == pytest.approx(95.05)


def _voice(channel: str, stt: int, agent: int, tts: int | None, total: int) -> str:
    return json.dumps(
        {
            "event": "voice_turn",
            "channel": channel,
            "stt_ms": stt,
            "agent_ms": agent,
            "tts_ms": tts,
            "total_ms": total,
        }
    )


LOG = [
    "uvicorn console line, not JSON",
    _voice("upload", 300, 1500, 600, 2400),
    _voice("upload", 500, 2500, None, 3000),
    _voice("stream", 250, 1600, 300, 2150),
    json.dumps({"event": "agent_turn", "latency_ms": 1900}),
    json.dumps({"event": "llm_call", "latency_ms": 900}),
    "{broken json",
    "",
]


def test_stage_report_groups_by_channel_and_stage() -> None:
    report = stage_report(read_events(LOG))

    assert report.turns == {"upload": 2, "stream": 1, "agent": 1}
    upload = report.stages["upload"]
    assert upload["stt_ms"].p50 == 400
    assert upload["tts_ms"].count == 1  # a turn without audio has no TTS time
    assert upload["total_ms"].max == 3000
    assert report.stages["agent"]["latency_ms"].p50 == 1900
    assert report.metrics()["stream"]["total_ms"]["p50"] == 2150
    table = render_table(report)
    assert "| upload | stt | 2 | 400 |" in table
    assert "| upload | tts | 1 | 600 | 600 | 600 |" in table


def test_latency_command(tmp_path: Path) -> None:
    log = tmp_path / "api.log"
    log.write_text("\n".join(LOG), encoding="utf-8")
    result = CliRunner().invoke(app, ["latency", str(log)])
    assert result.exit_code == 0, result.output
    assert "| stream | total | 1 | 2150 |" in result.output

    empty = tmp_path / "empty.log"
    empty.write_text("nothing here\n", encoding="utf-8")
    assert CliRunner().invoke(app, ["latency", str(empty)]).exit_code == 1


def test_latency_command_reads_stdin() -> None:
    result = CliRunner().invoke(app, ["latency"], input="\n".join(LOG))
    assert result.exit_code == 0
    assert "| agent | latency | 1 | 1900 |" in result.output
