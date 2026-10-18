"""Single-signal searches over `scheme_chunks`: vector similarity and full text (TRD §7, §10).

Each search returns its own ranked list. `sahayak.rag.retriever` fuses them. Both apply the same
`SearchFilter`, so a chunk is only returned while its scheme may be shown: by default the scheme
is `active` or `provisional` (TRD §8A) and is either central or belongs to the user's state.

Nothing here writes, and nothing commits: callers pass their own session.
"""

import uuid
from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import ColumnElement, Select, Text, case, cast, func, literal, select
from sqlalchemy.dialects.postgresql import REGCONFIG, TSQUERY
from sqlalchemy.ext.asyncio import AsyncSession

from sahayak.db.models import Language, Scheme, SchemeChunk, SchemeLevel, SchemeStatus

# Statuses a scheme may have for its text to be retrieved. Discontinued schemes are found only
# when asked about by name (M9), and archived ones never.
RETRIEVABLE_STATUSES = frozenset({SchemeStatus.ACTIVE, SchemeStatus.PROVISIONAL})

# `ts_rank` normalisation 1: divide by 1 + log(chunk length), so long chunks don't win on
# repetition alone.
RANK_NORMALIZATION = 1


@dataclass(frozen=True)
class SearchFilter:
    """Which chunks a search may return.

    `state` is the user's state (ISO code such as "MH"): central schemes plus that state's schemes
    match. None means the state isn't known yet, and every state's schemes match: retrieval only
    finds text, and eligibility (the rule engine) decides who qualifies.
    `scheme_ids` restricts the search to those schemes, for evidence about known schemes.
    """

    state: str | None = None
    statuses: frozenset[SchemeStatus] = RETRIEVABLE_STATUSES
    scheme_ids: frozenset[str] | None = None

    @classmethod
    def for_user(
        cls,
        state: str | None = None,
        *,
        scheme_ids: Collection[str] | None = None,
        statuses: Collection[SchemeStatus] = RETRIEVABLE_STATUSES,
    ) -> "SearchFilter":
        return cls(
            state=state.strip().upper() if state else None,
            statuses=frozenset(statuses),
            scheme_ids=None if scheme_ids is None else frozenset(scheme_ids),
        )


NO_FILTER = SearchFilter()

# Every search selects the chunk, its scheme's last verification and the hit's score.
type _HitSelect = Select[SchemeChunk, datetime | None, float]


@dataclass(frozen=True)
class SearchHit:
    """One chunk found by one search. `score` is only comparable within the same search."""

    chunk_id: uuid.UUID
    scheme_id: str
    source_url: str
    section: str
    language: Language
    text: str
    scheme_last_verified_at: datetime | None
    score: float


def _filtered[*Ts](statement: Select[*Ts], search_filter: SearchFilter) -> Select[*Ts]:
    statement = statement.join(Scheme, Scheme.id == SchemeChunk.scheme_id).where(
        Scheme.status.in_(sorted(search_filter.statuses))
    )
    if search_filter.state is not None:
        statement = statement.where(
            (Scheme.level == SchemeLevel.CENTRAL) | (Scheme.state == search_filter.state)
        )
    if search_filter.scheme_ids is not None:
        statement = statement.where(SchemeChunk.scheme_id.in_(sorted(search_filter.scheme_ids)))
    return statement


def _hit(chunk: SchemeChunk, last_verified_at: datetime | None, score: float) -> SearchHit:
    return SearchHit(
        chunk_id=chunk.id,
        scheme_id=chunk.scheme_id,
        source_url=chunk.source_url,
        section=chunk.section,
        language=Language(chunk.language),
        text=chunk.text,
        scheme_last_verified_at=last_verified_at,
        score=score,
    )


