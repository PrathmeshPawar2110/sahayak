from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sahayak.db.models import EvalRun
from sahayak.evals.retrieval import RetrievalQuery, record_run, run_retrieval_eval
from sahayak.rag.embeddings.fake import FakeEmbeddingProvider
from sahayak.rag.retriever import HybridRetriever
from tests.integration.rag_corpus import load_corpus

QUERIES = [
    RetrievalQuery.model_validate(row)
    for row in [
        {
            "id": "q001",
            "language": "en",
            "query": "monthly pension for unorganised workers",
            "targets": [{"scheme_id": "worker-pension", "contains": ["pension after 60"]}],
        },
        {
            "id": "q002",
            "language": "mr",
            "query": "महिलांना दरमहा किती रुपये मिळतात",
            "state": "MH",
            "targets": [{"scheme_id": "mh-women"}],
        },
        {
            # The user is in Karnataka, so the Maharashtra scheme is filtered out: a miss.
            "id": "q003",
            "language": "en",
            "query": "women receive 1500 rupees monthly support",
            "state": "KA",
            "targets": [{"scheme_id": "mh-women"}],
        },
    ]
]


async def test_runs_queries_with_their_state_and_records_the_run(
    db_session: AsyncSession,
) -> None:
    embedder = FakeEmbeddingProvider()
    await load_corpus(db_session, embedder)

    result = await run_retrieval_eval(
        db_session, HybridRetriever(embedder), QUERIES, translated=False
    )

    assert [r.recall for r in result.results] == [1.0, 1.0, 0.0]
    assert result.results[0].first_relevant_rank == 1
    assert all(c.scheme_id != "mh-women" for c in result.results[2].chunks)
    assert result.results[1].searched == ["महिलांना दरमहा किती रुपये मिळतात"]
    assert [r.query.id for r in result.misses] == ["q003"]

    run = await record_run(db_session, result, git_sha="abc123", config={"k": 5})
    stored = await db_session.scalar(select(EvalRun).where(EvalRun.id == run.id))
    assert stored is not None
    assert stored.suite == "retrieval"
    assert stored.metrics["recall_at_5"] == 2 / 3
    assert stored.metrics["by_language"]["mr"]["recall"] == 1.0
