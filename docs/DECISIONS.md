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

## D-011 — Scheme sources: official sites, not myScheme or its scraped datasets

- **Date:** 2026-10-10
- **Task:** M1-T06
- **Status:** accepted
- **Context:** TRD §8 names myScheme.gov.in as a source and two public myScheme datasets as possible seeds, "after verifying licence and freshness". Findings (checked 2026-10-10):
  - **myScheme `robots.txt`** allows everything except `/404`, and lists a sitemap.
  - **myScheme Terms of Use** say: "The use of any software (e.g. bots, scraper tools) or other automatic devices to access, monitor, or copy … is prohibited unless expressly authorized by the myScheme in writing." The separate copyright policy allows accurate reproduction of material with prominent acknowledgement. The stricter Terms of Use govern automated access.
  - **myScheme pages are rendered in the browser.** The HTML of `/schemes/<slug>` has no scheme text (an empty Next.js shell); the content comes from an undocumented API that needs a key embedded in the site's JavaScript. HTML parsing (trafilatura) therefore can't read it either.
  - **Hugging Face `shrijayan/gov_myscheme`:** labelled Apache-2.0, last modified 2025-02-09, 723 PDFs (with duplicate "copy" files), collected by crawling the myScheme sitemap. It is over a year and a half old, and the uploader can't license content they scraped against the site's terms.
  - **Kaggle `elchemist/myscheme-india-govt-welfare-schemes`:** labelled CC0, version 3 of 2026-03-25, 4,670 schemes plus 51,435 FAQs, "sourced from myScheme.gov.in public API" with a scraper. Same provenance problem, and it is a snapshot.
