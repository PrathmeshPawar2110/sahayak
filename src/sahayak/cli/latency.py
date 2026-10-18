"""`sahayak latency [LOG...]`: p50/p95 per voice pipeline stage from the API's JSON logs."""

import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from sahayak.evals.latency import read_events, render_table, stage_report

console = Console()


def latency_command(
    logs: Annotated[
        list[Path] | None,
        typer.Argument(help="JSON log files (LOG_FORMAT=json); standard input if none."),
    ] = None,
) -> None:
    """Report p50/p95 latency per stage (STT, agent, TTS, total) from `voice_turn` and
    `agent_turn` log events."""
    lines: list[str] = []
    if logs:
        for path in logs:
            lines += path.read_text(encoding="utf-8", errors="replace").splitlines()
    else:
        lines = sys.stdin.read().splitlines()
    report = stage_report(read_events(lines))
    if not report.stages:
        console.print("[yellow]No voice_turn or agent_turn events found.[/yellow]")
        raise typer.Exit(1)
    console.print(render_table(report), markup=False, highlight=False)
