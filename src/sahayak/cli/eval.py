"""`sahayak eval --suite eligibility|retrieval|dialogues|all`: score the rule engine, the
retriever and whole conversations with the agent."""

import asyncio
import os
import subprocess
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer
from langgraph.checkpoint.memory import InMemorySaver
from rich.console import Console

from sahayak.agent.catalogue import DbCatalogueSource
from sahayak.agent.checkpoint import checkpoint_serializer
from sahayak.agent.deps import AgentConfig, AgentDeps
from sahayak.agent.graph import build_graph
from sahayak.agent.passages import RetrieverPassageSearch
from sahayak.agent.service import AgentService
from sahayak.config import Settings, get_settings
from sahayak.db.session import create_engine, create_session_factory
from sahayak.eligibility.store import (
    RULES_DIR,
    load_approved_rules,
    load_latest_rules,
    load_rule_files,
)
from sahayak.evals import dialogues as dialogues_eval
from sahayak.evals import retrieval as retrieval_eval
from sahayak.evals.dataset import PROFILES_PATH, load_profiles
from sahayak.evals.eligibility import (
    EligibilityEval,
    record_run,
    render_report,
    run_config,
    run_eligibility_eval,
)
from sahayak.llm.factory import LLMConfigError, create_llm_provider
from sahayak.observability.logging import configure_logging
from sahayak.rag.embeddings import create_embedding_provider
from sahayak.rag.retriever import HybridRetriever
from sahayak.rag.translation import LLMQueryTranslator

console = Console()


class Suite(StrEnum):
    ELIGIBILITY = "eligibility"
    RETRIEVAL = "retrieval"  # needs the ingested index and the embedding model
    DIALOGUES = "dialogues"  # needs the index, the embedding model and the LLM (paid calls)
    ALL = "all"  # every suite


class SimulatedUserKind(StrEnum):
    LLM = "llm"  # plays each persona in natural hi/mr/en (paid calls)
    SCRIPTED = "scripted"  # states facts as field=value


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
    queries: Annotated[
        Path, typer.Option(help="Retrieval queries with targets (JSONL).")
    ] = retrieval_eval.QUERIES_PATH,
    report_dir: Annotated[Path, typer.Option(help="Where to write the reports.")] = Path(
        "evals/reports"
    ),
    min_accuracy: Annotated[
        float, typer.Option(min=0, max=1, help="Fail below this verdict accuracy.")
    ] = 0.9,
    min_recall: Annotated[
        float, typer.Option(min=0, max=1, help="Fail below this retrieval recall@5.")
    ] = 0.85,
    translate: Annotated[
        bool, typer.Option(help="Also search Hindi/Marathi queries in English (calls the LLM).")
    ] = True,
    dialogues: Annotated[
        Path, typer.Option(help="Scripted dialogues (JSONL).")
    ] = dialogues_eval.DIALOGUES_PATH,
    user: Annotated[
        SimulatedUserKind, typer.Option(help="Who plays the citizen in the dialogues.")
    ] = SimulatedUserKind.LLM,
    limit: Annotated[
        int | None, typer.Option(min=1, help="Run only the first N dialogues.")
    ] = None,
    record: Annotated[
        bool, typer.Option(help="Save the metrics as an eval_runs row (needs DATABASE_URL).")
    ] = True,
) -> None:
    """Run eval suites, write a Markdown report per suite and record each run."""
    settings = get_settings()
    configure_logging(settings)
    if rules is RulesSource.LATEST and record and suite is not Suite.RETRIEVAL:
        raise typer.BadParameter("--rules latest previews drafts; add --no-record")
    report_dir.mkdir(parents=True, exist_ok=True)
    git_sha = current_git_sha()
    passed = True
    if suite in (Suite.ELIGIBILITY, Suite.ALL):
        passed &= _eligibility(
            settings,
            rules=rules,
            rules_dir=rules_dir,
            dataset=dataset,
            report_dir=report_dir,
            min_accuracy=min_accuracy,
            record=record,
            git_sha=git_sha,
        )
    if suite in (Suite.RETRIEVAL, Suite.ALL):
        passed &= _retrieval(
            settings,
            queries=queries,
            report_dir=report_dir,
            min_recall=min_recall,
            translate=translate,
            record=record,
            git_sha=git_sha,
        )
    if suite in (Suite.DIALOGUES, Suite.ALL):
        passed &= _dialogues(
            settings,
            dialogues_path=dialogues,
            dataset=dataset,
            user=user,
            limit=limit,
            report_dir=report_dir,
            record=record,
            git_sha=git_sha,
        )
    if not passed:
        raise typer.Exit(1)


def _eligibility(
    settings: Settings,
    *,
    rules: RulesSource,
    rules_dir: Path,
    dataset: Path,
    report_dir: Path,
    min_accuracy: float,
    record: bool,
    git_sha: str | None,
) -> bool:
    profiles = load_profiles(dataset)
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
    _write_report(report_dir, "eligibility", report)
    if result.accuracy is None:
        console.print("[yellow]No rules to evaluate: nothing was scored.[/yellow]")
    elif result.accuracy < min_accuracy:
        console.print(f"[red]Accuracy {result.accuracy:.1%} is below {min_accuracy:.0%}.[/red]")
        return False
    return True


