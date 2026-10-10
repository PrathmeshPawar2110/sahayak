# CLAUDE.md

Sahayak is a multilingual voice agent for Indian government schemes. Requirements: `docs/TRD.md`.

## Workflow

- **`IMPLEMENTATION.md` is the task tracker.** Read it and `docs/TRD.md` before starting work, follow
  its "How Claude Code must use this file" rules, and keep task statuses current.
- Build one milestone at a time, in order. At the end of a milestone, run lint, type-check and tests,
  tick the exit criteria, and stop for human review.
- When a requirement is ambiguous, pick the simplest option that fits the TRD and record it in
  `docs/DECISIONS.md`.

## Conventions (TRD §17)

- Python 3.12, full type hints, `ruff` + `mypy --strict` must pass.
- Async everywhere in the request path. No blocking I/O inside handlers.
- Every external provider goes behind an interface in its package's `base.py`. No vendor SDK imports
  outside the provider implementations.
- Keep prompts in `src/sahayak/llm/prompts/*.md` as versioned files, not inline strings.
- Add or update tests in the same change as the code.
- No secrets in code, logs or fixtures. Redact PII before logging.
- Small, focused commits with conventional commit messages including the task ID
  (`feat(M2-T03): three-valued rule evaluator`).
- After each milestone, update `README.md` and `docs/DECISIONS.md`.
- The LLM never decides eligibility; the deterministic rule engine does (TRD §6).

## Commands

```sh
uv sync                 # install deps (Python 3.12 venv)
uv run just lint        # ruff + ruff format --check + mypy --strict
uv run just fmt         # auto-fix + format
uv run just test        # pytest (unit + integration; `live` tests excluded)
uv run just migrate     # alembic upgrade head
uv run just up / down   # docker compose stack
uv run pytest -m live   # tests that call paid APIs (never in default CI)
```

## Testing

- `tests/unit` — no network, no services.
- `tests/integration` — real Postgres/Redis. They skip when the services are unreachable, unless
  `SAHAYAK_REQUIRE_SERVICES=1` (set in CI), in which case they fail.
- `tests/e2e` — scripted dialogues and Playwright (later milestones).
- Never use real people's documents in tests or fixtures.
