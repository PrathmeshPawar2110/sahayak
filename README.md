# Sahayak

A multilingual (Hindi, Marathi, English) voice agent that helps Indian citizens find
government schemes, check eligibility with a deterministic rule engine, and prepare
applications.

> Work in progress. Requirements live in [docs/TRD.md](docs/TRD.md); progress is tracked in
> [IMPLEMENTATION.md](IMPLEMENTATION.md).

## Quick start

Prerequisites: [uv](https://docs.astral.sh/uv/), Docker with Compose v2, and
[just](https://just.systems/) (installed into the project by `uv sync`, run it as `uv run just`).

```sh
cp .env.example .env          # fill in secrets as needed
uv sync                       # creates .venv with Python 3.12 and dev tools
uv run pre-commit install     # git hooks
uv run just up                # api, postgres+pgvector, redis, minio, clamav
curl localhost:8000/healthz   # {"status":"ok"}
curl localhost:8000/readyz    # checks postgres and redis
```

API docs: <http://localhost:8000/docs>

## Common tasks

| Command | What it does |
|---|---|
| `uv run just lint` | ruff check, ruff format --check, mypy --strict |
| `uv run just fmt` | auto-fix lint issues and format |
| `uv run just test` | unit + integration tests (integration tests skip if Postgres/Redis are down) |
| `uv run just migrate` | apply Alembic migrations |
| `uv run just up` / `down` | start / stop the docker-compose stack |

## Scheme ingestion

The curated schemes are listed in [data/schemes/manifest.yaml](data/schemes/manifest.yaml).
`sahayak ingest` fetches each source (respecting `robots.txt` and a per-domain rate limit),
parses and chunks it, embeds new chunks, indexes them in Postgres (pgvector + full text), and has
the LLM draft each scheme's eligibility rule. Re-running only processes what changed.

```sh
uv run just migrate
uv run sahayak ingest                      # all schemes (needs LLM settings in .env)
uv run sahayak ingest -s pm-kisan          # one scheme
uv run sahayak ingest --skip-rules         # index only, no LLM needed
uv run sahayak rules list                  # live and pending rule versions per scheme
uv run sahayak rules review pm-kisan       # draft beside its source text: approve / edit / reject
```

Drafts are never used until a human approves them: the rule engine, not the LLM, decides
eligibility. `--reindex` re-embeds everything after changing the embedding model, and `--redraft`
drafts again from unchanged text. See D-018 to D-021 in [docs/DECISIONS.md](docs/DECISIONS.md).

After approving rules, snapshot them for CI and commit the result:

```sh
uv run sahayak rules export                # approved rules -> data/rules/<scheme_id>.json
uv run sahayak rules schema                # JSON Schema of a rule document
```

## Eligibility engine and evals

`sahayak.eligibility` is pure Python. `UserProfile` holds what is known about a citizen (`None`
means not known yet). `evaluate(rule, profile)` returns `ELIGIBLE`, `NOT_ELIGIBLE` or
`NEEDS_INFO`, with the conditions that passed or failed and the fields still missing.
`next_question` picks the missing field that the most undecided schemes need, asking at most 8
questions. See D-023 to D-027.

The eval set [evals/profiles.jsonl](evals/profiles.jsonl) has 30 synthetic profiles labelled for
the 10 schemes, with a reason for each label (D-028). It is regenerated with
`uv run python -m sahayak.evals.reference`, which never overwrites labels marked `checked`.

```sh
uv run sahayak eval --suite eligibility                  # data/rules/ snapshot, records an eval_runs row
uv run sahayak eval --no-record                          # no database needed
uv run sahayak eval --rules latest --no-record           # preview the current drafts
```

Each run writes `evals/reports/<date>-eligibility.md` (accuracy, confusion matrix, per-scheme
precision/recall/F1 and every mismatch) and exits 1 below `--min-accuracy` (default 0.9). CI runs
it on every PR.

## Retrieval

`sahayak.rag.retriever.HybridRetriever` finds the scheme passages behind an answer: pgvector cosine search
and Postgres full-text search, each filtered to schemes that are `active` or `provisional` and
central or in the user's state, fused with Reciprocal Rank Fusion into the top 5 chunks. A Hindi
or Marathi query is also translated to English with the LLM and searched again, because the
scheme sources are English. See D-029 and D-030.

```python
retriever = HybridRetriever(embedder, translator=LLMQueryTranslator(llm))
retrieval = await retriever.retrieve(
    session, "माझ्याकडे दोन एकर शेती आहे", language=Language.MR,
    search_filter=SearchFilter.for_user("MH"),
)
```

The retrieval eval scores 31 hi/mr/en queries in
[evals/retrieval.jsonl](evals/retrieval.jsonl) (recall@5, hit@1, MRR, per language; D-031). It
needs the ingested schemes and the embedding model, so it runs on demand, not in CI:

```sh
uv run sahayak eval --suite retrieval                    # with query translation (calls the LLM)
uv run sahayak eval --suite retrieval --no-translate     # embeddings only
uv run sahayak eval --suite all                          # eligibility + retrieval + dialogues
```

On the 10 M1 schemes it reaches recall@5 0.919 (target 0.85), with hit@1 0.871 using translation
and 0.742 without.

## Talking to the agent

The agent is a LangGraph state machine (`sahayak.agent.graph`, TRD §10). Each turn: one LLM call
reads the message (intent, language, stated facts, schemes named, English search text); the
facts are validated and merged into the profile; candidate schemes are shortlisted and run
through the rule engine; and then either the single question that settles the most schemes is
asked, or a reply is written from cited scheme passages. Only schemes the engine finds
`ELIGIBLE` are recommended, and a post-check rejects any reply that names another scheme, cites
nothing, or states a different verdict (D-032 to D-036). Conversation state is kept per session
by the Postgres checkpointer.

```sh
uv run just migrate && uv run just dev        # needs LLM settings in .env and ingested schemes
S=$(curl -s -X POST localhost:8000/v1/sessions -H 'content-type: application/json' \
      -d '{"language":"hi"}' | jq -r .session_id)
curl -s -X POST localhost:8000/v1/sessions/$S/consent -H 'content-type: application/json' \
      -d '{"purposes":["profile"],"policy_version":"2026-10"}'
curl -s -X POST localhost:8000/v1/sessions/$S/messages -H 'content-type: application/json' \
      -d '{"text":"मैं उत्तर प्रदेश का किसान हूँ, मेरे पास 3 एकड़ ज़मीन है"}'
curl -s localhost:8000/v1/sessions/$S/profile     # what Sahayak knows
curl -s -X DELETE localhost:8000/v1/sessions/$S   # erase everything
```

A reply is `{reply_text, kind, language, question_field, eligibility: {ready, action_needed,
provisional}, citations[]}`. Messages need the `profile` consent; sensitive details (caste,
religion, disability) are asked and kept only with the `sensitive` consent, and withdrawing a
consent erases what it covered. Aadhaar, bank account, OTP and phone numbers are masked out of
messages before anything stores, logs or sends them (D-037, D-038). Until the M1 draft rules are
approved (M1-T14), every scheme is `provisional`: results are shown with a "not yet verified"
note and the official link.

Every node logs `agent_node` and every turn `agent_turn` with latency, LLM calls, tokens and cost
(never message text), and runs in an OpenTelemetry span (D-039).

### Dialogue eval

[evals/dialogues.jsonl](evals/dialogues.jsonl) holds 30 conversations, 10 each in Hindi, Marathi
and English, one per persona of the eligibility set (D-040). CI plays them all with a scripted
user and a fake LLM (`tests/e2e/test_dialogues.py`): each must end with exactly the schemes the
rules find on the full profile, and no reply may name a scheme the user can't get. Live, an LLM
plays each persona against the real agent:

```sh
uv run sahayak eval --suite dialogues                    # LLM-played users (about 600 LLM calls)
uv run sahayak eval --suite dialogues --user scripted --limit 3
```

### Windows notes

- If the repo lives under OneDrive, set `UV_LINK_MODE=copy` (OneDrive rejects uv's hard links),
  and set `LOCAL_MODEL_DIR` outside OneDrive (the embedding model is about 2 GB).
- With Smart App Control on, some compiled tools are blocked (notably mypy's `librt`). Run tools
  as modules (`uv run python -m pytest`, `uv run python -m pre_commit`, `uv run python -m
  sahayak.cli`). See [D-007](docs/DECISIONS.md) for the type-checking workaround; CI is the
  source of truth.
- Without Docker, `pip install pgserver` in a separate venv gives a local Postgres 16 with
  pgvector: start it with `pgserver.get_server(<data dir>, cleanup_mode=None)`, create a
  `sahayak_test` database, and point `TEST_DATABASE_URL` at it to run the integration tests.
- Run the API with `--reload` (`uv run just dev`): uvicorn then uses the selector event loop,
  which the checkpointer's driver (psycopg) needs on Windows. Without Redis the embedding cache
  is skipped, after one slow connection attempt every 30 seconds.

## Project layout

See TRD §13. Design decisions are recorded in [docs/DECISIONS.md](docs/DECISIONS.md).
