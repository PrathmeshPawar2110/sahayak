# Design decisions

Record one entry per decision, newest last. Reference them from `IMPLEMENTATION.md` notes as `see D-NNN`.

## Template

```
## D-NNN — <short title>

- **Date:** YYYY-MM-DD
- **Task:** <task ID>
- **Status:** accepted | superseded by D-NNN
- **Context:** what problem or ambiguity prompted this.
- **Decision:** what we chose.
- **Alternatives considered:** what else was on the table and why it lost.
- **Consequences:** what this makes easier or harder; follow-ups.
```

---

## D-001 — `just` as the task runner, installed through uv

- **Date:** 2026-10-10
- **Task:** M0-T12
- **Status:** accepted
- **Context:** The tracker allows a `Makefile` or a `justfile`. The main dev machine is Windows, which has no `make`.
- **Decision:** Use a `justfile`. `just` comes from the `rust-just` PyPI package in the `dev` group, so `uv sync` installs it and `uv run just <recipe>` works on every OS. Recipes use PowerShell on Windows and `sh` elsewhere.
- **Alternatives considered:** Makefile (not available on Windows without extra installs); a Python task script (more code to maintain).
- **Consequences:** No global tool install needed. CI calls `uv run ...` directly, so it doesn't depend on `just`.

## D-002 — Error envelope fields and readiness failures

