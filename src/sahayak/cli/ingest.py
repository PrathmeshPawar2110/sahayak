"""`sahayak ingest`: fetch, parse, chunk, embed and index schemes, then draft their rules."""

import asyncio
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated

import structlog
import typer
from rich.console import Console
from rich.table import Table

from sahayak.config import Settings, get_settings
from sahayak.db.session import create_engine, create_session_factory
from sahayak.ingestion.fetch import Fetcher
from sahayak.ingestion.manifest import SchemeSpec, load_manifest
from sahayak.ingestion.pipeline import SchemeReport, ingest_scheme
from sahayak.llm.base import LLMProvider
from sahayak.llm.factory import LLMConfigError, create_llm_provider
from sahayak.observability.logging import configure_logging
from sahayak.rag.embeddings import EmbeddingProvider, create_embedding_provider

DEFAULT_MANIFEST = Path("data/schemes/manifest.yaml")
DEFAULT_RAW_DIR = Path("data/raw")

log = structlog.get_logger(__name__)
console = Console()


def ingest_command(
    scheme: Annotated[
        list[str] | None,
        typer.Option("--scheme", "-s", help="Scheme id to ingest (repeatable). Default: all."),
    ] = None,
    manifest: Annotated[Path, typer.Option(help="Scheme manifest.")] = DEFAULT_MANIFEST,
    raw_dir: Annotated[Path, typer.Option(help="Where fetched documents are kept.")] = (
        DEFAULT_RAW_DIR
    ),
    skip_rules: Annotated[bool, typer.Option(help="Index only; draft no rules.")] = False,
    reindex: Annotated[
        bool,
        typer.Option(help="Delete and re-embed the chunks (after changing the embedding model)."),
    ] = False,
    redraft: Annotated[
        bool, typer.Option(help="Draft new rules even when the source text is unchanged.")
    ] = False,
) -> None:
    """Ingest schemes from the manifest. Re-running only processes what changed."""
    settings = get_settings()
    configure_logging(settings)
    specs = select_schemes(load_manifest(manifest).schemes, scheme)
    try:
        llm = None if skip_rules else create_llm_provider(settings)
    except LLMConfigError as exc:
        console.print(f"[red]{exc}[/red] (or pass --skip-rules to index without drafting rules)")
        raise typer.Exit(2) from exc
    embedder = create_embedding_provider(settings)

    reports = asyncio.run(
        _run(settings, specs, raw_dir, embedder, llm, reindex=reindex, redraft=redraft)
    )
    print_reports(console, reports)
    if not all(report.ok for report in reports):
        raise typer.Exit(1)


def select_schemes(specs: Sequence[SchemeSpec], ids: Sequence[str] | None) -> list[SchemeSpec]:
    if not ids:
        return list(specs)
    by_id = {spec.id: spec for spec in specs}
    unknown = [scheme_id for scheme_id in ids if scheme_id not in by_id]
    if unknown:
        raise typer.BadParameter(
            f"not in the manifest: {', '.join(unknown)}", param_hint="--scheme"
        )
    return [by_id[scheme_id] for scheme_id in dict.fromkeys(ids)]


async def _run(
    settings: Settings,
    specs: Sequence[SchemeSpec],
    raw_dir: Path,
    embedder: EmbeddingProvider,
    llm: LLMProvider | None,
    *,
    reindex: bool,
    redraft: bool,
) -> list[SchemeReport]:
    engine = create_engine(settings)
    sessions = create_session_factory(engine)
    reports: list[SchemeReport] = []
    try:
        async with Fetcher(
            raw_dir,
            user_agent=settings.crawler_user_agent,
            rate_limit=settings.crawler_rate_limit_per_domain,
        ) as fetcher:
            for spec in specs:
                async with sessions() as session:
                    try:
                        report = await ingest_scheme(
                            session,
                            spec,
                            fetcher=fetcher,
                            embedder=embedder,
                            llm=llm,
                            reindex=reindex,
                            force_rules=redraft,
                        )
                    except Exception as exc:  # one broken scheme must not stop the batch
                        log.exception("scheme_ingest_failed", scheme_id=spec.id)
                        report = SchemeReport(spec.id, rule=f"failed: {type(exc).__name__}")
                reports.append(report)
    finally:
        await engine.dispose()
    return reports


def print_reports(console: Console, reports: Sequence[SchemeReport]) -> None:
    table = Table(title="Ingestion")
    for column in ("Scheme", "Sources", "Chunks +new =kept -gone", "Rule", "Warnings"):
        table.add_column(column)
    for report in reports:
        total = report.fetched + len(report.failed_sources)
        rule = report.rule + (f" (v{report.rule_version})" if report.rule_version else "")
        stats = report.index
        table.add_row(
            report.scheme_id,
            f"{report.fetched}/{total}",
            f"+{stats.added} ={stats.kept} -{stats.removed}",
            rule if report.ok else f"[red]{rule}[/red]",
            str(len(report.warnings)),
        )
    console.print(table)
    for report in reports:
        for problem in report.failed_sources:
            console.print(f"[red]{report.scheme_id}: fetch failed: {problem}[/red]", soft_wrap=True)
        for warning in report.warnings:
            console.print(f"[yellow]{report.scheme_id}: {warning}[/yellow]", soft_wrap=True)