- **Decision:** The curated manifest uses official primary sources only: ministry and department sites (for example the PM-KISAN operational guidelines PDF), PIB documents on `static.pib.gov.in` (central schemes), and NIC district portals for Maharashtra schemes whose own portals are unreachable or rendered in the browser. Neither dataset is used as a source of scheme text or rules. A unit test keeps myScheme URLs out of the manifest.
- **Alternatives considered:** Crawling the myScheme API (forbidden by its terms, and fragile); seeding from the Kaggle dataset (provenance and freshness; acceptable at most as an offline checklist of scheme names, which M1 doesn't need).
- **Consequences:** M9-T05 (myScheme watcher) needs either written authorisation from myScheme or a different design (for example manual sitemap checks). This is flagged for M9-T03. Several official hosts (`pmjay.gov.in`, `jeevandayee.gov.in`, `npscra.nsdl.co.in`, `financialservices.gov.in`) timed out from the dev machine, and some (`labour.gov.in`, `india.gov.in`, `pib.gov.in` HTML pages) return 403 to non-browser clients. Each scheme can therefore list several sources, and the fetcher skips a failed source without failing the scheme.

## D-012 — Garbled Devanagari from legacy-font PDFs is detected and skipped

- **Date:** 2026-10-10
- **Task:** M1-T08
- **Status:** accepted
- **Context:** Maharashtra Government Resolutions (for example the Ladki Bahin GR of 28 June 2024) are typeset in the "Sakal Marathi" font. Its embedded Unicode mapping is wrong, so the PDF's text layer extracts as plausible-looking but garbled Marathi: the i-matra comes out as "ह" placed before the consonant ("महहला" for "महिला"), reph comes out as "ण" ("हनणणय" for "निर्णय"), and थ as "ि". The mapping is ambiguous (a real "ह" looks the same), so it can't be reversed reliably without a dictionary or OCR.
- **Decision:** The PDF parser flags a document as garbled when it uses a known broken font, or when its Devanagari text has an implausibly low share of the i-matra (`ि`). Normal Hindi and Marathi prose has about 3–5% of Devanagari characters as i-matra; the Ladki Bahin GR has 0.3%. Garbled documents are skipped with a warning instead of being indexed, because searchable garbage hurts retrieval and would mislead rule extraction. The manifest uses an official HTML page for such schemes instead.
- **Alternatives considered:** A character-level repair table (unreliable, see above); OCR with Tesseract `hin`/`mar` (the right long-term fix; Tesseract is planned for M10's OCR fallback and can be reused here then).
- **Consequences:** M9's Maharashtra GR watcher will hit this on most GRs. It will need the OCR path (M10-T13) or the English GR where one exists.

## D-013 — Settings added in M1

- **Date:** 2026-10-10
- **Task:** M1-T02, M1-T04
- **Status:** accepted
- **Context:** Providers need a few knobs TRD §15 doesn't list.
- **Decision:** `AZURE_OPENAI_API_VERSION` (default `2024-10-21`, a GA version with structured outputs), `AZURE_OPENAI_EMBEDDING_DEPLOYMENT`, `LLM_TIMEOUT_SEC` (120 s per request), `EMBEDDING_CACHE_TTL_DAYS` (30) and `LOCAL_MODEL_DIR` (where the local embedding model is downloaded). `ANTHROPIC_MODEL` now defaults to `claude-opus-5-5`, the current Opus model.
- **Consequences:** On machines where the repository lives in a synced folder (OneDrive), set `LOCAL_MODEL_DIR` outside it; the model is about 2 GB.

## D-014 — LLM providers: one retry policy, structured output, refusal fallback

- **Date:** 2026-10-10
- **Task:** M1-T02
- **Status:** accepted
- **Context:** NFR Reliability asks for 3 tries with exponential backoff. Both vendor SDKs also retry on their own.
- **Decision:** SDK retries are turned off (`max_retries=0`), and `LLMProvider` applies a single policy: 3 attempts with exponential backoff and jitter, on transient errors only (connection errors, timeouts, 408/409/429, 5xx). Refusals, truncated output and schema mismatches raise distinct errors and are not retried. Structured output uses each vendor's JSON-schema mode (Azure `response_format` with strict schemas; Anthropic `output_config.format`), and the result is always validated with Pydantic. The interface has no `temperature`, because current Claude models reject sampling parameters. The Anthropic provider sends `fallbacks: "default"` (beta `server-side-fallback-2026-07-01`), so a request declined by a safety classifier is re-run on a fallback model instead of failing. Provider tests run against an in-memory HTTP transport (both SDKs use `httpx2`, which `respx` can't mock).
- **Consequences:** Every call is traced (`llm.complete` / `llm.structured` spans with provider, model, tokens, attempts) and logged without content.

## D-015 — Local embeddings through fastembed (ONNX) instead of sentence-transformers

- **Date:** 2026-10-10
- **Task:** M1-T04
- **Status:** accepted
- **Context:** `multilingual-e5-large` is usually run with sentence-transformers, which pulls in PyTorch (several GB, CUDA wheels on Linux by default) into the API image and CI.
- **Decision:** Use `fastembed`, which runs the same model exported to ONNX on ONNX Runtime (CPU). The provider adds the `query: ` / `passage: ` prefixes and L2-normalises the vectors itself, because fastembed does neither for this model. Inference runs in a worker thread. The ~2.2 GB model is downloaded to `LOCAL_MODEL_DIR` on first use; tests use a deterministic fake embedder and never download it. A `live` test checks that the real model puts Hindi, Marathi and English text in one space.
- **Alternatives considered:** sentence-transformers + torch (much heavier); Azure embeddings only (paid per call, and the TRD default is local).
- **Consequences:** The M3 retriever embeds queries in-process. The API container will need the model cached in a volume (M12).

## D-016 — Crawler trust store fills in missing intermediate certificates

- **Date:** 2026-10-10
- **Task:** M1-T07
- **Status:** accepted
- **Context:** `pmkisan.gov.in` (sends unrelated GoDaddy G2 intermediates) and `www.nsiindia.gov.in` (sends no intermediate) present incomplete TLS chains. Browsers and Windows `curl` repair this by fetching the missing intermediate (AIA); Python's `ssl` with certifi, and OpenSSL on Linux, don't. Their `robots.txt` then counts as unreachable, which RFC 9309 treats as "disallow everything", so both sources were skipped.
- **Decision:** `src/sahayak/ingestion/extra_intermediates.crt` holds the four public certificates needed to complete those chains: the GoDaddy DV R1v1 intermediate, GoDaddy's TLS Root R1 cross-signed by Go Daddy Root G2, Let's Encrypt YE2, and ISRG Root YE cross-signed by ISRG Root X2. Each entry records its subject, issuer, expiry, SHA-256 fingerprint and source. The crawler's SSL context is certifi plus this file, with `VERIFY_X509_PARTIAL_CHAIN` explicitly cleared, so every chain must still end at a root that certifi trusts. Verification is never disabled.
- **Alternatives considered:** `verify=False` for these hosts (unsafe); `truststore` (uses the OS store, which repairs chains on Windows and macOS but not Linux, where CI and the containers run); a browser-like user agent (unrelated to the failure, and less honest).
- **Consequences:** With this file, all 15 manifest sources fetch with the descriptive `SahayakBot` user agent. The file is the API's only extension to certifi, and it's scoped to the crawler. An entry can be removed once its server is fixed (`openssl s_client -connect <host>:443` shows `Verify return code: 0` without it).

## D-017 — Chunk sizes are measured with a calibrated estimate of e5 tokens

- **Date:** 2026-10-10
- **Task:** M1-T09
- **Status:** accepted
- **Context:** TRD §8 asks for ~500-token chunks with 80 tokens of overlap. The tokens that matter are the embedding model's: multilingual-e5-large (XLM-R tokenizer) reads at most 512 tokens and silently drops the rest. Loading that tokenizer would tie the chunker, and its tests, to a 2 GB model download.
- **Decision:** `estimate_tokens` counts each word (including Devanagari vowel signs, which `\w` alone misses) as `ceil(len / 3.5)` tokens and each punctuation mark as 1. Measured against the real e5 tokenizer on all M1 sources, it never under-counts: the ratio of estimate to real was at least 1.03 overall, with a median of 1.41, and 1.4 on Hindi/Marathi prose. Chunks are packed by paragraph, then sentence (including the danda `।`), then word run, and never cross a section. Overlap is taken from the end of the previous chunk, by whole units first and then by words. Chunk ids are `uuid5(scheme_id + content_hash)`, where the hash covers source URL, section, language and text, so citations stay stable across re-ingests.
- **Alternatives considered:** The real tokenizer (exact, but heavy and slow in tests); tiktoken (an OpenAI tokenizer, which badly mis-counts Devanagari for e5).
- **Consequences:** On the M1 sources the largest chunk is 376 real e5 tokens, including the `passage: ` prefix and section heading, so nothing is truncated. Chunks run about 30% smaller than the 500-token target; a smaller divisor can be tuned later with the retrieval eval (M3-T05).

## D-018 — The indexer diffs chunks by content hash, per source

- **Date:** 2026-10-17
- **Task:** M1-T10
- **Status:** accepted
- **Context:** TRD §8 needs re-ingestion to be idempotent with no duplicate chunks, and embedding calls cost time (local e5) or money (Azure). Some sources fail to fetch on a given run (flaky government sites), and sources get added to or removed from the manifest.
- **Decision:** `index_scheme` upserts the `schemes` row from the manifest (keeping its lifecycle `status`; new rows start `provisional`). Then, for each fetched source, it compares stored and new chunk content hashes: new hashes are embedded and inserted, vanished ones are deleted, and kept chunks only have `chunk_index` updated if they moved. Chunks of sources no longer in the manifest are deleted. A manifest source missing from the run (fetch failed) keeps its stored chunks, and the scheme's `last_verified_at` and `content_hash` (over all source SHA-256s) are only updated when every source was indexed. The embedded text is `"<scheme name in the source's language>: <section>\n<chunk text>"`. Nothing commits inside the indexer: the caller uses one transaction per scheme.
- **Alternatives considered:** Delete and re-insert every chunk of a scheme (simple, but re-embeds everything and changes nothing for citations since ids are already content-based); skip a scheme whose source hashes are unchanged (saves one query per source, but would miss chunker changes).
- **Consequences:** An unchanged re-run makes no embedding calls and no writes to `scheme_chunks`. Changing the embedding model or the embedded-text format does **not** re-embed existing chunks, because their hashes don't change: that needs a full re-index (deleting the scheme's chunks first), which the M1-T12 CLI should offer. `tsv` is a generated column, so full-text search needs no indexer code.

