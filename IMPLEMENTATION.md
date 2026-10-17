# Sahayak — Implementation Plan & Task Tracker

**Source of truth for requirements:** `docs/TRD.md` (v1.2). This file is the **task tracker**. Claude Code works through it task by task and keeps every status up to date.

---

## How Claude Code must use this file

1. **Read `docs/TRD.md` and this file in full before starting any work.**
2. Work **one milestone at a time, in order**. Inside a milestone, follow task order unless the `Depends` column allows parallel work.
3. **Before starting a task:** set its status to `IN_PROGRESS` and update the milestone summary table.
4. **After finishing a task:** make sure its tests pass, set its status to `DONE`, and in **Notes** write a one-line summary plus the short commit hash (for example `Added rule evaluator; 42 tests. a1b2c3d`).
5. If a task can't be completed, set it to `BLOCKED` and write the reason and what's needed in **Notes**. Then move on to the next task that doesn't depend on it.
6. Tasks with **Owner = HUMAN** need Prathmesh (API keys, approvals, reviews). Don't attempt them. If one blocks you, mark the dependent task `BLOCKED` and list it in the "Waiting on human" section at the bottom.
7. At the end of each milestone: run the full lint, type-check and test suite, tick every acceptance check in the milestone's **Exit criteria**, set the milestone status to `NEEDS_REVIEW`, add an entry to the **Change log**, and **stop and ask for review**. Prathmesh sets it to `DONE`.
8. Never delete tasks. If a task becomes unnecessary, set it to `SKIPPED` and give the reason. If new work is discovered, add a task with the next free ID suffix (for example `M4-T12a`) and the note `added`.
9. Record design decisions in `docs/DECISIONS.md` and link them in Notes (for example `see D-007`).
10. Keep commits small with conventional messages that include the task ID: `feat(M2-T03): three-valued rule evaluator`.

### Status values

| Status | Meaning |
|---|---|
| `TODO` | Not started |
| `IN_PROGRESS` | Being worked on now (at most one or two tasks at a time) |
| `BLOCKED` | Can't continue; reason in Notes |
| `NEEDS_REVIEW` | Done by Claude Code and waiting for human review (used mainly for milestones and HUMAN-reviewed items) |
| `DONE` | Complete, tested and committed |
| `SKIPPED` | Deliberately not done; reason in Notes |

### Owner values

`CC` = Claude Code · `HUMAN` = Prathmesh · `CC+HUMAN` = Claude Code prepares it, a human approves it

---

## Milestone summary

| Milestone | Title | TRD ref | Status | Started | Completed |
|---|---|---|---|---|---|
| M0 | Project skeleton | §14, §13, §17 | `DONE` | 2026-10-10 | 2026-10-10 |
| M1 | Data layer and ingestion | §8, §9 | `IN_PROGRESS` | 2026-10-10 | |
| M2 | Rule engine and question planner | §9.2, §10 | `TODO` | | |
| M3 | RAG retriever | §7, §8 | `TODO` | | |
| M4 | LangGraph agent (text only) | §10, §11 | `TODO` | | |
| M5 | Voice pipeline | §7, FR-1–3, FR-10 | `TODO` | | |
| M6 | Web frontend | §14 M6 | `TODO` | | |
| M7 | Forms and checklist | FR-8, FR-9 | `TODO` | | |
| M8 | WhatsApp channel | FR-11 | `TODO` | | |
| M9 | Continuous scheme discovery | §8A | `TODO` | | |
| M10 | Accounts, document vault and strict filtering | §8B, §8C | `TODO` | | |
| M11 | Reminders and notifications | §8D | `TODO` | | |
| M12 | Scale to 50 schemes, evals and deployment | §12, §14 M12 | `TODO` | | |
| M13+ | Stretch goals | §14 Stretch | `TODO` | | |

---

## M0 — Project skeleton

**Status:** `DONE`

