# Task runner. `just` is installed into the project venv by `uv sync`: run `uv run just <recipe>`.

set windows-shell := ["powershell.exe", "-NoLogo", "-NoProfile", "-Command"]

# List recipes
default:
    @just --list

# Install dependencies and git hooks
install:
    uv sync
    uv run pre-commit install

# Start the docker-compose stack (api, postgres, redis, object store, clamav)
up:
    docker compose up --build --detach

# Stop the stack (data volumes are kept; add -v to `docker compose down` to wipe them)
down:
    docker compose down

# Lint, check formatting and type-check
lint:
    uv run ruff check
    uv run ruff format --check
    uv run mypy

# Auto-fix lint issues and format code
fmt:
    uv run ruff check --fix
    uv run ruff format

# Run tests; extra args go to pytest, e.g. `just test -m integration`
test *args:
    uv run pytest {{args}}

# Apply database migrations (uses DATABASE_URL)
migrate:
    uv run alembic upgrade head

# Create a new migration from model changes, e.g. `just revision "add schemes"`
revision message:
    uv run alembic revision --autogenerate -m "{{message}}"

# Run the API locally with auto-reload
dev:
    uv run uvicorn sahayak.api.app:create_app --factory --reload --no-access-log
