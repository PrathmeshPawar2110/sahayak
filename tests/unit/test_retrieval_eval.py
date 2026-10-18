import json
import re
import uuid
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from sahayak.cli import app
from sahayak.cli import eval as eval_cli
from sahayak.config import Settings
from sahayak.db.models import Language
from sahayak.evals import retrieval as retrieval_eval
from sahayak.evals.retrieval import (
    QueryResult,
    RetrievalEval,
    RetrievalQuery,
    RetrievalTarget,
    Scores,
    load_queries,
    render_report,
    run_config,
)
from sahayak.ingestion.manifest import load_manifest
from sahayak.rag.retriever import RetrievedChunk

runner = CliRunner()


def chunk(scheme_id: str, section: str = "Benefits", text: str = "Some text.") -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid.uuid5(uuid.NAMESPACE_DNS, f"{scheme_id}:{section}:{text}"),
        scheme_id=scheme_id,
        source_url=f"https://example.gov.in/{scheme_id}",
        section=section,
        language=Language.EN,
        text=text,
        scheme_last_verified_at=None,
        score=0.1,
    )


def query(
    qid: str = "q001", language: str = "en", targets: list[dict[str, Any]] | None = None
) -> RetrievalQuery:
    return RetrievalQuery.model_validate(
        {
            "id": qid,
            "language": language,
            "query": "pension for workers",
            "targets": targets or [{"scheme_id": "pm-sym"}],
        }
    )


def test_target_matches_scheme_and_any_phrase_in_section_or_text() -> None:
    anywhere = RetrievalTarget(scheme_id="ssy")
    phrased = RetrievalTarget(scheme_id="ssy", contains=["Which Documents", "kyc"])

    assert anywhere.matches(chunk("ssy"))
    assert not anywhere.matches(chunk("apy"))
    assert phrased.matches(chunk("ssy", section="Which documents are required?"))
    assert phrased.matches(chunk("ssy", text="Bring the KYC form."))
    assert not phrased.matches(chunk("ssy", text="Interest is compounded yearly."))
    assert not phrased.matches(chunk("apy", text="Bring the KYC form."))
    assert phrased.describe() == "ssy ('Which Documents' / 'kyc')"
    assert anywhere.describe() == "ssy"


def test_query_result_scores_recall_and_first_relevant_rank() -> None:
    q = query(targets=[{"scheme_id": "pm-sym"}, {"scheme_id": "apy"}])
    result = QueryResult(q, [chunk("ssy"), chunk("pm-sym"), chunk("pm-sym", "Eligibility")], [])

    assert result.found == [True, False]
    assert result.recall == 0.5
    assert result.first_relevant_rank == 2
    assert result.reciprocal_rank == 0.5


def test_query_result_with_nothing_relevant() -> None:
    result = QueryResult(query(), [chunk("ssy")], [])

    assert result.recall == 0
    assert result.first_relevant_rank is None
    assert result.reciprocal_rank == 0


def sample_eval() -> RetrievalEval:
    hit = QueryResult(query("q001"), [chunk("pm-sym")], ["pension for workers"])
    second = QueryResult(query("q002", "hi"), [chunk("apy"), chunk("pm-sym")], ["a", "b"])
    miss = QueryResult(query("q003", "hi"), [chunk("apy")], ["c"])
    return RetrievalEval(k=5, translated=True, results=[hit, second, miss])


def test_scores_overall_and_by_language() -> None:
    result = sample_eval()

    assert result.overall == Scores(queries=3, recall=2 / 3, hit_at_1=1 / 3, mrr=0.5)
    by_language = result.by_language()
    assert list(by_language) == [Language.HI, Language.EN]
    assert by_language[Language.HI] == Scores(queries=2, recall=0.5, hit_at_1=0, mrr=0.25)
    assert [r.query.id for r in result.misses] == ["q003"]
    assert Scores.of([]) == Scores(0, None, None, None)


def test_metrics_and_config_are_json_ready(tmp_path: Path) -> None:
    dataset = tmp_path / "queries.jsonl"
    dataset.write_text("{}\n", encoding="utf-8")
    result = sample_eval()

    metrics = json.loads(json.dumps(result.metrics()))
    config = run_config(
        result,
        dataset=dataset,
        min_recall=0.85,
        embedding_model="fake:hashed-bag-of-words",
        translator="query_translation@v1",
    )

    assert metrics["recall_at_5"] == pytest.approx(2 / 3)
    assert metrics["by_language"]["hi"]["queries"] == 2
    assert metrics["misses"] == ["q003"]
    assert config["k"] == 5
    assert config["translator"] == "query_translation@v1"
    assert len(config["dataset_sha256"]) == 64