| ID | Task | Owner | Depends | Status | Notes |
|---|---|---|---|---|---|
| M0-T01 | Create the repo layout from TRD §13 (empty packages with `__init__.py`), `.gitignore`, `README.md` stub, `docs/DECISIONS.md` (with a decision template), copy TRD to `docs/TRD.md`. | CC | — | `DONE` | Layout per TRD §13, README stub, DECISIONS template; TRD moved (not copied) to docs/ (see D-009). d66836b |
| M0-T02 | Create `CLAUDE.md` from TRD §17 conventions, plus a pointer to this file. | CC | M0-T01 | `DONE` | CLAUDE.md with §17 conventions, workflow pointer to this file, commands and test layout. b25b870 |
| M0-T03 | Set up `pyproject.toml` with `uv` (Python 3.12), dependency groups (`dev`, `voice`, `vault`), and ruff, mypy (`--strict`), pytest and pytest-asyncio config. | CC | M0-T01 | `DONE` | uv + Python 3.12.15; ruff, mypy --strict (pydantic plugin), pytest-asyncio, coverage; empty voice/vault groups (see D-010). d0e9f29 |
| M0-T04 | Pre-commit hooks: ruff, ruff-format, mypy, end-of-file and trailing-whitespace fixers, and a secret scanner (gitleaks or detect-secrets). | CC | M0-T03 | `DONE` | ruff, ruff-format, mypy, EOF/whitespace, yaml/toml, gitleaks (see D-004). Some hooks are blocked locally by Smart App Control; CI runs all of them (see D-007). d410bd8 |
| M0-T05 | `src/sahayak/config.py` using pydantic-settings; `.env.example` with every variable from TRD §15 (empty secrets). | CC | M0-T03 | `DONE` | All §15 variables typed and validated (secrets as SecretStr, TTL ≤ 300 s); test keeps .env.example in sync (see D-008). 22fb684 |
| M0-T06 | Structured logging (structlog JSON) with a PII-redaction processor stub, request-ID middleware, and OpenTelemetry tracing setup (console exporter in dev). | CC | M0-T05 | `DONE` | structlog JSON incl. stdlib/uvicorn logs, PII redaction baseline, X-Request-ID middleware + request log, OTel console exporter in dev. ffd9657 |
| M0-T07 | FastAPI app factory, `/healthz` (liveness) and `/readyz` (checks DB and Redis), consistent error envelope `{error:{code,message}}`, OpenAPI at `/docs`. | CC | M0-T06 | `DONE` | create_app factory, /healthz, /readyz (Postgres + Redis, 503 envelope), error envelope, /docs; smoke-tested with uvicorn (see D-002). 5620fc3 |
| M0-T08 | `docker-compose.yml`: api, postgres 16 with pgvector, redis 7, minio, clamav. Dockerfile (multi-stage, non-root user). | CC | M0-T07 | `DONE` | Multi-stage non-root Dockerfile; compose with api, pgvector/pg16, redis 7, clamav, RustFS as the S3 store (see D-005). Not run locally (no Docker); CI compose job covers it. 9d63e48 |
| M0-T09 | Async SQLAlchemy 2.0 engine/session setup and Alembic initialised (empty baseline migration, pgvector extension enabled). | CC | M0-T08 | `DONE` | Async engine, session factory, DbSession dependency; Alembic via sahayak.db:alembic, baseline 0001 enables pgvector; compose migrate job (see D-006). f9230c8, path_separator fix 27e90c4 |
| M0-T10 | Test scaffolding: `tests/unit`, `tests/integration`, `tests/e2e`, fixtures for app client, DB (transaction rollback) and Redis; `live` pytest marker excluded by default. | CC | M0-T09 | `DONE` | Fixtures: app client, rollback-only db_session, Redis DB 15; markers by directory; live excluded; skip locally / fail in CI (see D-003). 7982858 |
| M0-T11 | GitHub Actions CI: install with uv, ruff, mypy, pytest (unit + integration using service containers). | CC | M0-T10 | `DONE` | Jobs: lint (+pre-commit, gitleaks), test (pgvector + redis service containers), compose smoke test. Green in CI run 38052116905. 5eecff0, setup-uv pin 4dfe731 |
| M0-T12 | `Makefile` or `justfile` with `up`, `down`, `test`, `lint`, `migrate`, `fmt`. | CC | M0-T08 | `DONE` | justfile via rust-just: up, down, test, lint, migrate, fmt (+ install, revision, dev) (see D-001). febd17b |
| M0-T13 | Create Azure OpenAI (or Anthropic) API key and add it to local `.env`. | HUMAN | — | `TODO` | |

**Exit criteria**
- [x] `docker compose up` starts all services; `/healthz` and `/readyz` return 200. *Verified in CI (`compose` job, run 38052116905): image builds, migrate exits 0, api runs as uid 10001, `/readyz`, `/healthz`, `/docs` OK. Not run locally (no Docker).*
- [x] `just lint` and `just test` pass locally and in CI (`justfile` instead of `make`, see D-001). *CI run 38052116905: ruff, mypy 2.4 --strict, pre-commit, gitleaks, and 50/50 tests incl. integration against pgvector/redis. Locally: same, with the mypy fallback (D-007) and integration tests skipped.*
- [x] `CLAUDE.md` and `docs/DECISIONS.md` exist.

---

## M1 — Data layer and ingestion

**Status:** `IN_PROGRESS`

