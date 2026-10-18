from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from sahayak.agent.catalogue import DbCatalogueSource, SessionScope
from sahayak.agent.passages import RetrieverPassageSearch
from sahayak.db.models import EligibilityRule, RuleStatus, SchemeStatus
from sahayak.rag.embeddings.fake import FakeEmbeddingProvider
from sahayak.rag.retriever import HybridRetriever
from tests.integration.rag_corpus import load_corpus

AGE = {"all": [{"field": "age", "op": ">=", "value": 18}]}


def _scope(session: AsyncSession) -> SessionScope:
    @asynccontextmanager
    async def scope() -> AsyncIterator[AsyncSession]:
        yield session

    return scope


@pytest.fixture
async def corpus(db_session: AsyncSession) -> None:
    await load_corpus(db_session, FakeEmbeddingProvider())
    rules = [
        ("farmer-support", 1, RuleStatus.ARCHIVED),
        ("farmer-support", 2, RuleStatus.APPROVED),
        ("farmer-support", 3, RuleStatus.DRAFT),  # an amendment awaiting review
        ("worker-pension", 1, RuleStatus.DRAFT),
        ("worker-pension", 2, RuleStatus.DRAFT),
        ("mh-women", 1, RuleStatus.DRAFT),  # active without an approved rule
        ("old-loan", 1, RuleStatus.APPROVED),
    ]
    for scheme_id, version, status in rules:
        db_session.add(
            EligibilityRule(scheme_id=scheme_id, version=version, status=status, logic=AGE)
        )
    await db_session.flush()


@pytest.mark.usefixtures("corpus")
@pytest.mark.parametrize("show_provisional", [True, False])
async def test_catalogue_picks_the_rule_by_status(
    db_session: AsyncSession, show_provisional: bool
) -> None:
    source = DbCatalogueSource(_scope(db_session), show_provisional=show_provisional)
    catalogue = await source.load()
    assert "not-a-scheme" not in catalogue  # archived
    versions = {s.id: s.rule.version if s.rule else None for s in catalogue}
    assert versions == {
        "farmer-support": 2,
        "worker-pension": 2 if show_provisional else None,
        "mh-women": None,
        "ka-housing": None,
        "old-loan": None,  # discontinued
    }
    pension = catalogue.get("worker-pension")
    assert pension is not None
    assert pension.status is SchemeStatus.PROVISIONAL


@pytest.mark.usefixtures("corpus")
async def test_catalogue_is_cached_for_its_ttl(db_session: AsyncSession) -> None:
    clock = [0.0]
    source = DbCatalogueSource(
        _scope(db_session), show_provisional=True, ttl_sec=60, monotonic=lambda: clock[0]
    )
    first = await source.load()
    clock[0] = 59
    assert await source.load() is first
    clock[0] = 61
    assert await source.load() is not first


@pytest.mark.usefixtures("corpus")
async def test_passage_search_returns_evidence_chunks(db_session: AsyncSession) -> None:
    search = RetrieverPassageSearch(_scope(db_session), HybridRetriever(FakeEmbeddingProvider()))
    chunks = await search.search("monthly pension for workers", state="MH", top_k=3)
    assert len(chunks) == 3
    assert chunks[0].scheme_id == "worker-pension"
    assert chunks[0].language == "en"
    restricted = await search.search("support", state=None, scheme_ids=["mh-women"], top_k=5)
    assert {c.scheme_id for c in restricted} == {"mh-women"}
    assert await search.search("support", state=None, scheme_ids=[]) == []
    assert await search.search("  ", state=None) == []
