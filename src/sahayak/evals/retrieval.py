"""The retrieval eval (M3-T05): does hybrid search find the right scheme text?

`evals/retrieval.jsonl` holds realistic user queries in Hindi, Marathi and English, each with one
or more targets. A target is a scheme, optionally narrowed to the chunks whose section or text
contains one of the given phrases (so a target names the passage that answers the question
without depending on chunk ids, which change when sources are re-chunked).

Metrics, over the top k results (k = 5, TRD §10):
- recall@k: per query, the share of its targets found in the top k; averaged over queries.
- hit@1: the top result matches a target.
- MRR: the mean of 1 / rank of the first result that matches a target (0 if none).
"""

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import fmean
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from sahayak.db.models import EvalRun, Language
from sahayak.rag.retriever import HybridRetriever, RetrievedChunk
from sahayak.rag.search import SearchFilter

SUITE = "retrieval"
QUERIES_PATH = Path("evals/retrieval.jsonl")


class RetrievalTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scheme_id: str
    # Case-insensitive phrases; a chunk matches if its section or text contains any of them.
    # Empty: any chunk of the scheme matches.
    contains: list[str] = Field(default_factory=list)

    def matches(self, chunk: RetrievedChunk) -> bool:
        if chunk.scheme_id != self.scheme_id:
            return False
        if not self.contains:
            return True
        haystack = f"{chunk.section}\n{chunk.text}".casefold()
        return any(phrase.casefold() in haystack for phrase in self.contains)

    def describe(self) -> str:
        if not self.contains:
            return self.scheme_id
        return f"{self.scheme_id} ({' / '.join(repr(p) for p in self.contains)})"


class RetrievalQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^q\d{3}$")
    language: Language
    query: str = Field(min_length=1)
    state: str | None = None  # the user's state, applied as the search filter
    targets: list[RetrievalTarget] = Field(min_length=1)
    note: str = ""