## D-019 — Rule extraction: fixed-depth LLM schema, field catalogue, one draft per source text

- **Date:** 2026-10-17
- **Task:** M1-T11
- **Status:** accepted
- **Context:** The LLM drafts each scheme's `EligibilityRule` (TRD §8 step 6, §9.2) for human review. The rule DSL is a recursive tree, but Anthropic's structured outputs don't accept recursive JSON Schemas. `UserProfile` (M2-T02) and the full DSL (M2-T01) don't exist yet, but the LLM must only use real profile fields.
- **Decision:**
  - **Minimal DSL now.** `sahayak.eligibility.rules` holds the TRD §9.2 tree (`all` / `any` / `not` / conditions) as Pydantic models, plus `validate_logic` (unknown fields, operator vs. field type, enum choices, empty groups) and a readable `describe`. `sahayak.eligibility.fields` lists the 26 eligibility-relevant `UserProfile` fields from TRD §9.1 with their types and choices. M2-T01/T02 extend these, and a test keeps the static `ProfileFieldName` literal equal to the catalogue.
  - **LLM schema.** `RuleExtraction` is a fixed-depth form: top-level `all`/`any` over conditions or one level of groups, each of which can be negated. `field` is an enum of the catalogue names. There are no defaults or numeric constraints, because both providers' strict modes reject some keywords. Criteria the DSL can't express go to `unmapped_criteria` instead of being approximated. The result is converted to the TRD tree before saving.
  - **Source text.** All parsed sections of all of a scheme's sources are sent when they fit in 60,000 characters (true for every M1 scheme). Otherwise only sections whose heading or text matches eligibility keywords in English, Hindi or Marathi are sent, and the draft records `truncated`.
  - **Drafts.** A draft is skipped when a rule already exists for the same excerpts (`source_hash`). A new draft archives older unreviewed drafts of the scheme, and the approved rule stays live until a new one is approved. `extraction` records the provider, model, prompt id (`rule_extraction@v1`), self-reported confidence, unmapped criteria, the exact excerpts and any validation `issues`. A rule with issues can't be approved (see D-020).
