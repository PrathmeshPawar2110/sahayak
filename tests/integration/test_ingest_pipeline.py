"""M1-T13: two schemes through fetch, parse, chunk, embed, index and rule drafting, end to end.

HTTP is served from recorded fixtures (with ETags, so re-runs exercise conditional GETs), the LLM
and the embedder are the offline fakes, and Postgres is real.
"""

import hashlib
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from sahayak.db.models import EligibilityRule, Language, RuleStatus, Scheme, SchemeChunk
from sahayak.ingestion.fetch import DomainRateLimiter, Fetcher
from sahayak.ingestion.index import IndexStats
from sahayak.ingestion.manifest import Manifest, load_manifest
from sahayak.ingestion.pipeline import SchemeReport, ingest_scheme
from sahayak.llm.fake import FakeCall, FakeLLMProvider
from sahayak.rag.embeddings.fake import FakeEmbeddingProvider

FIXTURES = Path(__file__).parents[1] / "fixtures" / "ingestion"
KISAN_PDF = "https://agri.example.gov.in/kasy/guidelines.pdf"
BAHIN_HI = "https://wcd.example.gov.in/bahin/hi.html"
BAHIN_EN = "https://wcd.example.gov.in/bahin/en.html"

NOT_TAX_PAYER_OR_GOVT = {
    "negate": True,
    "combinator": "any",
    "conditions": [
        {"field": "is_income_tax_payer", "op": "==", "value": True},
        {"field": "is_govt_employee", "op": "==", "value": True},
    ],
}
RULES: dict[str, dict[str, Any]] = {
    "test-kisan": {
        "combinator": "all",
        "items": [
            {"field": "occupation", "op": "in", "value": ["farmer"]},
            {"field": "land_holding_acres", "op": ">", "value": 0},
            NOT_TAX_PAYER_OR_GOVT,
        ],
        "source_refs": [
            {"url": KISAN_PDF, "section": "2. Eligibility"},
            {"url": KISAN_PDF, "section": "3. Exclusions"},
        ],
        "unmapped_criteria": ["Institutional landholders are excluded"],
        "notes": "",
        "confidence": 0.85,
    },
    "mh-test-bahin": {
        "combinator": "all",
        "items": [
            {"field": "gender", "op": "==", "value": "female"},
            {"field": "domicile_state", "op": "==", "value": "MH"},
            {"field": "age", "op": ">=", "value": 21},
            {"field": "age", "op": "<=", "value": 65},
            {"field": "annual_family_income_inr", "op": "<=", "value": 250000},
            NOT_TAX_PAYER_OR_GOVT,
        ],
        "source_refs": [
            {"url": BAHIN_HI, "section": "पात्रता"},
            {"url": BAHIN_HI, "section": "अपात्रता"},
        ],
        "unmapped_criteria": ["Married, widowed, divorced, abandoned or destitute women"],
        "notes": "2.5 lakh converted to 250000.",
        "confidence": 0.8,
    },
}


