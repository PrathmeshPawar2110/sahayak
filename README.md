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

## Project layout

See TRD §13. Design decisions are recorded in [docs/DECISIONS.md](docs/DECISIONS.md).