- **Alternatives considered:** A recursive schema (works on Azure strict mode only); approximating unmapped criteria with the nearest field (silently wrong rules); one LLM call per section (loses cross-section context, such as eligibility vs. exclusions).
- **Consequences:** Two levels of nesting cover the M1 schemes (eligibility conditions plus exclusion lists). A deeper rule can still be written by hand in review. The prompt is versioned, so changing it means adding `rule_extraction.v2.md`. Re-drafting unchanged text needs `sahayak ingest --redraft`.

## D-020 — Ingestion CLI and rule review behaviour

- **Date:** 2026-10-17
- **Task:** M1-T12
- **Status:** accepted
- **Context:** TRD §8 asks for `sahayak ingest [--scheme ID]` and `sahayak rules review <scheme_id>` (approve, edit in `$EDITOR`, reject), with idempotent re-runs.
- **Decision:**
  - **Per scheme, two commits.** `ingest_scheme` commits the index first, then drafts the rule and commits again, so an LLM failure never loses indexed chunks. One failing scheme is reported and the batch continues. The exit code is 1 if any scheme fetched nothing or failed its rule draft.
  - **Rules only from complete text.** If any manifest source of a scheme fails to fetch, no rule is drafted that run (`incomplete sources`). Drafting from part of the text would mislead the reviewer, and a flaky source would otherwise create a new draft version on every run. A garbled source (D-012) counts as fetched and indexes as empty.
  - **Options.** `--skip-rules` indexes without an LLM (needed until LLM keys are configured), `--reindex` deletes and re-embeds a scheme's chunks (D-018), and `--redraft` drafts even when the text is unchanged. Without `--skip-rules`, missing LLM settings stop the command before anything runs.
  - **No Redis embedding cache in the CLI.** The indexer only embeds new chunks, so the cache would save almost nothing and would add a Redis dependency to ingestion.
  - **Review.** `rules review` shows the latest draft beside the exact excerpts the model read, with the readable rule, unmapped criteria, notes, source refs, confidence and validation issues. **Approve** is refused while issues remain. It archives the previously approved version and moves a `provisional` scheme to `active` (TRD §8A). **Edit** opens the rule's `logic`, `source_refs` and `notes` as JSON in `$EDITOR`, validates the shape, re-checks issues, sets `review_edited`, and commits right away so an edit survives quitting. **Reject** records an optional reason in `extraction.rejection_reason`. The reviewer's name comes from `--reviewer` or `SAHAYAK_REVIEWER`. `rules list` shows each scheme's live and pending versions.
  - **Entry points.** `sahayak` (`[project.scripts]`) and `python -m sahayak.cli`. The second works where Smart App Control blocks the generated `sahayak.exe` shim (D-007).
