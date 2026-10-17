"""The eligibility eval (TRD §12.1): rule engine verdicts against hand-checked labels.

Each (profile, scheme) pair whose scheme has a rule is one prediction. Metrics: overall verdict
accuracy over the three verdicts, a confusion matrix, and per scheme the accuracy and the
precision, recall and F1 of ELIGIBLE. Schemes without a rule are listed, not scored.
"""

import hashlib
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from sahayak.db.models import EvalRun
from sahayak.eligibility.engine import Verdict, evaluate
from sahayak.eligibility.rules import SchemeRule
from sahayak.evals.dataset import EvalProfile

SUITE = "eligibility"
VERDICTS = list(Verdict)


@dataclass(frozen=True)
class Mismatch:
    profile_id: str
    scheme_id: str
    expected: Verdict
    actual: Verdict
    detail: str


@dataclass
class SchemeScore:
    scheme_id: str
    rule_version: int
    confusion: Counter[tuple[Verdict, Verdict]] = field(default_factory=Counter)

    @property
    def total(self) -> int:
        return sum(self.confusion.values())

    @property
    def correct(self) -> int:
        return sum(n for (expected, actual), n in self.confusion.items() if expected is actual)

    @property
    def accuracy(self) -> float | None:
        return self.correct / self.total if self.total else None

    def _count(self, *, expected: bool, actual: bool) -> int:
        return sum(
            n
            for (e, a), n in self.confusion.items()
            if (e is Verdict.ELIGIBLE) is expected and (a is Verdict.ELIGIBLE) is actual
        )

    @property
    def precision(self) -> float | None:
        tp, fp = self._count(expected=True, actual=True), self._count(expected=False, actual=True)
        return tp / (tp + fp) if tp + fp else None

    @property
    def recall(self) -> float | None:
        tp, fn = self._count(expected=True, actual=True), self._count(expected=True, actual=False)
        return tp / (tp + fn) if tp + fn else None

    @property
    def f1(self) -> float | None:
        p, r = self.precision, self.recall
        if p is None or r is None or p + r == 0:
            return None
        return 2 * p * r / (p + r)


@dataclass
class EligibilityEval:
    rules_source: str
    profiles: int
    checked_profiles: int
    schemes: dict[str, SchemeScore]
    unscored_schemes: list[str]  # labelled, but no rule to evaluate
    mismatches: list[Mismatch]

    @property
    def total(self) -> int:
        return sum(s.total for s in self.schemes.values())

    @property
    def correct(self) -> int:
        return sum(s.correct for s in self.schemes.values())

    @property
    def accuracy(self) -> float | None:
        return self.correct / self.total if self.total else None

    @property
    def confusion(self) -> Counter[tuple[Verdict, Verdict]]:
        total: Counter[tuple[Verdict, Verdict]] = Counter()
        for score in self.schemes.values():
            total.update(score.confusion)
        return total

    def metrics(self) -> dict[str, Any]:
        """What goes into `eval_runs.metrics`."""
        return {
            "accuracy": self.accuracy,
            "predictions": self.total,
            "correct": self.correct,
            "profiles": self.profiles,
            "checked_profiles": self.checked_profiles,
            "unscored_schemes": self.unscored_schemes,
            "confusion": {
                f"{e.value}->{a.value}": n for (e, a), n in sorted(self.confusion.items())
            },
            "schemes": {
                scheme_id: {
                    "rule_version": s.rule_version,
                    "accuracy": s.accuracy,
                    "precision": s.precision,
                    "recall": s.recall,
                    "f1": s.f1,
                    "predictions": s.total,
                }
                for scheme_id, s in sorted(self.schemes.items())
            },
        }