| ID | Task | Owner | Depends | Status | Notes |
|---|---|---|---|---|---|
| M1-T01 | SQLAlchemy models + Alembic migration for `schemes`, `scheme_chunks` (vector + tsvector with GIN and HNSW indexes), `eligibility_rules`, `sessions`, `profiles`, `messages`, `eval_runs` (TRD §9.3). Include scheme `status`, `aliases`, `launched_at`, `ends_at`, `last_verified_at` now so M9 needs no rework. | CC | M0 | `DONE` | ORM models + migration for catalogue, chunks (HNSW + GIN), rules, sessions, evals. e4fd80b |
| M1-T02 | `LLMProvider` interface (`base.py`) with structured-output method; Azure OpenAI implementation; Anthropic implementation; fake provider for tests. Retries with exponential backoff (3 tries). | CC | M0 | `DONE` | LLMProvider with Azure OpenAI, Anthropic and fake providers; 3-try backoff (see D-014). d145dd0 |
| M1-T03 | Prompt loader: prompts as versioned Markdown files in `src/sahayak/llm/prompts/`, with Jinja2 variables. | CC | M1-T02 | `DONE` | Versioned Markdown prompt loader with Jinja2. c7ba87e |
| M1-T04 | `EmbeddingProvider` interface; local `multilingual-e5-large` implementation (with the correct `query:`/`passage:` prefixes); Azure implementation; Redis embedding cache. | CC | M0 | `DONE` | EmbeddingProvider: local e5 via fastembed, Azure OpenAI, Redis cache (see D-015). 3533331, ba6b3d6 |
| M1-T05 | `data/schemes/manifest.yaml` schema (id, names, level, state, source URLs) and the **first 10 schemes** (TRD §8 list). | CC | — | `DONE` | Manifest schema and the first 10 schemes. 5ac00c4 |
| M1-T06 | Check licence, freshness and fields of the myScheme seed datasets (Hugging Face / Kaggle) and confirm myScheme `robots.txt`/terms. Record findings as a decision. | CC | — | `DONE` | Source, dataset and provider findings recorded (see D-011 to D-015). 72c981d |
| M1-T07 | Fetcher: download HTML/PDF to `data/raw/` with rate limiting, user agent, ETag caching, source URL + fetch date recorded. | CC | M1-T05 | `DONE` | Polite fetcher: robots.txt, per-domain rate limit, conditional GETs (see D-016). 1282098 |
| M1-T08 | Parsers: HTML (trafilatura) and PDF (pymupdf), keeping section headings; normalisation of Devanagari text. | CC | M1-T07 | `DONE` | HTML (trafilatura) and PDF (pymupdf) parsers keeping section headings. f92f1d2 |
| M1-T09 | Chunker: ~500 tokens, 80 overlap, section-aware; metadata (`scheme_id`, `section`, `language`, `source_url`). Unit tests. | CC | M1-T08 | `DONE` | Section-aware chunker with overlap and stable chunk ids (see D-017). 8dbbe1e |
| M1-T10 | Indexer: upsert chunks + embeddings + tsvector; idempotent by content hash (re-run creates no duplicates). | CC | M1-T01, M1-T04, M1-T09 | `DONE` | Idempotent indexer diffing chunks by content hash per source; 6 unit + 9 Postgres tests (see D-018). baa549e |
| M1-T11 | Rule extraction: LLM reads the eligibility section and outputs `EligibilityRule` JSON (schema from M2-T01 — create a minimal version here if M2 not started), saved as `draft`. | CC | M1-T02, M1-T10 | `TODO` | |
| M1-T12 | CLI (`typer`): `sahayak ingest [--scheme ID]`, `sahayak rules review <scheme_id>` (shows rule beside source text; approve / edit in `$EDITOR` / reject). | CC | M1-T11 | `TODO` | |
| M1-T13 | Integration test: ingest 2 schemes from recorded fixtures end to end; re-run is idempotent. | CC | M1-T12 | `TODO` | |
| M1-T14 | Review and approve the draft rules for the first 10 schemes. | HUMAN | M1-T12 | `TODO` | |

**Exit criteria**
- [ ] 10 schemes ingested; chunks searchable by vector and full text.
- [ ] Draft rules exist for all 10; re-running ingestion creates no duplicates.

---

## M2 — Rule engine and question planner

**Status:** `TODO`

| ID | Task | Owner | Depends | Status | Notes |
|---|---|---|---|---|---|
| M2-T01 | Pydantic models for the rule DSL: conditions (`==, !=, <, <=, >, >=, in, not_in, exists`), combinators (`all, any, not`), `source_refs`. Leave optional `required_documents` and `application_window` fields (used in M10). JSON Schema export. | CC | M0 | `TODO` | |
| M2-T02 | `UserProfile` model (TRD §9.1, including the sensitive and document-related fields) and `FieldSource`. | CC | M0 | `TODO` | |
| M2-T03 | Three-valued evaluator: returns `ELIGIBLE / NOT_ELIGIBLE / NEEDS_INFO`, the passed and failed conditions, and the missing fields. Correct short-circuit logic for `all`/`any`/`not` with unknowns. | CC | M2-T01, M2-T02 | `TODO` | |
| M2-T04 | Rule validation on save: unknown fields/operators rejected, value types checked against `UserProfile`. | CC | M2-T03 | `TODO` | |
| M2-T05 | Question planner: greedy choice of the missing field that appears in the most `NEEDS_INFO` rules; respects max 8 questions; never asks for known fields. | CC | M2-T03 | `TODO` | |
| M2-T06 | Unit tests with **≥ 95% coverage** of `eligibility/` (property-based tests with Hypothesis for the evaluator). | CC | M2-T05 | `TODO` | |
| M2-T07 | Eval set v0: profile generator + first **30 profiles** in `evals/profiles.jsonl` with expected verdicts for the 10 schemes. | CC | M2-T03 | `TODO` | |
| M2-T08 | Hand-check the 30 eval labels. | HUMAN | M2-T07 | `TODO` | |
| M2-T09 | `sahayak eval --suite eligibility` runner → Markdown report + `eval_runs` row; add to CI. | CC | M2-T07 | `TODO` | |

**Exit criteria**
- [ ] ≥ 95% coverage on `eligibility/`.
- [ ] Eligibility eval ≥ 90% accuracy on approved rules, running in CI.

---

## M3 — RAG retriever

**Status:** `TODO`