def load_queries(path: Path = QUERIES_PATH) -> list[RetrievalQuery]:
    queries = [
        RetrievalQuery.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    ids = [q.id for q in queries]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise ValueError(f"{path}: duplicate query ids: {', '.join(duplicates)}")
    return queries


@dataclass(frozen=True)
class QueryResult:
    query: RetrievalQuery
    chunks: list[RetrievedChunk]
    searched: list[str]  # the query texts actually searched (original + translation)

    @property
    def found(self) -> list[bool]:
        """Per target: whether any retrieved chunk matches it."""
        return [any(t.matches(c) for c in self.chunks) for t in self.query.targets]

    @property
    def recall(self) -> float:
        return sum(self.found) / len(self.query.targets)

    @property
    def first_relevant_rank(self) -> int | None:
        for rank, chunk in enumerate(self.chunks, start=1):
            if any(t.matches(chunk) for t in self.query.targets):
                return rank
        return None

    @property
    def reciprocal_rank(self) -> float:
        rank = self.first_relevant_rank
        return 0.0 if rank is None else 1.0 / rank


@dataclass(frozen=True)
class Scores:
    queries: int
    recall: float | None
    hit_at_1: float | None
    mrr: float | None

    @classmethod
    def of(cls, results: Sequence[QueryResult]) -> "Scores":
        if not results:
            return cls(0, None, None, None)
        return cls(
            queries=len(results),
            recall=fmean(r.recall for r in results),
            hit_at_1=fmean(r.first_relevant_rank == 1 for r in results),
            mrr=fmean(r.reciprocal_rank for r in results),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "queries": self.queries,
            "recall": self.recall,
            "hit_at_1": self.hit_at_1,
            "mrr": self.mrr,
        }


@dataclass(frozen=True)
class RetrievalEval:
    k: int
    translated: bool  # whether a query translator was used
    results: list[QueryResult]

    @property
    def overall(self) -> Scores:
        return Scores.of(self.results)

    def by_language(self) -> dict[Language, Scores]:
        return {
            language: Scores.of([r for r in self.results if r.query.language is language])
            for language in Language
            if any(r.query.language is language for r in self.results)
        }

    @property
    def misses(self) -> list[QueryResult]:
        """Queries with at least one target outside the top k."""
        return [r for r in self.results if r.recall < 1]

    def metrics(self) -> dict[str, Any]:
        """What goes into `eval_runs.metrics`."""
        return {
            f"recall_at_{self.k}": self.overall.recall,
            "hit_at_1": self.overall.hit_at_1,
            "mrr": self.overall.mrr,
            "queries": self.overall.queries,
            "by_language": {lang.value: s.as_dict() for lang, s in self.by_language().items()},
            "misses": [r.query.id for r in self.misses],
        }


async def run_retrieval_eval(
    session: AsyncSession,
    retriever: HybridRetriever,
    queries: Sequence[RetrievalQuery],
    *,
    translated: bool,
) -> RetrievalEval:
    results = []
    for query in queries:
        retrieval = await retriever.retrieve(
            session,
            query.query,
            language=query.language,
            search_filter=SearchFilter.for_user(query.state),
        )
        results.append(QueryResult(query, retrieval.chunks, retrieval.queries))
    return RetrievalEval(k=retriever.top_k, translated=translated, results=results)


def render_report(
    result: RetrievalEval,
    *,
    git_sha: str | None,
    dataset: Path,
    min_recall: float,
    embedding_model: str,
) -> str:
    overall = result.overall
    k = result.k
    if overall.recall is None:
        status = "NOT RUN: no queries"
    else:
        status = "PASS" if overall.recall >= min_recall else "FAIL"
    lines = [
        f"# Retrieval eval: {datetime.now(UTC):%Y-%m-%d %H:%M} UTC",
        "",
        f"- **Result:** {status} (target recall@{k} >= {min_recall:.2f})",
        f"- **recall@{k}:** {_num(overall.recall)}, **hit@1:** {_num(overall.hit_at_1)}, "
        f"**MRR:** {_num(overall.mrr)}",
        f"- **Queries:** `{dataset.as_posix()}`, {overall.queries}",
        f"- **Embeddings:** {embedding_model}; "
        f"**translation:** {'on' if result.translated else 'off'}",
        f"- **Commit:** {git_sha or 'unknown'}",
        "",
        "## By language",
        "",
        f"| Language | Queries | recall@{k} | hit@1 | MRR |",
        "|---|---|---|---|---|",
    ]
    for language, scores in result.by_language().items():
        lines.append(
            f"| {language.value} | {scores.queries} | {_num(scores.recall)} "
            f"| {_num(scores.hit_at_1)} | {_num(scores.mrr)} |"
        )
    if result.misses:
        lines += [
            "",
            "## Missed targets",
            "",
            "| Query | Lang | Missed | Retrieved (scheme: section) |",
            "|---|---|---|---|",
        ]
        for r in result.misses:
            missed = [
                t.describe() for t, ok in zip(r.query.targets, r.found, strict=True) if not ok
            ]
            got = "<br>".join(f"{c.scheme_id}: {c.section[:50]}" for c in r.chunks) or "-"
            lines.append(
                f"| {r.query.id} | {r.query.language.value} | {'; '.join(missed)} | {_cell(got)} |"
            )
    return "\n".join(lines) + "\n"


def run_config(
    result: RetrievalEval,
    *,
    dataset: Path,
    min_recall: float,
    embedding_model: str,
    translator: str | None,
) -> dict[str, Any]:
    """What goes into `eval_runs.config`: enough to reproduce the run."""
    return {
        "k": result.k,
        "dataset": dataset.as_posix(),
        "dataset_sha256": hashlib.sha256(dataset.read_bytes()).hexdigest(),
        "min_recall": min_recall,
        "embedding_model": embedding_model,
        "translator": translator,
    }


async def record_run(
    session: AsyncSession, result: RetrievalEval, *, git_sha: str | None, config: dict[str, Any]
) -> EvalRun:
    run = EvalRun(suite=SUITE, git_sha=git_sha, config=config, metrics=result.metrics())
    session.add(run)
    await session.flush()
    return run


def _num(value: float | None) -> str:
    return "-" if value is None else f"{value:.3f}"


def _cell(text: str) -> str:
    return text.replace("|", "/").replace("\n", " ")
