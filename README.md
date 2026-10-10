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

## Project layout

See TRD §13. Design decisions are recorded in [docs/DECISIONS.md](docs/DECISIONS.md).
