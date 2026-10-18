"""`sahayak eval --suite eligibility`: score the rule engine against the labelled profiles."""

import asyncio
import os
import subprocess
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from sahayak.config import get_settings
from sahayak.db.session import create_engine, create_session_factory
from sahayak.eligibility.store import (
    RULES_DIR,
    load_approved_rules,
    load_latest_rules,
    load_rule_files,
)
from sahayak.evals.dataset import PROFILES_PATH, load_profiles
from sahayak.evals.eligibility import (
    EligibilityEval,
    record_run,
    render_report,
    run_config,
    run_eligibility_eval,
)
from sahayak.observability.logging import configure_logging

console = Console()


class Suite(StrEnum):
    ELIGIBILITY = "eligibility"
    ALL = "all"  # every suite that exists so far


class RulesSource(StrEnum):
    FILES = "files"  # data/rules/*.json, the committed snapshot (what CI uses)
    DB = "db"  # approved rules in the database
    LATEST = "latest"  # newest draft or approved rule per scheme: a preview for reviewers


def eval_command(
    suite: Annotated[Suite, typer.Option(help="Which eval suite to run.")] = Suite.ELIGIBILITY,
    rules: Annotated[
        RulesSource, typer.Option(help="Where the rules come from.")
    ] = RulesSource.FILES,
    rules_dir: Annotated[Path, typer.Option(help="Rule snapshot for --rules files.")] = RULES_DIR,
    dataset: Annotated[Path, typer.Option(help="Labelled profiles (JSONL).")] = PROFILES_PATH,
    report_dir: Annotated[Path, typer.Option(help="Where to write the report.")] = Path(
        "evals/reports"
    ),
    min_accuracy: Annotated[
        float, typer.Option(min=0, max=1, help="Fail below this verdict accuracy.")
    ] = 0.9,
    record: Annotated[
        bool, typer.Option(help="Save the metrics as an eval_runs row (needs DATABASE_URL).")
    ] = True,
) -> None:
    """Run an eval suite, write a Markdown report and record the run."""
    settings = get_settings()
    configure_logging(settings)
    if rules is RulesSource.LATEST and record:
        raise typer.BadParameter("--rules latest previews drafts; add --no-record")
    profiles = load_profiles(dataset)
    git_sha = current_git_sha()
    source = f"{rules_dir.as_posix()}/*.json" if rules is RulesSource.FILES else rules.value

    async def run() -> EligibilityEval:
        if rules is RulesSource.FILES and not record:
            return run_eligibility_eval(profiles, load_rule_files(rules_dir), rules_source=source)
        engine = create_engine(settings)
        try:
            async with create_session_factory(engine)() as session:
                if rules is RulesSource.DB:
                    loaded = await load_approved_rules(session)
                elif rules is RulesSource.LATEST:
                    loaded = await load_latest_rules(session)
                else:
                    loaded = load_rule_files(rules_dir)
                result = run_eligibility_eval(profiles, loaded, rules_source=source)
                if record:
                    config = run_config(result, dataset=dataset, min_accuracy=min_accuracy)
                    await record_run(session, result, git_sha=git_sha, config=config)
                    await session.commit()
                return result
        finally:
            await engine.dispose()

    result = asyncio.run(run())
    report = render_report(result, git_sha=git_sha, dataset=dataset, min_accuracy=min_accuracy)
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"{datetime.now(UTC):%Y-%m-%d}-eligibility.md"
    path.write_bytes(report.encode("utf-8"))
    console.print(report, markup=False, highlight=False)
    console.print(f"Report written to {path}")
    if result.accuracy is None:
        console.print("[yellow]No rules to evaluate: nothing was scored.[/yellow]")
    elif result.accuracy < min_accuracy:
        console.print(f"[red]Accuracy {result.accuracy:.1%} is below {min_accuracy:.0%}.[/red]")
        raise typer.Exit(1)


def current_git_sha() -> str | None:
    if sha := os.environ.get("GITHUB_SHA"):
        return sha
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip() or None