| ID | Task | Owner | Depends | Status | Notes |
|---|---|---|---|---|---|
| M3-T01 | Vector search over `scheme_chunks` (pgvector, cosine), filtered by scheme status and state. | CC | M1 | `TODO` | |
| M3-T02 | Full-text search (Postgres `tsvector`, `simple` config for hi/mr + `english` for en). | CC | M1 | `TODO` | |
| M3-T03 | Reciprocal Rank Fusion of both result lists; top-k=5; dedupe by chunk. | CC | M3-T01, M3-T02 | `TODO` | |
| M3-T04 | Language-aware querying: query in the user's language and, if useful, an English translation; merge results. | CC | M3-T03 | `TODO` | |
| M3-T05 | Retrieval test set (~30 queries in hi/mr/en with expected scheme/chunk) + recall@5 metric in the eval runner. | CC | M3-T03 | `TODO` | |

**Exit criteria**
- [ ] recall@5 ≥ 0.85 on the retrieval test set.

---

## M4 — LangGraph agent (text only)

**Status:** `TODO`

| ID | Task | Owner | Depends | Status | Notes |
|---|---|---|---|---|---|
| M4-T01 | `AgentState` (TRD §10) and graph skeleton with the Postgres checkpointer. | CC | M2, M3 | `TODO` | |
| M4-T02 | `classify_intent` node (structured output; language detection/confirmation). | CC | M4-T01 | `TODO` | |
| M4-T03 | `extract_profile` node: merges new facts into `UserProfile` with `FieldSource`; flags contradictions. | CC | M4-T01 | `TODO` | |
| M4-T04 | `shortlist_schemes` node: state filter + hybrid retrieval on stated need; excludes discontinued/archived. | CC | M4-T01 | `TODO` | |
| M4-T05 | `check_eligibility` node wrapping the rule engine. | CC | M4-T01 | `TODO` | |
| M4-T06 | `filter_results` node (basic version: only `ELIGIBLE` shown; `NOT_ELIGIBLE` and `NEEDS_INFO` hidden). Extended with documents and windows in M10. | CC | M4-T05 | `TODO` | |
| M4-T07 | `plan_question` node: uses the planner; phrases the question naturally in the user's language. | CC | M4-T06 | `TODO` | |
| M4-T08 | `retrieve_evidence` and `compose_response` nodes: simple language, citations with `chunk_id`, last-verified date. | CC | M4-T06 | `TODO` | |
| M4-T09 | Guardrails: response post-check against `display_results` (regenerate once, then template fallback); citation enforcement; prompt-injection-safe context wrapping. | CC | M4-T08 | `TODO` | |
| M4-T10 | PII detectors (Aadhaar-like, bank account, OTP, phone) used before logging and before saving profile; polite "not needed" reply. | CC | M4-T03 | `TODO` | |
| M4-T11 | `handle_reset` node and graph edges per TRD §10. | CC | M4-T09 | `TODO` | |
| M4-T12 | API: `POST /v1/sessions`, `/consent`, `/messages`, `GET /profile`, `DELETE /sessions/{id}`, `GET /v1/schemes[/{id}]`. | CC | M4-T11 | `TODO` | |
| M4-T13 | Simulated-user harness (LLM plays a fixed profile) + ~30 scripted dialogues in hi/mr/en; metrics: final accuracy, questions asked, turns. | CC | M4-T12 | `TODO` | |
| M4-T14 | Per-node tracing (latency, tokens, cost) visible in logs/traces. | CC | M4-T12 | `TODO` | |

**Exit criteria**
- [ ] Full text conversation works end to end in hi, mr and en.
- [ ] Scripted dialogue tests pass; no non-eligible scheme appears in any reply.

---

## M5 — Voice pipeline

**Status:** `TODO`

| ID | Task | Owner | Depends | Status | Notes |
|---|---|---|---|---|---|
| M5-T01 | Get Sarvam AI API key (and optionally OpenAI key for Whisper fallback). | HUMAN | — | `TODO` | |
| M5-T02 | Verify current Sarvam STT/TTS model names, endpoints, limits and audio formats; record as a decision. | CC | — | `TODO` | |
| M5-T03 | `STTProvider` interface + Sarvam implementation (language hint, confidence) + Whisper fallback; shared contract test suite. | CC | M5-T01, M5-T02 | `TODO` | |
| M5-T04 | `TTSProvider` interface + Sarvam implementation + fallback; output MP3/WAV (web) and OGG/Opus (WhatsApp) via ffmpeg. | CC | M5-T03 | `TODO` | |
| M5-T05 | Redis TTS cache keyed by (text hash, voice, language). | CC | M5-T04 | `TODO` | |
| M5-T06 | `POST /v1/sessions/{id}/voice` (multipart): STT → agent → TTS; returns transcript, text, audio URL. Text fallback when STT/TTS fails. | CC | M5-T04 | `TODO` | |
| M5-T07 | Low-ASR-confidence handling: agent asks the user to confirm key values (FR-14). | CC | M5-T06 | `TODO` | |
| M5-T08 | WebSocket `/v1/sessions/{id}/stream` for streaming audio in and partial transcript/audio out. | CC | M5-T06 | `TODO` | |
| M5-T09 | Per-stage latency metrics (STT, agent, TTS) and p50/p95 report command. | CC | M5-T06 | `TODO` | |
| M5-T10 | ASR eval: ~50 hi/mr clips with references (open datasets) → WER per language in the eval runner. | CC | M5-T03 | `TODO` | |