async def vector_search(
    session: AsyncSession,
    query_vector: list[float],
    *,
    search_filter: SearchFilter = NO_FILTER,
    limit: int = 20,
) -> list[SearchHit]:
    """Chunks nearest to `query_vector` by cosine distance; `score` is the cosine similarity.

    Without `scheme_ids` this uses the HNSW index, which returns at most `hnsw.ef_search` (40 by
    default) neighbours before the filter is applied, so a selective filter can return fewer than
    `limit` hits. With `scheme_ids` the search is exact: the scheme filter runs first and only
    those schemes' chunks are ranked, so evidence for a named scheme is never crowded out.
    """
    if limit <= 0:
        return []
    if search_filter.scheme_ids is not None:
        return await _exact_vector_search(session, query_vector, search_filter, limit)
    distance: ColumnElement[float] = SchemeChunk.embedding.cosine_distance(query_vector)
    statement: _HitSelect = _filtered(
        select(SchemeChunk, Scheme.last_verified_at, (1 - distance).label("score")), search_filter
    )
    rows = await session.execute(statement.order_by(distance, SchemeChunk.id).limit(limit))
    return [_hit(row[0], row[1], float(row[2])) for row in rows]


async def _exact_vector_search(
    session: AsyncSession, query_vector: list[float], search_filter: SearchFilter, limit: int
) -> list[SearchHit]:
    # A MATERIALIZED CTE is an optimisation fence: Postgres filters and computes every distance
    # first, then sorts those rows exactly, instead of walking the HNSW index and filtering after.
    distance: ColumnElement[float] = SchemeChunk.embedding.cosine_distance(query_vector)
    candidates = (
        _filtered(select(SchemeChunk.id, distance.label("distance")), search_filter)
        .cte("candidates")
        .prefix_with("MATERIALIZED")
    )
    statement: _HitSelect = (
        select(SchemeChunk, Scheme.last_verified_at, (1 - candidates.c.distance).label("score"))
        .join(candidates, candidates.c.id == SchemeChunk.id)
        .join(Scheme, Scheme.id == SchemeChunk.scheme_id)
        .order_by(candidates.c.distance, SchemeChunk.id)
        .limit(limit)
    )
    rows = await session.execute(statement)
    return [_hit(row[0], row[1], float(row[2])) for row in rows]


def any_term_query(config: str, query: str) -> ColumnElement[str]:
    """A tsquery matching chunks that contain *any* of the query's terms.

    `plainto_tsquery` normalises the words with `config` (stemming, stop words) but joins them
    with AND, which a natural-language question rarely satisfies in full; the terms are joined
    with OR instead, and ranking rewards chunks that match more of them. Lexemes are quoted in
    the tsquery text, so replacing the ` & ` operators can't touch their contents.
    """
    terms = func.plainto_tsquery(cast(literal(config), REGCONFIG), query)
    return cast(func.replace(cast(terms, Text), " & ", " | "), TSQUERY)


async def fulltext_search(
    session: AsyncSession,
    query: str,
    *,
    search_filter: SearchFilter = NO_FILTER,
    limit: int = 20,
) -> list[SearchHit]:
    """Chunks matching the query's words, ranked by `ts_rank` (normalised by chunk length).

    English chunks are indexed with the `english` configuration and matched against the query
    normalised the same way; Hindi and Marathi chunks use `simple` (Postgres has no stemmer for
    either), so they match exact word forms. A query with only stop words matches nothing.
    """
    if limit <= 0 or not query.strip():
        return []
    english, simple = any_term_query("english", query), any_term_query("simple", query)
    is_english = SchemeChunk.language == Language.EN
    tsv = SchemeChunk.tsv
    matches = (is_english & tsv.op("@@")(english)) | (~is_english & tsv.op("@@")(simple))
    rank: ColumnElement[float] = case(
        (is_english, func.ts_rank(tsv, english, RANK_NORMALIZATION)),
        else_=func.ts_rank(tsv, simple, RANK_NORMALIZATION),
    )
    statement: _HitSelect = _filtered(
        select(SchemeChunk, Scheme.last_verified_at, rank.label("score")).where(matches),
        search_filter,
    )
    rows = await session.execute(statement.order_by(rank.desc(), SchemeChunk.id).limit(limit))
    return [_hit(row[0], row[1], float(row[2])) for row in rows]
