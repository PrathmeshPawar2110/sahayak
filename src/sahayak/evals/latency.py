"""Latency per pipeline stage, p50 and p95 (TRD §12.5, M5-T09).

The voice pipeline logs one `voice_turn` event per turn (`sahayak.voice.pipeline`) and the agent
one `agent_turn` event per text or voice turn. `sahayak latency` reads JSON log lines (files or
stdin), and `stage_report` turns those events into percentiles per channel and stage.
"""

import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

VOICE_STAGES = ("stt_ms", "agent_ms", "tts_ms", "total_ms")
# TRD §5: web voice turn (end of speech to first audio) p50 <= 3.5 s, p95 <= 6 s.
VOICE_TARGETS_MS = {"p50": 3500, "p95": 6000}


def percentile(values: Sequence[float], q: float) -> float | None:
    """The q-th percentile (0-100), interpolating linearly between closest ranks."""
    if not values:
        return None
    ordered = sorted(values)
    rank = (len(ordered) - 1) * q / 100
    low, high = math.floor(rank), math.ceil(rank)
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


@dataclass(frozen=True)
class StageStats:
    count: int
    p50: float | None
    p95: float | None
    max: float | None

    @classmethod
    def of(cls, values: Sequence[float]) -> "StageStats":
        return cls(
            count=len(values),
            p50=percentile(values, 50),
            p95=percentile(values, 95),
            max=max(values) if values else None,
        )

    def as_dict(self) -> dict[str, Any]:
        return {"count": self.count, "p50": self.p50, "p95": self.p95, "max": self.max}


@dataclass(frozen=True)
class LatencyReport:
    # channel ("upload", "stream", or "text" for agent turns) -> stage -> stats
    stages: dict[str, dict[str, StageStats]]
    turns: dict[str, int]

    def metrics(self) -> dict[str, Any]:
        return {
            channel: {stage: stats.as_dict() for stage, stats in stages.items()}
            for channel, stages in self.stages.items()
        }


def read_events(lines: Iterable[str]) -> list[dict[str, Any]]:
    """JSON log lines as dicts; anything else (console output, blank lines) is skipped."""
    events = []
    for line in lines:
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def stage_report(events: Iterable[Mapping[str, Any]]) -> LatencyReport:
    samples: dict[str, dict[str, list[float]]] = {}
    turns: dict[str, int] = {}
    for event in events:
        name = event.get("event")
        stages: tuple[str, ...]
        if name == "voice_turn":
            channel = str(event.get("channel", "voice"))
            stages = VOICE_STAGES
        elif name == "agent_turn":
            channel, stages = "agent", ("latency_ms",)
        else:
            continue
        turns[channel] = turns.get(channel, 0) + 1
        bucket = samples.setdefault(channel, {stage: [] for stage in stages})
        for stage in stages:
            value = event.get(stage)
            if isinstance(value, int | float) and not isinstance(value, bool):
                bucket[stage].append(float(value))
    return LatencyReport(
        stages={
            channel: {stage: StageStats.of(values) for stage, values in stages.items()}
            for channel, stages in samples.items()
        },
        turns=turns,
    )


def render_table(report: LatencyReport) -> str:
    """A Markdown table: one row per channel and stage."""
    lines = ["| Channel | Stage | Turns | p50 ms | p95 ms | max ms |", "|---|---|---|---|---|---|"]
    for channel, stages in report.stages.items():
        for stage, stats in stages.items():
            lines.append(
                f"| {channel} | {stage.removesuffix('_ms')} | {stats.count} | {_ms(stats.p50)} "
                f"| {_ms(stats.p95)} | {_ms(stats.max)} |"
            )
    return "\n".join(lines) + "\n"


def _ms(value: float | None) -> str:
    return "-" if value is None else f"{value:.0f}"