**Exit criteria**
- [ ] Voice round trip works locally in hi, mr and en.
- [ ] p50 / p95 latency measured and logged (target p50 ≤ 3.5 s).

---

## M6 — Web frontend

**Status:** `TODO`

| ID | Task | Owner | Depends | Status | Notes |
|---|---|---|---|---|---|
| M6-T01 | Scaffold `web/` (React + Vite + TypeScript + Tailwind), API client generated from OpenAPI, add to docker-compose. | CC | M4 | `TODO` | |
| M6-T02 | Language picker (हिंदी / मराठी / English) and UI strings in all three languages (i18n). | CC | M6-T01 | `TODO` | |
| M6-T03 | Consent screen on first use. | CC | M6-T01 | `TODO` | |
| M6-T04 | Chat screen: text input, chat bubbles, audio playback of replies. | CC | M6-T01 | `TODO` | |
| M6-T05 | Big mic button (MediaRecorder), recording state, live transcript via WebSocket. | CC | M5, M6-T04 | `TODO` | |
| M6-T06 | Eligibility cards: scheme name, benefit, reason, citations, last verified. | CC | M6-T04 | `TODO` | |
| M6-T07 | "What Sahayak knows about me" panel with edit and delete-data buttons. | CC | M6-T04 | `TODO` | |
| M6-T08 | Mobile-first, accessible layout (large tap targets, contrast, screen-reader labels). | CC | M6-T06 | `TODO` | |
| M6-T09 | Playwright smoke test of the core flow. | CC | M6-T08 | `TODO` | |
| M6-T10 | Try the core flow on a real phone with a non-technical person and note issues. | HUMAN | M6-T09 | `TODO` | |

**Exit criteria**
- [ ] A non-technical user can complete the core flow on a phone browser.

---

## M7 — Forms and checklist

**Status:** `TODO`

| ID | Task | Owner | Depends | Status | Notes |
|---|---|---|---|---|---|
| M7-T01 | Personalized document checklist generator from scheme `documents_required` + profile (FR-8). | CC | M4 | `TODO` | |
| M7-T02 | WeasyPrint setup with Noto Sans Devanagari; Jinja2 base template. | CC | M0 | `TODO` | |
| M7-T03 | Form templates for 5 schemes in `data/form_templates/`; unknown fields left blank and highlighted. | CC | M7-T02 | `TODO` | |
| M7-T04 | `generate_form` node + `GET /v1/sessions/{id}/forms/{scheme_id}.pdf`. | CC | M7-T03 | `TODO` | |
| M7-T05 | Download button in the web app. | CC | M6, M7-T04 | `TODO` | |
| M7-T06 | Snapshot tests that Hindi and Marathi render correctly in the PDF. | CC | M7-T04 | `TODO` | |

**Exit criteria**
- [ ] Downloaded PDF renders Hindi and Marathi correctly, with unknown fields highlighted.

---

## M8 — WhatsApp channel

**Status:** `TODO`

| ID | Task | Owner | Depends | Status | Notes |
|---|---|---|---|---|---|
| M8-T01 | Set up Twilio account and WhatsApp Sandbox; add credentials to `.env`; expose local webhook (ngrok or similar). | HUMAN | — | `TODO` | |
| M8-T02 | `POST /webhooks/whatsapp` with Twilio signature validation; map sender to a session via salted phone hash. | CC | M8-T01, M4 | `TODO` | |
| M8-T03 | Download voice notes, transcode with ffmpeg, run the voice pipeline; reply with OGG voice note + text. | CC | M5, M8-T02 | `TODO` | |
| M8-T04 | Handle images/PDFs sent on WhatsApp (store temporarily, route to the vault flow in M10; until then, a polite "coming soon" reply). | CC | M8-T02 | `TODO` | |
| M8-T05 | Commands: "start over", "delete my data", "STOP". | CC | M8-T02 | `TODO` | |
| M8-T06 | Integration tests with recorded Twilio payloads. | CC | M8-T03 | `TODO` | |

**Exit criteria**
- [ ] Full flow works over WhatsApp voice notes in the sandbox.

---

## M9 — Continuous scheme discovery

**Status:** `TODO`

