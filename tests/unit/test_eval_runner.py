import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from sahayak.cli import app
from sahayak.eligibility.engine import Verdict
from sahayak.eligibility.profile import UserProfile
from sahayak.eligibility.rules import SchemeRule, validate_rule
from sahayak.eligibility.store import write_rule_files
from sahayak.evals.dataset import EvalProfile, write_profiles
from sahayak.evals.eligibility import render_report, run_config, run_eligibility_eval

runner = CliRunner()

E, N, Q = Verdict.ELIGIBLE, Verdict.NOT_ELIGIBLE, Verdict.NEEDS_INFO


def adult_rule(scheme_id: str = "apy", minimum: int = 18) -> SchemeRule:
    return validate_rule(
        {
            "scheme_id": scheme_id,
            "version": 2,
            "status": "approved",
            "logic": {"field": "age", "op": ">=", "value": minimum},
        }
    )


def case(pid: str, age: int | None, **expected: Verdict) -> EvalProfile:
    return EvalProfile(
        id=pid,
        description=pid,
        profile=UserProfile(language="en", age=age),
        expected=expected,
    )


PROFILES = [
    case("p001", 30, apy=E, ssy=N),
    case("p002", 12, apy=N, ssy=E),
    case("p003", None, apy=Q, ssy=Q),
    case("p004", 17, apy=E, ssy=E),  # mislabelled for apy: the engine says NOT_ELIGIBLE
]


def test_scores_only_schemes_with_rules() -> None:
    result = run_eligibility_eval(PROFILES, {"apy": adult_rule()}, rules_source="test")

    assert result.unscored_schemes == ["ssy"]
    assert (result.correct, result.total, result.accuracy) == (3, 4, 0.75)
    score = result.schemes["apy"]
    assert (score.precision, score.recall) == (1.0, 0.5)
    assert score.f1 == pytest.approx(2 / 3)
    assert [(m.profile_id, m.expected, m.actual) for m in result.mismatches] == [("p004", E, N)]
    assert result.mismatches[0].detail == "failed age >= 18"
    assert result.confusion[(E, N)] == 1


def test_undefined_ratios_are_none() -> None:
    result = run_eligibility_eval(
        [PROFILES[1], PROFILES[2]], {"apy": adult_rule()}, rules_source="t"
    )

    score = result.schemes["apy"]
    assert (score.precision, score.recall, score.f1) == (None, None, None)
    assert run_eligibility_eval([], {}, rules_source="t").accuracy is None


def test_metrics_and_config_are_json_ready(tmp_path: Path) -> None:
    dataset = tmp_path / "profiles.jsonl"
    write_profiles(PROFILES, dataset)
    result = run_eligibility_eval(PROFILES, {"apy": adult_rule()}, rules_source="test")

    metrics = json.loads(json.dumps(result.metrics()))
    config = run_config(result, dataset=dataset, min_accuracy=0.9)

    assert metrics["accuracy"] == 0.75
    assert metrics["confusion"]["ELIGIBLE->NOT_ELIGIBLE"] == 1
    assert metrics["schemes"]["apy"]["rule_version"] == 2
    assert config["rule_versions"] == {"apy": 2}
    assert len(config["dataset_sha256"]) == 64


def test_report_shows_status_scores_and_mismatches() -> None:
    result = run_eligibility_eval(PROFILES, {"apy": adult_rule()}, rules_source="test")

    report = render_report(
        result, git_sha="abc123", dataset=Path("evals/p.jsonl"), min_accuracy=0.9
    )

    assert "**Result:** FAIL" in report
    assert "75.0% (3/4 verdicts)" in report
    assert "| apy | v2 | 75.0% | 100.0% | 50.0% | 66.7% |" in report
    assert "| **ELIGIBLE** | 1 | 1 | 0 |" in report
    assert "| p004 | apy | ELIGIBLE | NOT_ELIGIBLE | failed age >= 18 |" in report
    assert "**No rule, not scored:** ssy" in report


def test_report_without_rules_says_not_run() -> None:
    result = run_eligibility_eval(PROFILES, {}, rules_source="test")

    report = render_report(result, git_sha=None, dataset=Path("p.jsonl"), min_accuracy=0.9)

    assert "NOT RUN" in report
    assert "## Per scheme" not in report


def invoke(tmp_path: Path, rules: dict[str, SchemeRule], *extra: str) -> Any:
    rules_dir, reports = tmp_path / "rules", tmp_path / "reports"
    dataset = tmp_path / "profiles.jsonl"
    write_rule_files(rules, rules_dir)
    write_profiles(PROFILES, dataset)
    args = ["eval", "--suite", "eligibility", "--no-record", "--rules-dir", str(rules_dir)]
    args += ["--dataset", str(dataset), "--report-dir", str(reports), *extra]
    return runner.invoke(app, args)


def test_eval_command_fails_below_the_target(tmp_path: Path) -> None:
    result = invoke(tmp_path, {"apy": adult_rule()})

    assert result.exit_code == 1, result.output
    assert "below 90%" in result.output
    (report,) = (tmp_path / "reports").glob("*-eligibility.md")
    assert "**Result:** FAIL" in report.read_text(encoding="utf-8")


def test_eval_command_passes_at_the_target(tmp_path: Path) -> None:
    result = invoke(tmp_path, {"apy": adult_rule()}, "--min-accuracy", "0.75")

    assert result.exit_code == 0, result.output


def test_eval_command_without_rules_does_not_fail(tmp_path: Path) -> None:
    result = invoke(tmp_path, {})

    assert result.exit_code == 0, result.output
    assert "nothing was scored" in result.output


def test_previewing_drafts_cannot_be_recorded() -> None:
    result = runner.invoke(app, ["eval", "--rules", "latest"])

    assert result.exit_code == 2
    assert "--no-record" in result.output