- **Date:** 2026-10-10
- **Task:** M0-T07
- **Status:** accepted
- **Context:** TRD §11 requires errors to look like `{error: {code, message}}`.
- **Decision:** The envelope is `{"error": {"code", "message", "request_id"?, "details"?}}`. `code` is a stable snake_case string (`not_found`, `validation_error`, `internal_error`, …). `request_id` matches the `X-Request-ID` header for support. Validation `details` list `loc`/`msg`/`type` but never the submitted value, which could hold PII. A failed `/readyz` returns 503 with `code: not_ready` and per-dependency `ok`/`unavailable` details, never exception text, which could leak connection strings. Domain code raises `AppError(code, message, status_code)`.
- **Alternatives considered:** RFC 9457 problem+json (doesn't match the TRD shape).
- **Consequences:** Clients branch on `error.code`, not on message text.

## D-003 — Integration tests skip locally, fail in CI

- **Date:** 2026-10-10
- **Task:** M0-T10
- **Status:** accepted
- **Context:** Integration tests need Postgres and Redis. Developers won't always have the stack running, but CI must never silently skip.
- **Decision:** Fixtures probe the TCP port first. If it's unreachable, tests are skipped with a reason, unless `SAHAYAK_REQUIRE_SERVICES=1` (set in CI), in which case they fail. Test services are selected with `TEST_DATABASE_URL` (default `.../sahayak_test`) and `TEST_REDIS_URL` (default DB 15). The test database is migrated to head once per session; each test gets a session inside a transaction that is rolled back (`join_transaction_mode="create_savepoint"`).
- **Alternatives considered:** testcontainers (needs Docker on every dev machine and slows the suite).
- **Consequences:** docker-compose creates `sahayak_test` alongside `sahayak` on first start.

## D-004 — Secret scanning with gitleaks

- **Date:** 2026-10-10
- **Task:** M0-T04
- **Status:** accepted
- **Context:** The tracker asks for gitleaks or detect-secrets.
- **Decision:** gitleaks as a pre-commit hook (staged changes) and as a CI step (`gitleaks dir` over the checkout, image `ghcr.io/gitleaks/gitleaks:v8.30.0`).
- **Alternatives considered:** detect-secrets (needs a baseline file kept in sync).
- **Consequences:** CI scans the working tree, not git history. If history scanning becomes necessary, switch the step to `gitleaks git` with a full-depth checkout.

## D-005 — RustFS instead of MinIO for local object storage

- **Date:** 2026-10-10
- **Task:** M0-T08
- **Status:** accepted
- **Context:** TRD §7 names MinIO for local blob storage. MinIO no longer publishes container images: `minio/minio` is gone from Docker Hub and Quay, and Bitnami's copy is frozen as `bitnamilegacy/minio` (July 2025, no security updates).
- **Decision:** The compose service is still called `minio` (and `BLOB_BACKEND=minio` still means "local S3-compatible store"), but it runs `rustfs/rustfs:1.0.1`: an actively maintained, Apache-2.0, MinIO-compatible S3 server on the same ports (9000 S3 API, 9001 console). Credentials come from `LOCAL_S3_ACCESS_KEY` / `LOCAL_S3_SECRET_KEY`.
- **Alternatives considered:** `bitnamilegacy/minio` (unmaintained); SeaweedFS or Garage (more setup for S3 credentials).
- **Consequences:** M10's `BlobStore` must use only the plain S3 API (presigned URLs, put/get/delete), so local RustFS and Azure Blob in production stay interchangeable. Verify presigned-URL behaviour against RustFS when M10 starts.

## D-006 — Where migrations live and how they run

- **Date:** 2026-10-10
- **Task:** M0-T09
- **Status:** accepted
- **Context:** TRD §13 puts Alembic under `src/sahayak/db/alembic/`. The Docker image installs the package as a wheel, so a path relative to the checkout wouldn't exist there.
- **Decision:** `alembic.ini` uses `script_location = sahayak.db:alembic` (a package resource path), so the same config works from a checkout and from the installed wheel. The URL always comes from `DATABASE_URL`. Compose runs a one-shot `migrate` service that the `api` service waits on; the API process never migrates on start. Migration files are named `YYYYMMDD_<rev>_<slug>.py` with short sequential revision IDs (`0001`, …) and are excluded from mypy (their names aren't importable).
- **Alternatives considered:** Migrating on API start (racy with several replicas).
- **Consequences:** In production (M12) migrations run as a separate job before the new revision rolls out.

## D-007 — Local tooling under Windows Smart App Control

- **Date:** 2026-10-10
- **Task:** M0-T03, M0-T04
- **Status:** accepted
- **Context:** The main dev machine has Windows Application Control (Smart App Control) on. It blocks: the compiled `librt` extension that every current mypy imports (all `librt` versions tested); console-script `.exe` launchers in some environments (`pre-commit.exe`, pre-commit's `trailing-whitespace` hook, the `gitleaks` binary); and occasionally uv's temporary build interpreter. Docker and WSL aren't installed.
- **Decision:** Project configuration stays standard: mypy 2.x, pre-commit hooks and gitleaks as configured, with CI as the authority. On the affected machine:
  - Run tools as modules (`python -m pre_commit`, `python -m pytest`), which avoids blocked launchers.
  - Type-check with a pure-Python mypy 1.17.1 (built with `--no-binary mypy`, which predates `librt`) pointed at the project venv with `--python-executable`.
  - Set `UV_LINK_MODE=copy`, because the project lives under OneDrive, which rejects uv's hard links.
- **Alternatives considered:** Pinning the project to an old mypy (would weaken checks for everyone); turning off Smart App Control (the machine owner's call).
- **Consequences:** Local mypy results may differ slightly from mypy 2.x in CI. Docker-based checks (`docker compose up`, image build) run only in CI on this machine.

## D-008 — Settings added beyond TRD §15

- **Date:** 2026-10-10
- **Task:** M0-T05, M0-T06
- **Status:** accepted
- **Context:** M0 needs a few knobs the TRD doesn't list.
- **Decision:** Add `LOG_LEVEL`, `LOG_FORMAT` (`json` | `console`), `OTEL_TRACES_EXPORTER` (`none` | `console`; defaults to `console` only when `APP_ENV=dev`), and the compose-only `LOCAL_S3_ACCESS_KEY` / `LOCAL_S3_SECRET_KEY`. Tests read `TEST_DATABASE_URL`, `TEST_REDIS_URL` and `SAHAYAK_REQUIRE_SERVICES`. A unit test fails if any `Settings` field is missing from `.env.example`. `SIGNED_URL_TTL_SEC` is capped at 300 s by validation (NFR Security).
- **Consequences:** An OTLP exporter can be added as a new `OTEL_TRACES_EXPORTER` value when a collector exists (M12).

## D-009 — TRD moved, not copied, to `docs/TRD.md`

- **Date:** 2026-10-10
- **Task:** M0-T01
- **Status:** accepted
- **Context:** M0-T01 says to copy the TRD to `docs/TRD.md`. Two copies would drift apart.
- **Decision:** `git mv Sahayak_TRD_1.md docs/TRD.md`; history follows the file.
- **Consequences:** None beyond the path change; `IMPLEMENTATION.md` already refers to `docs/TRD.md`.

## D-010 — Dependency baseline

- **Date:** 2026-10-10
- **Task:** M0-T03
- **Status:** accepted
- **Context:** The TRD names "SQLAlchemy 2.0". The current release is 2.1 (same 2.x API).
- **Decision:** Take current releases at M0 (FastAPI 0.143, SQLAlchemy 2.1, Pydantic 2.14, structlog 26, uv 0.13), pinned through `uv.lock`, with Python `>=3.12,<3.13`. The `voice` and `vault` dependency groups exist but stay empty until M5 and M10, so the image doesn't carry unused libraries.
- **Consequences:** Upgrades go through `uv lock --upgrade` in their own commit.