class Web:
    """Serves fixture files with ETags and answers conditional GETs with 304."""

    def __init__(self) -> None:
        self.pages: dict[str, tuple[bytes, str]] = {
            KISAN_PDF: ((FIXTURES / "kasy-guidelines.pdf").read_bytes(), "application/pdf"),
            BAHIN_HI: ((FIXTURES / "bahin-hi.html").read_bytes(), "text/html; charset=utf-8"),
            BAHIN_EN: ((FIXTURES / "bahin-en.html").read_bytes(), "text/html; charset=utf-8"),
        }
        self.down: set[str] = set()
        self.not_modified: list[str] = []

    def respond(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url in self.down:
            return httpx.Response(503)
        body, content_type = self.pages[url]
        etag = f'"{hashlib.sha256(body).hexdigest()[:16]}"'
        if request.headers.get("If-None-Match") == etag:
            self.not_modified.append(url)
            return httpx.Response(304, headers={"ETag": etag})
        return httpx.Response(
            200, content=body, headers={"Content-Type": content_type, "ETag": etag}
        )


async def no_sleep(_seconds: float) -> None:
    return None


def rule_reply(call: FakeCall) -> dict[str, Any]:
    prompt = call.messages[0].content
    return next(rule for scheme_id, rule in RULES.items() if f"(`{scheme_id}`)" in prompt)


@pytest.fixture
def manifest() -> Manifest:
    return load_manifest(FIXTURES / "manifest.yaml")


@pytest.fixture
def web() -> Iterator[Web]:
    site = Web()
    with respx.mock(assert_all_called=False) as router:
        router.get(url__regex=r".*/robots\.txt$").respond(404)
        router.get(url__regex=r"https://(agri|wcd)\.example\.gov\.in/.*").mock(
            side_effect=site.respond
        )
        yield site


@pytest.fixture
async def fetcher(tmp_path: Path) -> AsyncIterator[Fetcher]:
    limiter = DomainRateLimiter(0.0, sleep=no_sleep)
    async with Fetcher(tmp_path, user_agent="SahayakBot/test", limiter=limiter) as fetcher:
        yield fetcher


async def ingest_all(
    session: AsyncSession,
    manifest: Manifest,
    fetcher: Fetcher,
    embedder: FakeEmbeddingProvider,
    llm: FakeLLMProvider | None,
    **options: bool,
) -> dict[str, SchemeReport]:
    reports = {}
    for spec in manifest.schemes:
        reports[spec.id] = await ingest_scheme(
            session, spec, fetcher=fetcher, embedder=embedder, llm=llm, **options
        )
    return reports


async def snapshot(session: AsyncSession) -> dict[str, Any]:
    chunks = await session.execute(
        select(SchemeChunk.id, SchemeChunk.scheme_id, SchemeChunk.chunk_index).order_by(
            SchemeChunk.id
        )
    )
    rules = await session.execute(
        select(EligibilityRule.scheme_id, EligibilityRule.version, EligibilityRule.status).order_by(
            EligibilityRule.scheme_id, EligibilityRule.version
        )
    )
    return {"chunks": chunks.all(), "rules": rules.all()}


@pytest.mark.usefixtures("web")
async def test_two_schemes_end_to_end_and_rerun_is_idempotent(
    db_session: AsyncSession, manifest: Manifest, fetcher: Fetcher, web: Web
) -> None:
    embedder, llm = FakeEmbeddingProvider(), FakeLLMProvider(rule_reply)

    first = await ingest_all(db_session, manifest, fetcher, embedder, llm)

    assert {r.scheme_id: (r.fetched, r.failed_sources, r.rule) for r in first.values()} == {
        "test-kisan": (1, [], "created"),
        "mh-test-bahin": (2, [], "created"),
    }
    assert all(r.ok and r.index.added > 0 and r.warnings == [] for r in first.values())

    schemes = {s.id: s for s in (await db_session.scalars(select(Scheme))).all()}
    assert set(schemes) == {"test-kisan", "mh-test-bahin"}
    assert schemes["mh-test-bahin"].state == "MH"
    assert all(s.content_hash and s.last_verified_at for s in schemes.values())

    # Chunks carry their section, language and source, and both search paths find them.
    sections = set(
        (await db_session.execute(select(SchemeChunk.scheme_id, SchemeChunk.section))).all()
    )
    assert ("test-kisan", "3. Exclusions") in sections
    assert ("mh-test-bahin", "अपात्रता") in sections
    hindi_sources = await db_session.scalars(
        select(SchemeChunk.source_url).where(SchemeChunk.language == Language.HI).distinct()
    )
    assert hindi_sources.all() == [BAHIN_HI]
    english_hit = await db_session.scalar(
        select(SchemeChunk.section).where(
            SchemeChunk.tsv.op("@@")(text("plainto_tsquery('english', 'cultivable lands')"))
        )
    )
    hindi_hit = await db_session.scalar(
        select(SchemeChunk.section).where(
            SchemeChunk.tsv.op("@@")(text("plainto_tsquery('simple', 'आयकर')"))
        )
    )
    nearest = await db_session.scalar(
        select(SchemeChunk.section).order_by(
            SchemeChunk.embedding.cosine_distance(
                embedder.vector("not eligible: income tax, government employees")
            )
        )
    )
    assert (english_hit, hindi_hit, nearest) == ("2. Eligibility", "अपात्रता", "3. Exclusions")

    # One draft per scheme, saved for review, with the text the model read.
    drafts = (await db_session.scalars(select(EligibilityRule))).all()
    assert sorted((r.scheme_id, r.version, r.status) for r in drafts) == [
        ("mh-test-bahin", 1, RuleStatus.DRAFT),
        ("test-kisan", 1, RuleStatus.DRAFT),
    ]
    assert all(r.extraction and r.extraction["issues"] == [] for r in drafts)
    bahin_prompt = next(c for c in llm.calls if "`mh-test-bahin`" in c.messages[0].content)
    assert "ढाई लाख रुपये" in bahin_prompt.messages[0].content  # Hindi source text
    assert "anganwadi" in bahin_prompt.messages[0].content  # and the English page

    # Re-run: every source answers 304, nothing is embedded, drafted or duplicated.
    before = await snapshot(db_session)
    embedded, drafted = len(embedder.calls), len(llm.calls)

    second = await ingest_all(db_session, manifest, fetcher, embedder, llm)

    assert sorted(web.not_modified) == sorted([KISAN_PDF, BAHIN_HI, BAHIN_EN])
    assert {r.scheme_id: (r.index.added, r.index.removed, r.rule) for r in second.values()} == {
        "test-kisan": (0, 0, "unchanged"),
        "mh-test-bahin": (0, 0, "unchanged"),
    }
    assert (len(embedder.calls), len(llm.calls)) == (embedded, drafted)
    assert await snapshot(db_session) == before


@pytest.mark.usefixtures("web")
async def test_changed_source_updates_chunks_and_drafts_a_new_version(
    db_session: AsyncSession, manifest: Manifest, fetcher: Fetcher, web: Web
) -> None:
    embedder, llm = FakeEmbeddingProvider(), FakeLLMProvider(rule_reply)
    await ingest_all(db_session, manifest, fetcher, embedder, llm)
    body, content_type = web.pages[BAHIN_EN]
    web.pages[BAHIN_EN] = (
        body.replace(b"anganwadi centre", b"gram panchayat office"),
        content_type,
    )

    reports = await ingest_all(db_session, manifest, fetcher, embedder, llm)

    bahin = reports["mh-test-bahin"]
    assert (bahin.index.added, bahin.index.removed) == (1, 1)
    assert (bahin.rule, bahin.rule_version) == ("created", 2)
    assert reports["test-kisan"].rule == "unchanged"
    statuses = await db_session.execute(
        select(EligibilityRule.version, EligibilityRule.status)
        .where(EligibilityRule.scheme_id == "mh-test-bahin")
        .order_by(EligibilityRule.version)
    )
    assert statuses.all() == [(1, RuleStatus.ARCHIVED), (2, RuleStatus.DRAFT)]


@pytest.mark.usefixtures("web")
async def test_unreachable_source_keeps_old_chunks_and_drafts_no_rule(
    db_session: AsyncSession, manifest: Manifest, fetcher: Fetcher, web: Web
) -> None:
    embedder = FakeEmbeddingProvider()
    await ingest_all(db_session, manifest, fetcher, embedder, None)
    count = select(func.count()).select_from(SchemeChunk)
    chunks_before = await db_session.scalar(count)
    web.down.add(BAHIN_EN)
    llm = FakeLLMProvider(rule_reply)

    reports = await ingest_all(db_session, manifest, fetcher, embedder, llm)

    bahin = reports["mh-test-bahin"]
    assert bahin.ok
    assert bahin.failed_sources == [f"{BAHIN_EN}: HTTP 503 for {BAHIN_EN}"]
    assert bahin.rule == "incomplete sources"
    assert reports["test-kisan"].rule == "created"
    assert await db_session.scalar(count) == chunks_before
    assert all("`mh-test-bahin`" not in c.messages[0].content for c in llm.calls)


@pytest.mark.usefixtures("web")
async def test_reindex_re_embeds_every_chunk(
    db_session: AsyncSession, manifest: Manifest, fetcher: Fetcher
) -> None:
    embedder = FakeEmbeddingProvider()
    first = await ingest_all(db_session, manifest, fetcher, embedder, None)

    again = await ingest_all(db_session, manifest, fetcher, embedder, None, reindex=True)

    for scheme_id, report in again.items():
        assert report.index == IndexStats(added=first[scheme_id].index.added)
        assert report.rule == "skipped"