def _retrieval(
    settings: Settings,
    *,
    queries: Path,
    report_dir: Path,
    min_recall: float,
    translate: bool,
    record: bool,
    git_sha: str | None,
) -> bool:
    loaded = retrieval_eval.load_queries(queries)
    translator = None
    if translate:
        try:
            translator = LLMQueryTranslator(create_llm_provider(settings))
        except LLMConfigError as exc:
            raise typer.BadParameter(f"{exc}; or run with --no-translate") from exc
    embedder = create_embedding_provider(settings)
    retriever = HybridRetriever(embedder, translator=translator)
    embedding_model = f"{embedder.name}:{embedder.model}"
    translator_id = None if translator is None else translator.prompt_id

    async def run() -> retrieval_eval.RetrievalEval:
        engine = create_engine(settings)
        try:
            async with create_session_factory(engine)() as session:
                result = await retrieval_eval.run_retrieval_eval(
                    session, retriever, loaded, translated=translator is not None
                )
                if record:
                    config = retrieval_eval.run_config(
                        result,
                        dataset=queries,
                        min_recall=min_recall,
                        embedding_model=embedding_model,
                        translator=translator_id,
                    )
                    await retrieval_eval.record_run(session, result, git_sha=git_sha, config=config)
                    await session.commit()
                return result
        finally:
            await engine.dispose()

    result = asyncio.run(run())
    report = retrieval_eval.render_report(
        result,
        git_sha=git_sha,
        dataset=queries,
        min_recall=min_recall,
        embedding_model=embedding_model,
    )
    _write_report(report_dir, "retrieval", report)
    recall = result.overall.recall
    if recall is None:
        console.print("[yellow]No retrieval queries: nothing was scored.[/yellow]")
    elif recall < min_recall:
        console.print(f"[red]recall@{result.k} {recall:.3f} is below {min_recall:.2f}.[/red]")
        return False
    return True


def _dialogues(
    settings: Settings,
    *,
    dialogues_path: Path,
    dataset: Path,
    user: SimulatedUserKind,
    limit: int | None,
    report_dir: Path,
    record: bool,
    git_sha: str | None,
) -> bool:
    """The agent as the API runs it (catalogue and passages from the database, the configured
    LLM), talking to a simulated user. Fails if any reply named a scheme it shouldn't."""
    loaded = dialogues_eval.load_dialogues(dialogues_path)[:limit]
    personas = {profile.id: profile for profile in load_profiles(dataset)}
    try:
        llm = create_llm_provider(settings)
    except LLMConfigError as exc:
        raise typer.BadParameter(str(exc)) from exc

    def progress(result: dialogues_eval.DialogueResult) -> None:
        status = "ok" if result.exact else "differs"
        console.print(
            f"{result.dialogue.id} {result.dialogue.language}: shown "
            f"{sorted(result.shown)}, {result.questions} questions, {status}"
            + (f", {result.error}" if result.error else ""),
            markup=False,
        )

    async def run() -> dialogues_eval.DialogueEval:
        engine = create_engine(settings)
        sessions = create_session_factory(engine)
        try:
            deps = AgentDeps(
                llm=llm,
                catalogue=DbCatalogueSource(
                    sessions, show_provisional=settings.show_provisional_schemes
                ),
                passages=RetrieverPassageSearch(
                    sessions, HybridRetriever(create_embedding_provider(settings))
                ),
                config=AgentConfig(
                    max_questions=settings.max_questions_per_session,
                    show_provisional=settings.show_provisional_schemes,
                ),
            )
            service = AgentService(build_graph(InMemorySaver(serde=checkpoint_serializer())), deps)
            simulated: dialogues_eval.SimulatedUser = (
                dialogues_eval.LLMSimulatedUser(llm)
                if user is SimulatedUserKind.LLM
                else dialogues_eval.ScriptedUser()
            )
            result = await dialogues_eval.run_dialogue_eval(
                service,
                simulated,
                loaded,
                personas,
                await deps.catalogue.load(),
                agent_model=llm.model,
                on_result=progress,
            )
            if record:
                async with sessions() as session:
                    config = dialogues_eval.run_config(result, dataset=dialogues_path)
                    await dialogues_eval.record_run(session, result, git_sha=git_sha, config=config)
                    await session.commit()
            return result
        finally:
            await engine.dispose()

    result = asyncio.run(run())
    generated_at = f"{datetime.now(UTC):%Y-%m-%d}"
    report = dialogues_eval.render_report(result, git_sha=git_sha, generated_at=generated_at)
    _write_report(report_dir, "dialogues", report)
    metrics = result.metrics()
    if metrics.get("violations"):
        console.print(f"[red]{metrics['violations']} replies named schemes they shouldn't.[/red]")
        return False
    return True


def _write_report(report_dir: Path, suite: str, report: str) -> None:
    path = report_dir / f"{datetime.now(UTC):%Y-%m-%d}-{suite}.md"
    path.write_bytes(report.encode("utf-8"))
    console.print(report, markup=False, highlight=False)
    console.print(f"Report written to {path}")


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