- **Alternatives considered:** Drafting from whatever was fetched (see above); a review web UI (later, with the admin API); a single transaction per scheme (an LLM outage would roll back indexing).
- **Consequences:** Rules exist only for schemes whose sources all fetched in the same run. The review is terminal-only and synchronous, and long source text scrolls in the terminal.

## D-021 — The fetcher retries a failed connection once

- **Date:** 2026-10-17
- **Task:** M1-T07 (found during the M1 exit run)
- **Status:** accepted
- **Context:** On the dev network (IPv6 with NAT64), the first TCP connection a process makes to `zpyavatmal.gov.in` often times out, and later ones succeed. The fetcher asks for `robots.txt` once per host per run, so that first attempt always failed. RFC 9309 then means "disallow everything", and `mh-mjpjay` lost a source on every run (5 runs in a row). Government hosts time out often in general (D-011).
- **Decision:** `ConnectError` and `ConnectTimeout` (no request reached the server) are retried once, for documents and `robots.txt`, with each attempt going through the per-domain rate limiter. Read timeouts and HTTP errors are not retried in the same run, because the server may already have processed the request; the next run will try again.
- **Alternatives considered:** Forcing IPv4 (works here, but breaks IPv6-only hosts); retrying every transport error (could double load on a struggling server).
- **Consequences:** An unreachable host costs at most two connect timeouts per run. With the retry, all 15 manifest sources fetched in the M1 exit run.

## D-022 — OpenAI's own API as a third LLM provider

- **Date:** 2026-10-17
- **Task:** M1-T02 (follow-up, during the M1 exit run)
- **Status:** accepted
- **Context:** The Azure OpenAI resource available for development (`maqopenai`) rejects requests from the dev machine with HTTP 403 ("Access denied due to Virtual Network/Firewall rules"). Prathmesh chose to use OpenAI's API directly instead.
- **Decision:** `LLM_PROVIDER=openai` with `OPENAI_API_KEY` and `OPENAI_MODEL` (default `gpt-4o`, which supports strict structured outputs). Azure OpenAI and OpenAI share the chat-completions code in `llm/openai_chat.py`; `AzureOpenAIProvider` and `OpenAIProvider` only build their SDK client. Error messages now name the provider (`openai HTTP 403`, `azure_openai HTTP 403`). Azure OpenAI stays the TRD default, so nothing else changes.
- **Alternatives considered:** Replacing Azure entirely (the TRD's production plan uses Azure); pointing the Azure client at OpenAI's endpoint (the URL and auth schemes differ).
- **Consequences:** The provider contract tests run against all three providers. Embeddings are unaffected (local e5 by default).