| ID | Task | Owner | Depends | Status | Notes |
|---|---|---|---|---|---|
| M9-T01 | Migrations: `discovery_sources`, `discovered_items`, `scheme_change_events`, `review_queue`. | CC | M1 | `TODO` | |
| M9-T02 | `SourceWatcher` interface (`fetch_new_items(since)`), per-source cursor, polite crawling (rate limit, user agent, conditional requests). | CC | M9-T01 | `TODO` | |
| M9-T03 | Confirm URLs, feed formats, `robots.txt` and terms for PIB, myScheme, Maharashtra GRs, MahaDBT; record decisions. | CC | — | `TODO` | |
| M9-T04 | PIB RSS watcher with keyword filter. Fixture tests. | CC | M9-T02, M9-T03 | `TODO` | |
| M9-T05 | myScheme watcher (listing/sitemap diff against known IDs). Fixture tests. | CC | M9-T02, M9-T03 | `TODO` | |
| M9-T06 | Maharashtra GR watcher and MahaDBT watcher. Fixture tests. | CC | M9-T02, M9-T03 | `TODO` | |
| M9-T07 | Dedupe by URL/content hash; LLM classifier (`NEW_SCHEME / AMENDMENT / CLOSURE / DEADLINE_CHANGE / IRRELEVANT`). | CC | M9-T04 | `TODO` | |
| M9-T08 | Scheme matcher: fuzzy name + aliases + embedding similarity with the two thresholds; ambiguous → review queue. | CC | M9-T07 | `TODO` | |
| M9-T09 | Change appliers: new scheme → `provisional` + ingest + draft rules; amendment → new draft rule version; closure → `discontinued` immediately. | CC | M9-T08 | `TODO` | |
| M9-T10 | Review queue CLI + admin endpoints (`/admin/discover/run`, `/admin/review-queue`); approve/merge/reject. | CC | M9-T09 | `TODO` | |
| M9-T11 | Daily reviewer digest (email or Slack webhook). | CC | M9-T10 | `TODO` | |
| M9-T12 | Scheduler worker container (APScheduler): daily discovery, 6-hourly PIB, weekly staleness re-check; watcher health alerts after 48 h without success. | CC | M9-T09 | `TODO` | |
| M9-T13 | Agent support for `provisional` (labelled section, caveat, official link) and `discontinued` (closed message + alternatives); `SHOW_PROVISIONAL_SCHEMES` flag. | CC | M9-T09, M4 | `TODO` | |
| M9-T14 | `GET /v1/schemes/new?since=` and "New schemes" section in the web app. | CC | M9-T09, M6 | `TODO` | |
| M9-T15 | Discovery eval set (~100 labelled PIB/GR items) + matching eval (~50 amendments); targets in TRD §8A. | CC | M9-T08 | `TODO` | |
| M9-T16 | Hand-check discovery eval labels. | HUMAN | M9-T15 | `TODO` | |

**Exit criteria**
- [ ] `sahayak discover` on fixtures creates provisional schemes, versions amendments, hides closures.
- [ ] Discovery evals meet targets (NEW_SCHEME recall ≥ 90%, matching ≥ 95%).
- [ ] A live run adds at least one real recent item to the review queue.

---

## M10 — Accounts, document vault and strict filtering

**Status:** `TODO`

### M10a — Accounts and consent

| ID | Task | Owner | Depends | Status | Notes |
|---|---|---|---|---|---|
| M10-T01 | Choose the OTP auth provider and create an account/keys. | HUMAN | — | `TODO` | |
| M10-T02 | Migrations: `users`, `family_members`, `consents`, `audit_log`; `sessions.user_id`; `profiles.member_id`. | CC | M4 | `TODO` | |
| M10-T03 | `OTPProvider` interface + chosen implementation + fake for tests; `/v1/auth/otp/request`, `/verify`, `/refresh`; JWT (15 min access / 30 day refresh); rate limiting on OTP requests. | CC | M10-T01, M10-T02 | `TODO` | |
| M10-T04 | Per-purpose consent (profile / vault / sensitive / reminders) with grant and withdraw; `/v1/me/consents`. Features check consent before acting. | CC | M10-T03 | `TODO` | |
| M10-T05 | Family members (`/v1/me/members`); anonymous-session-to-account upgrade carrying the profile over; WhatsApp number linking after confirmation. | CC | M10-T03 | `TODO` | |
| M10-T06 | Authorization on every user-scoped endpoint + tests that user A can never access user B's data. | CC | M10-T05 | `TODO` | |
| M10-T07 | `DELETE /v1/me`: logical delete now, hard delete of blobs, keys, rows and reminders within 7 days (background job); audit entry. | CC | M10-T06 | `TODO` | |

### M10b — Document vault