def test_report_shows_status_languages_and_misses() -> None:
    report = render_report(
        sample_eval(),
        git_sha="abc123",
        dataset=Path("evals/retrieval.jsonl"),
        min_recall=0.85,
        embedding_model="fake:hashed",
    )

    assert "**Result:** FAIL (target recall@5 >= 0.85)" in report
    assert "**recall@5:** 0.667, **hit@1:** 0.333, **MRR:** 0.500" in report
    assert "**translation:** on" in report
    assert "| hi | 2 | 0.500 | 0.000 | 0.250 |" in report
    assert "| q003 | hi | pm-sym | apy: Benefits |" in report


def test_report_without_queries_says_not_run() -> None:
    report = render_report(
        RetrievalEval(k=5, translated=False, results=[]),
        git_sha=None,
        dataset=Path("q.jsonl"),
        min_recall=0.85,
        embedding_model="fake",
    )

    assert "NOT RUN" in report
    assert "## Missed targets" not in report


def test_load_queries_rejects_duplicates_and_bad_rows(tmp_path: Path) -> None:
    path = tmp_path / "q.jsonl"
    row = query().model_dump_json()
    path.write_text(f"{row}\n\n{row}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate query ids: q001"):
        load_queries(path)

    path.write_text('{"id": "q001", "language": "en", "query": "x", "targets": []}\n', "utf-8")
    with pytest.raises(ValueError, match="targets"):
        load_queries(path)


def test_committed_query_set_covers_every_language_and_scheme() -> None:
    queries = load_queries()
    schemes = {s.id for s in load_manifest(Path("data/schemes/manifest.yaml")).schemes}

    assert len(queries) >= 30
    assert {q.language for q in queries} == set(Language)
    assert all(sum(q.language is lang for q in queries) >= 10 for lang in Language)
    targeted = {t.scheme_id for q in queries for t in q.targets}
    assert targeted == schemes  # every target is a real scheme, and every scheme is targeted
    assert [q.id for q in queries] == [f"q{i:03d}" for i in range(1, len(queries) + 1)]


# --- CLI


def compact(output: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m|[\s│]", "", output)


@pytest.fixture
def no_llm_settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    settings = Settings(_env_file=None, llm_provider="anthropic", log_format="json")
    monkeypatch.setattr(eval_cli, "get_settings", lambda: settings)
    return settings


def test_translation_needs_a_configured_llm(no_llm_settings: Settings) -> None:
    result = runner.invoke(app, ["eval", "--suite", "retrieval", "--no-record"])

    assert result.exit_code == 2
    assert "ANTHROPIC_API_KEY" in result.output
    assert "--no-translate" in compact(result.output)


@pytest.mark.parametrize(("min_recall", "exit_code"), [("0.6", 0), ("0.7", 1)])
def test_retrieval_suite_fails_below_the_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    no_llm_settings: Settings,
    min_recall: str,
    exit_code: int,
) -> None:
    from sahayak.rag.embeddings.fake import FakeEmbeddingProvider

    monkeypatch.setattr(eval_cli, "create_embedding_provider", lambda _s: FakeEmbeddingProvider())

    class FakeEngine:
        async def dispose(self) -> None:
            return None

    class FakeSession:
        async def __aenter__(self) -> "FakeSession":
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

    async def fake_run(*_args: object, translated: bool) -> RetrievalEval:
        assert translated is False
        return sample_eval()

    monkeypatch.setattr(eval_cli, "create_engine", lambda _s: FakeEngine())
    monkeypatch.setattr(eval_cli, "create_session_factory", lambda _e: FakeSession)
    monkeypatch.setattr(retrieval_eval, "run_retrieval_eval", fake_run)

    result = runner.invoke(
        app,
        [
            "eval",
            "--suite",
            "retrieval",
            "--no-translate",
            "--no-record",
            "--report-dir",
            str(tmp_path),
            "--min-recall",
            min_recall,
        ],
    )

    assert result.exit_code == exit_code, result.output
    (report,) = tmp_path.glob("*-retrieval.md")
    assert "**translation:** on" in report.read_text(encoding="utf-8")  # from sample_eval()