def run_eligibility_eval(
    profiles: list[EvalProfile], rules: dict[str, SchemeRule], *, rules_source: str
) -> EligibilityEval:
    labelled = sorted({scheme_id for p in profiles for scheme_id in p.expected})
    scores = {
        scheme_id: SchemeScore(scheme_id, rules[scheme_id].version)
        for scheme_id in labelled
        if scheme_id in rules
    }
    mismatches = []
    for profile in profiles:
        for scheme_id, expected in profile.expected.items():
            if scheme_id not in scores:
                continue
            result = evaluate(rules[scheme_id], profile.profile)
            scores[scheme_id].confusion[(expected, result.verdict)] += 1
            if result.verdict is not expected:
                detail = "; ".join(
                    [
                        *(f"failed {c.text()}" for c in result.failed),
                        *(f"missing {f}" for f in result.missing_fields),
                    ]
                )
                mismatches.append(
                    Mismatch(profile.id, scheme_id, expected, result.verdict, detail or "-")
                )
    return EligibilityEval(
        rules_source=rules_source,
        profiles=len(profiles),
        checked_profiles=sum(p.label_status == "checked" for p in profiles),
        schemes=scores,
        unscored_schemes=[s for s in labelled if s not in scores],
        mismatches=mismatches,
    )


def render_report(
    result: EligibilityEval, *, git_sha: str | None, dataset: Path, min_accuracy: float
) -> str:
    accuracy = result.accuracy
    if accuracy is None:
        status = "NOT RUN: no rules to evaluate"
    else:
        status = "PASS" if accuracy >= min_accuracy else "FAIL"
    lines = [
        f"# Eligibility eval: {datetime.now(UTC):%Y-%m-%d %H:%M} UTC",
        "",
        f"- **Result:** {status} (target accuracy >= {min_accuracy:.0%})",
        f"- **Accuracy:** {_pct(accuracy)} ({result.correct}/{result.total} verdicts)",
        f"- **Rules:** {result.rules_source}, {len(result.schemes)} scheme(s)",
        f"- **Dataset:** `{dataset.as_posix()}`, {result.profiles} profiles, "
        f"{result.checked_profiles} with human-checked labels",
        f"- **Commit:** {git_sha or 'unknown'}",
    ]
    if result.unscored_schemes:
        lines.append(f"- **No rule, not scored:** {', '.join(result.unscored_schemes)}")
    if result.schemes:
        lines += [
            "",
            "## Per scheme",
            "",
            "| Scheme | Rule | Accuracy | Precision (ELIGIBLE) | Recall | F1 |",
            "|---|---|---|---|---|---|",
        ]
        for scheme_id, s in sorted(result.schemes.items()):
            lines.append(
                f"| {scheme_id} | v{s.rule_version} | {_pct(s.accuracy)} | {_pct(s.precision)} "
                f"| {_pct(s.recall)} | {_pct(s.f1)} |"
            )
        lines += ["", "## Confusion matrix (rows: expected, columns: engine)", ""]
        lines.append("| | " + " | ".join(v.value for v in VERDICTS) + " |")
        lines.append("|---" * (len(VERDICTS) + 1) + "|")
        confusion = result.confusion
        for expected in VERDICTS:
            row = " | ".join(str(confusion[(expected, actual)]) for actual in VERDICTS)
            lines.append(f"| **{expected.value}** | {row} |")
    if result.mismatches:
        lines += [
            "",
            "## Mismatches",
            "",
            "| Profile | Scheme | Expected | Engine | Why the engine decided so |",
            "|---|---|---|---|---|",
        ]
        lines += [
            f"| {m.profile_id} | {m.scheme_id} | {m.expected.value} | {m.actual.value} "
            f"| {m.detail.replace('|', '/')} |"
            for m in result.mismatches
        ]
    return "\n".join(lines) + "\n"


def run_config(result: EligibilityEval, *, dataset: Path, min_accuracy: float) -> dict[str, Any]:
    """What goes into `eval_runs.config`: enough to reproduce the run."""
    return {
        "rules_source": result.rules_source,
        "rule_versions": {k: s.rule_version for k, s in sorted(result.schemes.items())},
        "dataset": dataset.as_posix(),
        "dataset_sha256": hashlib.sha256(dataset.read_bytes()).hexdigest(),
        "min_accuracy": min_accuracy,
    }


async def record_run(
    session: AsyncSession, result: EligibilityEval, *, git_sha: str | None, config: dict[str, Any]
) -> EvalRun:
    run = EvalRun(suite=SUITE, git_sha=git_sha, config=config, metrics=result.metrics())
    session.add(run)
    await session.flush()
    return run


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value:.1%}"