| ID | Task | Owner | Depends | Status | Notes |
|---|---|---|---|---|---|
| M10-T08 | Migrations: `document_types`, `documents`, `user_schemes`. | CC | M10-T02 | `TODO` | |
| M10-T09 | Storage layer: `BlobStore` interface (MinIO locally, Azure Blob later); envelope encryption (per-user AES-GCM data key wrapped by KEK; local file KEK in dev); signed URLs (≤ 5 min). | CC | M10-T08 | `TODO` | |
| M10-T10 | Column-level encryption helpers for sensitive profile fields, certificate numbers and `extracted_fields`; key rotation note in `DECISIONS.md`. | CC | M10-T09 | `TODO` | |
| M10-T11 | Upload pipeline: size/type checks, ClamAV scan, image cleanup (deskew, crop, compress). | CC | M10-T09 | `TODO` | |
| M10-T12 | PII masking on image and text (Aadhaar-like and bank-account numbers; keep last 4). **100% recall** on the masking test set. | CC | M10-T11 | `TODO` | |
| M10-T13 | `DocumentExtractor` interface: vision-LLM implementation (classify type + extract fields against the type's schema) and OCR fallback (Azure Document Intelligence or Tesseract hin/mar). | CC | M10-T11 | `TODO` | |
| M10-T14 | Seed `document_types` for the types in TRD §8B: extraction schemas, profile field maps, **draft** validity rules and renewal guidance for MH (with `validity_reviewed=false`). | CC | M10-T08 | `TODO` | |
| M10-T15 | Verify validity periods and renewal steps for each document type in Maharashtra from official sources; mark `validity_reviewed=true`. | HUMAN | M10-T14 | `TODO` | |
| M10-T16 | Quality checks: holder name vs member name, date sanity, legibility; expiry computation from printed date or validity rule. | CC | M10-T13, M10-T14 | `TODO` | |
| M10-T17 | Confirm flow: `POST /members/{id}/documents` → `pending_confirmation`; `POST /documents/{id}/confirm` saves fields, updates profile + `FieldSource`, re-runs eligibility, returns newly unlocked schemes. | CC | M10-T16 | `TODO` | |
| M10-T18 | Conflict handling between document and self-declared values (ask the user; never silently override). | CC | M10-T17 | `TODO` | |
| M10-T19 | Document management endpoints: list, signed-URL download, replace (old → `superseded`), delete; show which schemes use each document. | CC | M10-T17 | `TODO` | |
| M10-T20 | Optional QR / verification-URL check for certificates that have one → `verification=qr_verified`. | CC | M10-T17 | `TODO` | |
| M10-T21 | Sensitive-field handling: optional prompts with "prefer not to say", sensitive consent required, never inferred, stripped from logs/traces, sent to the LLM only when needed. Log-capture tests. | CC | M10-T04, M10-T10 | `TODO` | |

### M10c — Eligibility with documents and strict filtering

| ID | Task | Owner | Depends | Status | Notes |
|---|---|---|---|---|---|
| M10-T22 | Extend the rule engine with `required_documents` (`valid_on` = `today` or `application_deadline`) and `application_window`. Unit tests. | CC | M2, M10-T08 | `TODO` | |
| M10-T23 | Full `filter_results` per the TRD §8C table: Ready to apply / Eligible, action needed / provisional; hide not eligible, needs-info, closed windows, discontinued. Sort by benefit and deadline. | CC | M10-T22 | `TODO` | |
| M10-T24 | Agent nodes: `load_user_context`, `handle_document`, `manage_vault`; new intents; post-check updated to reject any scheme outside `display_results`. | CC | M10-T23, M10-T17 | `TODO` | |
| M10-T25 | "No results" behavior: honest message + what would unlock more (missing detail or document). | CC | M10-T24 | `TODO` | |
| M10-T26 | `GET /v1/members/{id}/recommendations` and `POST/PATCH /members/{id}/schemes/{scheme_id}` (saved/applied). | CC | M10-T23 | `TODO` | |
| M10-T27 | WhatsApp document flow: image/PDF → extraction → spoken/text summary → yes/no confirmation. | CC | M8, M10-T17 | `TODO` | |
| M10-T28 | Web UI: login, consent settings, family members, "My documents" (upload with camera, confirm screen, status/expiry badges), recommendations in two groups. | CC | M6, M10-T19, M10-T26 | `TODO` | |
| M10-T29 | Synthetic certificate generator (fake names, no real people) + extraction eval set (~60 docs) + masking test set. | CC | M10-T13 | `TODO` | |
| M10-T30 | Strict-filter eval (**0 inapplicable schemes shown**) added to CI as a hard gate; recall reported separately. | CC | M10-T23 | `TODO` | |
| M10-T31 | `docs/SECURITY.md`: threat model, encryption design, access rules, breach response plan, DPDP notes. | CC | M10-T21 | `TODO` | |
| M10-T32 | Review `SECURITY.md` and DPDP compliance points. | HUMAN | M10-T31 | `TODO` | |

**Exit criteria**
- [ ] Uploading a sample income certificate pre-fills the profile after confirmation and changes recommendations correctly.
- [ ] Strict-filter eval: 0 inapplicable schemes shown.
- [ ] Extraction ≥ 95% type accuracy, ≥ 90% key-field accuracy; masking recall 100%.
- [ ] Cross-user access tests pass; account deletion removes every file, row and reminder.

---

## M11 — Reminders and notifications

**Status:** `TODO`

| ID | Task | Owner | Depends | Status | Notes |
|---|---|---|---|---|---|
| M11-T01 | Migrations: `reminders` (with unique key), `notification_log`, `notification_prefs`. | CC | M10 | `TODO` | |
| M11-T02 | Reminder planner (daily 09:00 IST): document expiring (30/7/0 days, earlier for slow renewals), expired (+1, +14 days), document invalid before a saved scheme's deadline, application deadline (14/3 days), scholarship renewal. Idempotent. | CC | M11-T01 | `TODO` | |
| M11-T03 | Unreviewed validity rules produce "may need renewal" wording, never "expired". | CC | M11-T02 | `TODO` | |
| M11-T04 | `Notifier` interface + WhatsApp template implementation, SMS implementation, email implementation; channel fallback; retries with backoff; delivery status webhooks → `notification_log`. | CC | M11-T01 | `TODO` | |
| M11-T05 | Write reminder template text for every reminder type in hi/mr/en in `notifications/templates/`. | CC | M11-T02 | `TODO` | |
| M11-T06 | Register WhatsApp message templates with the provider and get them approved; register SMS DLT templates if SMS is used. | HUMAN | M11-T05 | `TODO` | |
| M11-T07 | Sending rules: quiet hours 21:00–08:00 IST, max 1 message per user per day (bundle), per-type opt-out, STOP keyword. | CC | M11-T04 | `TODO` | |
| M11-T08 | Deep links in reminders (upload renewed document / start application) and renewal guidance per document type and state. | CC | M11-T05, M10-T14 | `TODO` | |
| M11-T09 | Cancel pending reminders on document replacement, deletion, consent withdrawal or account deletion. | CC | M11-T02 | `TODO` | |
| M11-T10 | `GET/PUT /v1/me/reminders` and `/v1/me/notification-prefs`; reminder settings screen in the web app; "remind me" handling in chat (`manage_reminders` intent). | CC | M11-T07 | `TODO` | |
| M11-T11 | Time-travel tests (freezegun) over a simulated year: exact expected reminders, no duplicates, none in quiet hours, cancellations work. | CC | M11-T09 | `TODO` | |
| M11-T12 | Live test: receive a real WhatsApp template reminder in the sandbox. | CC+HUMAN | M11-T06, M11-T10 | `TODO` | |

**Exit criteria**
- [ ] Time-travel reminder tests pass.
- [ ] A real WhatsApp template reminder is received.
- [ ] Uploading a renewed document cancels pending reminders.

---

## M12 — Scale to 50 schemes, evals and deployment

**Status:** `TODO`

| ID | Task | Owner | Depends | Status | Notes |
|---|---|---|---|---|---|
| M12-T01 | Expand `manifest.yaml` to ~50 schemes (central + Maharashtra, including scholarships); ingest; draft rules with `required_documents` and windows. | CC | M10 | `TODO` | |
| M12-T02 | Review and approve rules for all ~50 schemes. | HUMAN | M12-T01 | `TODO` | |
| M12-T03 | Grow the eligibility eval set to **200 profiles** covering all schemes and edge cases. | CC | M12-T01 | `TODO` | |
| M12-T04 | Hand-check the new eval labels. | HUMAN | M12-T03 | `TODO` | |
| M12-T05 | Groundedness eval (LLM-as-judge, target ≥ 95%). | CC | M4 | `TODO` | |
| M12-T06 | Run the full eval suite; fix regressions until targets in TRD §5 and §12 are met; save the report. | CC | M12-T03, M12-T05 | `TODO` | |
| M12-T07 | Provision Azure resources (Container Apps, Postgres Flexible Server with pgvector, Redis, Blob Storage, Key Vault) and grant deploy credentials. | HUMAN | — | `TODO` | |
| M12-T08 | Infrastructure as code (Bicep or Terraform) + GitHub Actions deploy workflow: API, web, discovery job, reminder job; secrets from Key Vault. | CC | M12-T07 | `TODO` | |
| M12-T09 | Production hardening: CORS, rate limits, security headers, backups, log retention, alerting on job failures. | CC | M12-T08 | `TODO` | |
| M12-T10 | Load test the voice and text endpoints (k6 or Locust); confirm latency targets in production. | CC | M12-T08 | `TODO` | |
| M12-T11 | README: problem, demo GIF/video, architecture diagram, how to run, eval results table with real numbers, privacy approach. | CC | M12-T06 | `TODO` | |
| M12-T12 | Record the demo video and write the final resume bullets from real metrics. | HUMAN | M12-T11 | `TODO` | |

**Exit criteria**
- [ ] Public demo URL is live.
- [ ] Discovery and reminder jobs run daily in production.
- [ ] README shows real measured metrics.

---

## M13+ — Stretch goals

**Status:** `TODO`

| ID | Task | Owner | Depends | Status | Notes |
|---|---|---|---|---|---|
| M13-T01 | DigiLocker integration (requires partner registration) → `verification=digilocker` (FR-33). | CC+HUMAN | M12 | `TODO` | |
| M13-T02 | New-scheme WhatsApp alerts for opted-in users (FR-19). | CC | M11 | `TODO` | |
| M13-T03 | Phone call channel via Twilio or Exotel (FR-15). | CC | M12 | `TODO` | |
| M13-T04 | More states and languages (Tamil, Bengali). | CC | M12 | `TODO` | |
| M13-T05 | Analytics dashboard for scheme demand (aggregated, no personal data). | CC | M12 | `TODO` | |

---

## Waiting on human

Claude Code lists here every HUMAN task that is currently blocking progress.

| Task ID | What's needed | Blocking | Date raised |
|---|---|---|---|
| M0-T13 | Azure OpenAI (or Anthropic) API key in local `.env` | M1-T11 live rule extraction (M1-T02 can proceed with the fake provider) | 2026-10-10 |

---

## Change log

Claude Code adds one entry per completed milestone (newest first).

| Date | Milestone | Summary | Commit |
|---|---|---|---|
| 2026-10-10 | M0 | Project skeleton: layout, CLAUDE.md, uv/ruff/mypy/pytest, pre-commit, settings, logging/tracing/redaction, FastAPI app with health and readiness, Docker + compose, async DB + Alembic baseline (pgvector), test fixtures, CI, justfile. 10 decisions (D-001 to D-010). CI green on PR #1 after two CI fixes (setup-uv pin, Alembic path_separator). Reviewed, merged and marked DONE by Prathmesh. | d5c3a3f (merge) |
