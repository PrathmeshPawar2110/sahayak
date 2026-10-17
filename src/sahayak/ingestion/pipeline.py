"""One scheme through the whole ingestion pipeline (TRD §8): fetch, parse, chunk, index, draft.

The index is committed before rule drafting starts, so a failed LLM call never loses indexed
chunks. Rules are drafted only when every source was fetched: drafting from part of the text
would produce a misleading draft, and a flaky source would otherwise create a new draft version on
every run.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime

import anyio
import structlog
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from sahayak.db.models import SchemeChunk
from sahayak.ingestion.chunk import chunk_sections
from sahayak.ingestion.fetch import Fetcher, FetchError
from sahayak.ingestion.index import IndexStats, SourceChunks, index_scheme
from sahayak.ingestion.manifest import SchemeSpec
from sahayak.ingestion.parse import parse_document
from sahayak.ingestion.rule_extraction import SourceText, draft_rule
from sahayak.llm.base import LLMError, LLMProvider
from sahayak.rag.embeddings import EmbeddingProvider

log = structlog.get_logger(__name__)


@dataclass
class SchemeReport:
    scheme_id: str
    fetched: int = 0
    failed_sources: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    index: IndexStats = field(default_factory=IndexStats)
    # skipped | no text | incomplete sources | created | unchanged | failed: <reason>
    rule: str = "skipped"
    rule_version: int | None = None

    @property
    def ok(self) -> bool:
        return self.fetched > 0 and not self.rule.startswith("failed")


async def ingest_scheme(
    session: AsyncSession,
    spec: SchemeSpec,
    *,
    fetcher: Fetcher,
    embedder: EmbeddingProvider,
    llm: LLMProvider | None,
    reindex: bool = False,
    force_rules: bool = False,
) -> SchemeReport:
    """Ingest one scheme and commit. `llm=None` skips rule drafting."""
    report = SchemeReport(spec.id)
    indexed: list[SourceChunks] = []
    texts: list[SourceText] = []
    for source in spec.sources:
        url = str(source.url)
        try:
            document = await fetcher.fetch(url)
        except FetchError as exc:
            report.failed_sources.append(f"{url}: {exc}")
            continue
        report.fetched += 1
        data = await document.read_bytes()
        parsed = await anyio.to_thread.run_sync(parse_document, document.kind, data)
        report.warnings.extend(f"{url}: {warning}" for warning in parsed.warnings)
        # A garbled source indexes as empty, so earlier chunks of it are removed (D-012).
        sections = [] if parsed.garbled else parsed.sections
        chunks = chunk_sections(
            sections, scheme_id=spec.id, source_url=url, language=source.language
        )
        indexed.append(SourceChunks(url=url, sha256=document.sha256, chunks=chunks))
        if sections:
            texts.append(SourceText(url=url, sections=sections))

    if reindex:
        await session.execute(delete(SchemeChunk).where(SchemeChunk.scheme_id == spec.id))
    report.index = await index_scheme(
        session, embedder, spec, indexed, verified_at=datetime.now(UTC)
    )
    await session.commit()

    if llm is not None:
        await _draft(session, llm, spec, texts, report, force=force_rules)
    log.info(
        "scheme_ingested",
        scheme_id=spec.id,
        fetched=report.fetched,
        failed=len(report.failed_sources),
        added=report.index.added,
        kept=report.index.kept,
        removed=report.index.removed,
        rule=report.rule,
    )
    return report


async def _draft(
    session: AsyncSession,
    llm: LLMProvider,
    spec: SchemeSpec,
    texts: list[SourceText],
    report: SchemeReport,
    *,
    force: bool,
) -> None:
    if report.failed_sources:
        report.rule = "incomplete sources"
        return
    try:
        outcome = await draft_rule(session, llm, spec, texts, force=force)
    except LLMError as exc:
        await session.rollback()
        report.rule = f"failed: {type(exc).__name__}: {exc}"
        return
    await session.commit()
    report.rule = outcome.status.replace("_", " ")
    report.rule_version = outcome.rule.version if outcome.rule else None
