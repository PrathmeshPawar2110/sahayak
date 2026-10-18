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

## D-023 — `UserProfile` is the single source of truth for rule fields

- **Date:** 2026-10-17
- **Task:** M2-T02
- **Status:** accepted
- **Context:** M1 kept a hand-written catalogue of the 26 eligibility fields (D-019). M2-T02 adds the TRD §9.1 `UserProfile`, and two copies of field types and choices would drift apart.
- **Decision:** `sahayak.eligibility.profile.UserProfile` follows TRD §9.1 field for field, with `FieldSource` from §8C. Only `language` is required. `None` always means "not known yet". Constraints the TRD implies are validated: ages 0–130, non-negative income, land and child counts, percentages 0–100. State codes are trimmed, upper-cased and must be two letters (ISO 3166-2:IN, e.g. `MH`). Unknown keys are rejected, assignments are validated, and `field_sources` keys must be profile fields. Sensitive fields (TRD §8B) carry `json_schema_extra={"sensitive": true}`. `eligibility.fields.PROFILE_FIELDS` is now derived from the model (kind, choices, description, sensitivity and a validator for each field's range or pattern). The static `ProfileFieldName` literal stays, for the LLM schema, and a test keeps it equal to the model.
- **Alternatives considered:** Keeping the catalogue separate (two sources of truth); a `"prefer_not_to_say"` value on every sensitive field (the TRD allows it only for `religion`; a declined field stays `None` and the planner is told not to ask again, see D-027).
- **Consequences:** Adding a profile field is a one-place change plus the literal. Rule values are checked against the same constraints as profile values, so a rule such as `state == "mh"` or `age < 200` is flagged.

## D-024 — Three-valued evaluation: `exists`, short-circuiting and what a result reports

- **Date:** 2026-10-17
- **Task:** M2-T03
- **Status:** accepted
- **Context:** TRD §9.2 asks for `NEEDS_INFO` when a field the result depends on is unknown, but leaves open what `exists` means for an unknown field, which conditions a result reports, and which missing fields count.
- **Decision:**
  - **Kleene logic.** `all` is FALSE if any item is FALSE, else UNKNOWN if any is UNKNOWN. `any` is the dual. `not` keeps UNKNOWN.
  - **`exists`** is TRUE when the field has a value and UNKNOWN when it is `None`, never FALSE. `None` means "not asked yet", so `exists` makes the planner ask for the field instead of silently ruling the scheme out (FR-27).
  - **Short-circuit.** A definite result reports only the conditions that decided it: a FALSE `all` lists its failed items and no missing fields, so a scheme that is already ruled out never drives a question. An ELIGIBLE result never lists a failed condition, and a NOT_ELIGIBLE one never lists a passed condition. A NEEDS_INFO result lists what is known so far and the unknown fields inside the undecided parts of the rule.
  - **Negation.** Each reported condition is a `ConditionCheck` with `negated` (under an odd number of `not`s) and `met`, so `not any(is_govt_employee == true)` reports "NOT (is_govt_employee == true)" as passed for a non-employee.
  - **Types.** `True` never equals `1`. Numbers compare as numbers. A type mismatch raises `ValueError`, which can't happen with validated rules.
- **Alternatives considered:** `exists` as FALSE for `None` (rules out schemes before asking); reporting every leaf (an ELIGIBLE result would list failed alternatives of an `any`).
- **Consequences:** Kleene logic is sound but not complete: `any(age > 5, age <= 5)` stays NEEDS_INFO until age is known. Rules don't contain tautologies, so this costs at most an extra question. Property tests check soundness (a definite verdict never changes when more fields are filled in), agreement with two-valued logic on complete profiles, and that answering a field never adds missing fields.

## D-025 — Rule validation on save: drafts may hold issues, approved rules may not

- **Date:** 2026-10-17
- **Task:** M2-T04
- **Status:** accepted
- **Context:** M2-T04 asks that unknown fields and operators are rejected and value types checked against `UserProfile` when a rule is saved. LLM drafts often contain such mistakes (3 of the 10 M1 drafts do), and D-019 keeps them so the reviewer can see and fix them.
- **Decision:** Validation happens at three points.
  - **Shape** (`parse_logic`): an unknown operator or combinator, or a malformed tree, is rejected before anything is stored, for drafts too (extraction and `rules review` edits already call it).
  - **Meaning** (`validate_rule` / `rule_issues`): unknown fields, operators that don't suit the field, values outside the field's type, choices, range or pattern, empty groups, and duplicate document requirements. Drafts store these as `issues`, and approval is refused while any remain (D-020).
  - **ORM guard:** a SQLAlchemy `before_insert` / `before_update` hook on `EligibilityRule` runs `validate_rule` whenever the row's status is `approved`. No code path, including future admin endpoints, can save an invalid live rule. Rules loaded for evaluation are validated again.
- **Alternatives considered:** Rejecting invalid drafts at extraction (the reviewer would lose the draft and its excerpts); validating only in `approve_rule` (other write paths would bypass it).
- **Consequences:** `sahayak.db.models` imports `sahayak.eligibility.rules`, which is pure Pydantic. A failed check raises `RuleValidationError` at flush time, listing every issue.

## D-026 — Approved rules are exported to `data/rules/` for CI

- **Date:** 2026-10-17
- **Task:** M2-T04, M2-T09
- **Status:** accepted
- **Context:** The M2 exit criterion runs the eligibility eval in CI on approved rules. Approved rules live in Postgres and come from LLM ingestion plus human review, so CI can't rebuild them.
- **Decision:** `sahayak rules export` writes every approved rule to `data/rules/<scheme_id>.json`, with `schema.json` (the rule JSON Schema). It removes files of schemes that no longer have an approved rule. The files are committed. The eval reads them by default (`--rules files`). `--rules db` reads approved rules from the database, and `--rules latest` previews the newest drafts for reviewers. Loading validates every file, accepts only `approved` rules, and checks that file names match `scheme_id`. A unit test fails if `schema.json` is out of date.
- **Alternatives considered:** Seeding the CI database from a dump (binary, and harder to review); evaluating in CI only what the LLM drafts (not reproducible, and it would call a paid API).
- **Consequences:** After approving rules, run `sahayak rules export` and commit the snapshot. The diff shows every rule change in review. The database stays the source of truth: the agent (M4) loads rules from it, not from the files.

## D-027 — Question planner: tie-breaks, declined fields and output

- **Date:** 2026-10-17
- **Task:** M2-T05
- **Status:** accepted
- **Context:** TRD §10 `plan_question` picks the missing field that appears in the most NEEDS_INFO rules. It doesn't say how to break ties, what to do when a user declines a sensitive question (TRD §8B), or what the planner returns.
- **Decision:** `next_question(results, profile, questions_asked=, max_questions=8, declined=())` returns a `PlannedQuestion` (field, the NEEDS_INFO scheme ids it feeds, whether it's sensitive) or `None`. It returns `None` when `questions_asked` has reached the limit, or when no NEEDS_INFO scheme has an askable field. Each scheme counts once per field. Ties go to non-sensitive fields, then to `UserProfile` field order, so the choice is deterministic. Known fields (re-checked against the profile) and `declined` fields are never asked. Turning the field into a question in the user's language is the agent's job (M4).
- **Alternatives considered:** Never asking sensitive fields while any other field is missing (would hide a scholarship that only needs the category until every unrelated question is answered); weighting by scheme benefit (no benefit data yet).
- **Consequences:** The agent must track declined fields per session. A sensitive field is asked first only when strictly more schemes need it than any other field.

## D-028 — Eligibility eval set v0: independent reference labels and their judgement calls

- **Date:** 2026-10-17
- **Task:** M2-T07
- **Status:** accepted (all 30 labels and the judgement calls below approved by Prathmesh on 2026-10-18, M2-T08)
- **Context:** TRD §12.1 asks for programmatically generated profiles with hand-checked labels. If the labels came from the rules under test, the eval would always pass. Many official criteria can't be expressed with `UserProfile` fields.
- **Decision:**
  - **Independent reference.** `sahayak.evals.reference` writes each scheme's criteria again in plain Python (its own three-valued logic, not the engine or the DSL), from the official source text in the manifest. It labels 30 hand-designed personas: typical citizens, boundary variants (ages 9/10, 17/18, 40/41, 21, 65/66, 70; incomes exactly at and Rs 1 over Rs 1,80,000 and Rs 2,50,000) and four profiles with missing fields. Every scheme gets ELIGIBLE, NOT_ELIGIBLE and NEEDS_INFO labels. Each label carries a one-line reason. `label_status` is `generated` until a human checks it and sets `checked`. The generator refuses to overwrite checked labels, and a test keeps unchecked labels equal to the generator.
  - **Unexpressible criteria.** A further restriction that the application process checks (no LPG connection in the household, a kutcha house, a bank account, no EPF/ESIC/NPS cover, one beneficiary per family) is assumed to be met. An alternative route that can't be expressed (PM-JAY's SECC 2011 lists) doesn't count, so only the expressible route can make the scheme ELIGIBLE.
  - **Judgement calls for the M2-T08 check:**
    - PM-SYM and PMAY-G "monthly income ≤ Rs 15,000" is approximated as annual family income ≤ Rs 1,80,000. PM-SYM's unorganised workers are farmer, agri_labourer, daily_wage, street_vendor, artisan and self_employed (not student, salaried, homemaker, unemployed or other).
    - PM-KISAN needs land > 0 and no government employee or income-tax payer, whatever the person's own occupation. Government employees are also excluded from PM-SYM (NPS cover).
    - AB PM-JAY: age ≥ 70 outside DL, WB and OD (the September 2024 source; Delhi and Odisha may have joined since). Under 70 is NOT_ELIGIBLE, because the SECC route can't be expressed.
    - PMAY-G doesn't use "irrigated land > 2.5 acres", because the profile can't tell irrigated land from other land.
    - SSY: under 10 in completed years, so a girl who has turned 10 is NOT_ELIGIBLE.
    - Majhi Ladki Bahin: any marital status qualifies, because "one unmarried woman in the family" is eligible. State means residence (`state`), not `domicile_state`.
- **Alternatives considered:** Labelling from the approved rules (circular); random profiles only (few boundary cases among 30).
- **Consequences:** A preview against the 10 M1 drafts (`sahayak eval --rules latest --no-record`) gives 97.7% (293/300). All 7 mismatches are mistakes in the drafts that review should fix: the PM-KISAN non-creamy-layer exclusion, the PM Vishwakarma trade names and BPL condition, SSY age ≤ 10, Ladki Bahin excluding unmarried women, and PMAY-G's caste and BPL alternatives. Because most labels are NOT_ELIGIBLE, overall accuracy flatters. The per-scheme ELIGIBLE recall in the report is the more telling number. M12 grows the set to 200 profiles.

## D-029 — Hybrid retrieval: filters, full-text matching and fusion

- **Date:** 2026-10-18
- **Task:** M3-T01, M3-T02, M3-T03
- **Status:** accepted
- **Context:** TRD §7 and §10 ask for pgvector plus Postgres full-text search fused with RRF, top-k 5, filtered by scheme status and state. They don't say what "filtered by state" means when the state is unknown, how a natural-language question becomes a tsquery, or how filtering interacts with the HNSW index.
- **Decision:**
  - **Filter.** By default only `active` and `provisional` schemes are searched (TRD §8A: discontinued schemes only when asked by name, M9; archived never). With a user state, central schemes plus that state's schemes match. With no state yet, every state matches: retrieval only finds text, and the rule engine decides eligibility. `scheme_ids` restricts a search to named schemes (evidence for eligible or selected schemes, M4). `ends_at` isn't checked here: M9 moves a scheme past its end date to `discontinued`.
  - **Vector search** orders by cosine distance and uses the HNSW index. pgvector returns at most `hnsw.ef_search` (40) neighbours before filtering, so a selective filter can return fewer hits. That's harmless for the default filter at the MVP's size, but not for `scheme_ids`, so a scheme-restricted search is exact: a `MATERIALIZED` CTE filters and computes distances first, then sorts.
  - **Full-text search** ORs the query's terms: `plainto_tsquery` normalises them, and its ` & ` operators are replaced with ` | ` (lexemes are quoted, so their contents can't change). An AND of every word in a question ("which farmer families are covered?") almost never matches. English chunks match the query normalised with `english`, Hindi and Marathi chunks with `simple`, as the generated `tsv` column indexes them. Ranking is `ts_rank` with normalisation 1 (divided by 1 + log length). Both branches use the GIN index (a BitmapOr).
  - **Fusion.** Each search contributes its top 20 (`CANDIDATES`); RRF with k = 60 fuses them, a chunk counts once per list at its best rank, ties go to the better best rank and then the chunk id, and the top 5 are returned with each list's rank for debugging.
  - **Logging.** Query text can carry personal details, so the retriever logs and traces only counts, scheme ids and latency.
- **Alternatives considered:** `websearch_to_tsquery` (AND semantics, same problem); weighting vector scores against `ts_rank` (the scales aren't comparable, which is why RRF is used); pgvector iterative index scans (`hnsw.iterative_scan` needs pgvector 0.8, and the local pgserver has 0.6.2); excluding schemes outside a known state from vector search only (the two lists would disagree).
- **Consequences:** Hindi and Marathi chunks match only exact word forms in full text (no stemmer); the vector search covers inflections. If the corpus grows far beyond the MVP, unrestricted filtered searches may need `hnsw.ef_search` raised or iterative scans.

## D-030 — Hindi and Marathi queries are also searched in English

- **Date:** 2026-10-18
- **Task:** M3-T04
- **Status:** accepted
- **Context:** Every M1 chunk is English (D-012). multilingual-e5 maps hi/mr/en into one space, but full-text search can only match shared words (in practice numbers), so it contributes almost nothing to a Hindi or Marathi query.
- **Decision:** `HybridRetriever` takes an optional `QueryTranslator` and the user's language. A query is translated when the language is `hi` or `mr` (including romanised Hinglish) or when it contains Devanagari. `LLMQueryTranslator` uses the configured LLM with the versioned prompt `query_translation@v1` (structured output, user text wrapped as data). Both queries are embedded in one call, each gets a vector and a full-text list, and all four lists go into one RRF, so a chunk found in both languages ranks higher. A failed translation (any `LLMError`) is logged by error type and the original query is still searched; a translation equal to the query is dropped. English queries are never translated.
- **Alternatives considered:** Translating chunks into hi/mr at ingestion (TRD §8 step 4, optional; it multiplies the index and still misses romanised queries); a separate translation API (another provider for one feature; the LLM is already configured); translating only for full-text search (the English vector query also helps, see the eval in D-031).
- **Consequences:** A Hindi or Marathi turn costs one short LLM call (about a second with gpt-4o). Translations aren't cached yet; the query embeddings are (Redis, D-015). The agent (M4) can pass its own translation if it already has one.

## D-031 — Retrieval eval: phrase-based targets, on-demand runs

- **Date:** 2026-10-18
- **Task:** M3-T05
- **Status:** accepted
- **Context:** M3's exit criterion is recall@5 ≥ 0.85 on about 30 queries with an expected scheme or chunk. Chunk ids are content hashes (D-017), so they change whenever a source is re-fetched or re-chunked. Scoring needs the real ingested index and the 2 GB embedding model, neither of which CI has.
- **Decision:**
  - **Dataset.** `evals/retrieval.jsonl`: 31 queries written as users would ask them (11 English, 10 Hindi including one romanised Hinglish query, 10 Marathi, with the TRD §3 farmer persona), covering all 10 schemes. Marathi queries and Maharashtra-scheme queries carry `state: MH`, which is applied as the search filter. A target is a scheme, optionally narrowed with `contains` phrases: a chunk matches if its section or text contains any of them (case-insensitive). 11 targets are chunk-level (document lists, contribution amounts, exclusions, the 70+ extension, benefit amounts). Two queries have two targets (PM-SYM and APY for "pension after 60"). The queries were written before the first run and not tuned to it.
  - **Metrics.** recall@5 is the share of a query's targets found in the top 5, averaged over queries; also hit@1 and MRR, overall and per language. `sahayak eval --suite retrieval` writes `evals/reports/<date>-retrieval.md`, records an `eval_runs` row (embedding model, translator prompt, dataset hash) and exits 1 below `--min-recall` (0.85). `--no-translate` runs without the LLM.
  - **Where it runs.** On demand, against a database with the ingested schemes, like the LLM suites (TRD §12). CI covers the runner, the metrics and the CLI with the synthetic corpus and the fake embedder, and checks the committed query set (languages, valid scheme ids, every scheme targeted).
- **Results (2026-10-18, M1 index: 165 English chunks, multilingual-e5-large, gpt-4o translation):**

  | Run | recall@5 | hit@1 | MRR | hi hit@1 | mr hit@1 | en hit@1 |
  |---|---|---|---|---|---|---|
  | with translation (default) | 0.919 | 0.871 | 0.895 | 0.80 | 0.90 | 0.91 |
  | without translation | 0.919 | 0.742 | 0.825 | 0.60 | 0.70 | 0.91 |

  Translation leaves recall unchanged but moves the right chunk to the top much more often for Hindi and Marathi (D-030). It fixes the TRD persona query (q022, Marathi "I have two acres of farmland"), which misses without it. Remaining misses with translation: q002 (all five results are PM-SYM chunks, so APY, the second target, is crowded out), q021 (the Hinglish "who doesn't get PM-KISAN" finds PM-KISAN but not its exclusions chunk), and q024 (the Ladki Bahin eligibility question finds the scheme's overview, which also gives the age range, but not its eligibility chunk).
- **Alternatives considered:** Chunk-id targets (break on every re-chunk); scheme-only targets (too lenient for evidence retrieval, which needs the passage); running the suite in CI (would need the model and a full ingest in every run).
- **Consequences:** The numbers depend on the local index. Re-run after any ingestion, chunking or retriever change. The set is small and written by one person, so it should get a human spot-check and grow with the scheme list (M12). q002 suggests the agent's shortlist (M4-T04) should take candidate schemes from more than the top 5 chunks, or cap chunks per scheme.

## D-032 — Agent state and the Postgres checkpointer

- **Date:** 2026-10-18
- **Task:** M4-T01
- **Status:** accepted
- **Context:** TRD §7 and §10 ask for a LangGraph agent whose state is kept durably by the Postgres checkpointer. The checkpointer uses psycopg (the app uses asyncpg), creates its own tables with `setup()`, and by default rebuilds any Python class named in a stored checkpoint.
- **Decision:**
  - **State.** `AgentState` is TRD §10's state plus what the nodes carry between turns (questions asked, declined fields, contradictions to confirm, the user's need and the schemes they named) and per-turn fields (the LLM's reading of the message, the reply). Nodes get their dependencies (LLM, catalogue, passage search, config, clock) through LangGraph's runtime context, never through the state, and reach the database only through the `CatalogueSource` and `PassageSearch` interfaces, so the whole graph runs in tests without Postgres.
  - **One thread per session.** `thread_id` is the session id. Turns run with `durability="exit"`: one checkpoint per turn instead of one per node, and a turn that fails part-way leaves no trace in the state.
  - **Tables from Alembic.** Revision 0003 creates the library's tables (its migrations 0–9; the thread_id indexes without `CONCURRENTLY`, since the tables start empty) and marks them applied, so `setup()` is a no-op and the API never changes the schema on start (D-006). Alembic's `check` ignores these tables. A unit test fails when a library upgrade adds migrations, so a new revision has to apply them.
  - **Strict deserialisation.** The serializer gets an explicit allowlist of the Pydantic models stored in the state. Anything else in a checkpoint comes back as a plain dict instead of being imported and called.
  - **Own psycopg pool** (autocommit, dict rows, no prepared statements) next to the asyncpg engine. On Windows, psycopg's async mode needs the selector event loop, so the test suite (and the local dev server) sets `WindowsSelectorEventLoopPolicy`.
- **Alternatives considered:** a custom checkpointer on asyncpg (more code to maintain than a second driver); calling `setup()` on API start (D-006); LangGraph's default permissive deserialisation (it executes whatever a checkpoint names).
- **Consequences:** Checkpoints hold the profile, including sensitive fields, as JSON in Postgres. Column-level encryption of sensitive fields comes with M10-T10, and expired anonymous sessions (30 days) must be deleted together with their checkpoint thread (M12 retention job).

## D-033 — One LLM call understands a turn; profile merging is deterministic

- **Date:** 2026-10-18
- **Task:** M4-T02, M4-T03
- **Status:** accepted
- **Context:** TRD §10 has two LLM steps before any eligibility work: `classify_intent` (intent, language) and `extract_profile` (structured profile facts). Each is a gpt-4o call of about a second, and a text turn should take about 2 s at p50 (TRD §5). Shortlisting and evidence retrieval also need English search text for Hindi and Marathi users (D-030), which would be a third call.
- **Decision:**
  - **One call.** `classify_intent` makes one structured call (prompt `turn_understanding@v1`) that returns the intent, the message's language, the profile facts stated, fields the user declined or doesn't know, scheme names mentioned, and an English search phrase. `extract_profile` merges the facts without an LLM.
  - **Facts as text.** Each fact is a field name (an enum of the profile fields) and a value as text in a canonical form the prompt describes (digits, `true`/`false`, a listed choice, a state code; lakh and monthly amounts converted). The merge parses the text for the field's type and validates it with `UserProfile`. Invalid values are dropped, never guessed at.
  - **Merge rules.** A value fills an unknown field and records `FieldSource(self_declared, confidence 1.0)`. A value that disagrees with a known one becomes a `Contradiction` the agent asks about (FR-14) and doesn't overwrite it. The answer to that confirmation is taken as given; without an answer, the known value stays. Declined fields are remembered so the planner doesn't ask them again (D-027).
  - **Sensitive fields.** Without the session's `sensitive` consent, the prompt tells the model not to output caste, religion, minority or disability fields, and the merge drops any that come back anyway. The prompt also forbids inferring any value from names, writing style, language or location.
  - **Language.** The detected language becomes the session's language, except for a message with no letters ("45"), which keeps the current one.
  - **Untrusted text.** User messages (and later scheme passages) go into prompts inside tags with `<`, `>` and `&` escaped, so they can't close their tag, and every prompt says tag contents are data.
- **Alternatives considered:** two calls, as listed in the TRD (about a second slower per turn, and the second call would need the first's output anyway); extraction with the planner's pending field only (misses volunteered facts, which make conversations shorter).
- **Consequences:** The understanding prompt is long, since it carries the field catalogue (about 1,500 tokens, cacheable as a prefix). A wrong intent from this one call also affects extraction; the dialogue eval (M4-T13) measures the combined effect.

## D-034 — Shortlisting, the state guard and provisional schemes in M4

- **Date:** 2026-10-18
- **Task:** M4-T04, M4-T05, M4-T06
- **Status:** accepted
- **Context:** TRD §10 shortlists candidates "by state, plus hybrid retrieval on the user's stated need", then filters the rule engine's verdicts (TRD §8C). No rule is approved yet (M1-T14), and all 10 M1 schemes are `provisional` with draft rules, so with approved rules only the agent could never show a result.
- **Decision:**
  - **Catalogue rule per status.** `active` schemes are evaluated with their approved rule only. `provisional` schemes use their approved rule if any, otherwise their newest draft, while `SHOW_PROVISIONAL_SCHEMES` is on (TRD §8A: "draft rules are evaluated, but the result is always labelled"). Discontinued and archived schemes get no rule and are never candidates. The catalogue is cached for 60 s, since several nodes read it each turn.
  - **Shortlist.** Without a stated need, every scheme with a rule that may apply in the user's state is evaluated (all of them while the state is unknown): about 10 to 50 rule evaluations, a few milliseconds. A need ("pension") keeps only schemes whose passages match it among the top 20 retrieved (D-031 noted that 5 is too few), and a need that matches nothing narrows nothing. Schemes the user names are always evaluated, even in another state, so the reply can give their true verdict. The need and the named schemes carry over to answers to follow-up questions; a general request ("which schemes can I get?") clears them.
  - **State guard.** A state scheme is evaluated as `state == <its state> AND <rule>`, so it can't be ELIGIBLE for someone from another state, or before the state is known (then it is NEEDS_INFO on `state`, and the planner asks for it), even if a draft forgot the condition.
  - **Filter.** Only ELIGIBLE schemes are shown. Active ones go to `ready`: with no `required_documents` checked before M10, a rule has nothing left to check. Provisional ones go to the separate `provisional` group, which replies always label as unverified with the official link (this pulls the basic part of M9-T13 into M4). Drafts that fail to evaluate (an unknown field) are skipped with a warning.
- **Alternatives considered:** approved rules only (no result until M1-T14, and the TRD allows labelled provisional verdicts); always shortlisting by retrieval (a general request would depend on which passages happen to rank first); trusting each state scheme's rule to test the state (drafts don't always).
- **Consequences:** Until M1-T14, every result is labelled "recently announced, not yet verified". The drafts' known mistakes (D-028) show up in conversations; for example, PM-KISAN's draft asks for non-creamy-layer status. M9-T13 adds the discontinued-scheme replies, and M10 the document and window checks.

## D-035 — Asking questions: when, which, and how they are worded

- **Date:** 2026-10-18
- **Task:** M4-T07
- **Status:** accepted
- **Context:** TRD §10 `plan_question` uses the planner (D-027) and "phrases the question naturally in the user's language". It doesn't say which turns ask, how a contradiction is confirmed (FR-14), or what happens when the LLM's wording fails.
- **Decision:**
  - **When.** Only `discover` turns ask questions. "Am I eligible for X?" is a `discover` turn naming X (the understanding prompt says so); `ask_scheme_info` ("what documents does X need?") is answered without questions. The router and the node share one function (`sahayak.agent.questions`), so they always agree.
  - **Which.** An unconfirmed contradiction comes first. Otherwise the planner's choice. When the user named schemes, only those drive questions, so "am I eligible for PM-KISAN?" asks only what PM-KISAN needs and then answers. Sensitive fields are never asked without the session's sensitive-data consent (they count as declined). Every question, confirmations included, counts towards `MAX_QUESTIONS_PER_SESSION` (8). After that the agent replies with what it can show.
  - **Wording.** The LLM words the chosen question (prompt `question_wording@v1`, about 0.5 s): simple words in the user's language, the answer choices, and for sensitive fields a note that some schemes are only for certain groups and that they can skip it. Every profile field also has fixed wording in Hindi, Marathi and English (`agent/replies.yaml`), used when the LLM call fails, or when its question is empty, over 400 characters or names any scheme (a question must not hint at a scheme the user may not qualify for). Confirmations always use fixed wording with both values ("earlier you said 45, and now 54").
  - **Fixed replies as data.** Notices, resets, fallbacks and labels live in `replies.yaml`. A test checks every entry has all three languages with the same placeholders and every profile field has a question.
- **Alternatives considered:** fixed wording only (no LLM latency, but stiff, and it can't acknowledge what the user just said); asking sensitive questions without consent and dropping the answer (asks for data that would then be thrown away).
- **Consequences:** A question turn costs two LLM calls (understanding, then wording). The Hindi and Marathi fixed wording was written by the developer and should get a native-speaker review before launch (M6-T10's user test is a natural point).

## D-036 — Replies: structured drafts, the post-check and the fixed fallback

- **Date:** 2026-10-18
- **Task:** M4-T08, M4-T09
- **Status:** accepted
- **Context:** TRD §10 asks for replies grounded in retrieved passages with citations (`chunk_id`), a post-check against `display_results` (regenerate once, then a template), and prompt-injection-safe handling of scheme text. TRD §8A requires a caveat on provisional verdicts, and FR-20 a "last verified" date on scheme information.
- **Decision:**
  - **Evidence per scheme.** `retrieve_evidence` searches each scheme the reply is about separately (top 2 passages each, at most 5 schemes): the displayed schemes on a discover turn, otherwise the named or chosen ones. A shared top 5 would let one scheme crowd out the others (D-031, q002). Named schemes are searched whatever the user's state, so a "not eligible, you don't live in Maharashtra" can be explained.
  - **Structured drafts.** The LLM (prompt `response_composition@v1`) returns an opening, points and a closing. Each point is about at most one scheme, cites passage ids, and labels the verdict it states. It receives only the schemes it may discuss: the displayed ones (all ELIGIBLE) and the named ones, each with the engine's verdict, the conditions met and not met, and the missing details.
  - **Post-check.** A draft is rejected if a point is about a scheme it may not discuss, cites nothing, cites a passage that wasn't retrieved or that belongs to another scheme, or states a verdict different from the engine's; or if the text anywhere names a scheme outside the allowed set (catalogue name and alias matching, D-034). Rejected drafts are regenerated once, with the reasons. If the second draft fails too, or the LLM is down, a fixed reply lists only the engine's verdicts (`replies.yaml`), so an LLM outage still gives a useful answer.
  - **Added after the LLM, never by it:** citation markers `[n]` numbered in order of first use, a "not yet verified" caveat with the official link for every provisional scheme discussed, and a sources list with each passage's section, URL and the scheme's last-verified date (dd-mm-yyyy). The caveat text is written for M4's situation (rules not yet reviewed), not TRD §8A's "announced recently", which would be wrong for PM-KISAN.
  - **Untrusted text.** Passages go into `<document>` tags with `<`, `>` and `&` escaped (attributes quoted), so a passage can't close its tag, and the prompt says tag contents are data (D-033).
  - **Fixed replies.** Greetings, out-of-scope messages and features that arrive later (documents in M10, reminders in M11) get fixed replies without an LLM call. Form requests (M7) get the scheme's details plus "forms are coming soon".
- **Alternatives considered:** free-text replies checked by name matching alone (can't verify citations or stated verdicts); asking the LLM to add the caveats (it can forget them; TRD §8A requires them).
- **Consequences:** The verdict label is the LLM's own description of its point, so a point labelled `none` could still imply eligibility in its wording. Name matching and the prompt reduce the risk, and the groundedness eval (M12-T05) will measure it. The sources list is part of the reply text, so the voice channel (M5) should speak the reply without it.

## D-037 — PII detectors: what they catch, and where they run

- **Date:** 2026-10-18
- **Task:** M4-T10
- **Status:** accepted
- **Context:** TRD §10 asks for regex detectors for Aadhaar, bank account, OTP and phone numbers, used before logging and before the profile is saved, and a polite "not needed" reply. Indian users may type numbers in Devanagari digits, and bank account numbers have no fixed format (9 to 18 digits).
- **Decision:**
  - **Detectors** (`sahayak.privacy.pii`), in priority order where matches overlap: OTP (4–8 digits within a few characters of "OTP", "one-time password", "password", "ओटीपी" or "पासवर्ड", before or after the number); Aadhaar-like (12 digits starting 2–9, optionally grouped 4-4-4); phone (10 digits starting 6–9, optional +91 or 0); bank account (any run of 9–18 digits, or grouped digits next to an account word such as "a/c", "खाता" or "बँक"). Devanagari digits are matched like ASCII ones. Number edges exclude Latin letters, so digits inside trace and request ids aren't masked. "Code" and "PIN" alone don't count as OTP words (postal PIN codes).
  - **Masking** keeps the last 4 digits (`XXXX-XXXX-0123`, `XXXXXX3210`, `XXXXXXXX2345`); OTPs are masked whole.
  - **Where they run.** On the user's message before anything else (M4-T12): the agent, the LLM, the checkpoint and the `messages` table only ever see the masked text, and the reply starts with the "please don't share these, I haven't saved them" notice. In `extract_profile`, a fact whose text looks like an identifier is dropped (defence in depth). In log redaction, which now uses the same detectors instead of its own two regexes.
- **Alternatives considered:** checksums (Aadhaar's Verhoeff check would miss typos, and a near-miss is still sensitive); a named-entity model (heavier, and these identifiers are numeric patterns).
- **Consequences:** Any 9–18 digit run is treated as a possible account number, so such a number in a log line or message is masked too (an existing redaction test that expected a 14-digit "order number" to stay visible was changed). An income written as 9 or more plain digits (₹10 crore and up) would be masked, which is irrelevant for welfare schemes.

## D-038 — The session API: consent, persistence, deletion and failures

- **Date:** 2026-10-18
- **Task:** M4-T12
- **Status:** accepted
- **Context:** TRD §11 lists the session endpoints but not how consent gates them, what each turn stores, what withdrawing consent erases, or how a failing LLM shows up to clients.
- **Decision:**
  - **Consent.** `POST /v1/sessions/{id}/consent` takes `{purposes, granted, policy_version}` for the purposes an anonymous session can have: `profile` and `sensitive` (vault and reminder consents need accounts, M10). Consents are stored per purpose with time and policy version in `sessions.consents` (revision 0004). Messages are refused with 409 `consent_required` until `profile` is granted, since a turn saves what the user says. Without `sensitive`, sensitive fields are neither asked nor kept (D-033, D-035). Withdrawing `profile` erases the profile row, the message log and the conversation state; withdrawing `sensitive` removes sensitive fields from the profile row and from the saved agent state at once (DPDP: stop processing and erase).
  - **A turn.** The session row is locked (`SELECT … FOR UPDATE`) for the whole turn, so two messages to one session run one after the other instead of racing on the checkpoint. The user's text is masked first (D-037). The masked message and the reply go to `messages` (with language and latency), the profile to `profiles` (values and field sources, for `GET /profile`), and the session's language is updated. The expiry slides: a session expires 30 days after its last message (`SESSION_TTL_DAYS`), and an expired session answers 410 `session_expired`.
  - **Response.** `{reply_text, reply_audio_url: null, kind, language, intent, question_field, eligibility: {ready, action_needed, provisional}, citations[], session_deleted}`. `eligibility` is always the strict filtered list (TRD §8C), also while questions are still being asked, with each scheme's name in the user's language, the conditions met, the official link and the last-verified date.
  - **Deletion.** `DELETE /v1/sessions/{id}` and "delete my data" in chat delete the session row (profile and messages cascade) and every checkpoint of its thread. Deletion also works on expired sessions.
  - **Failures.** If the LLM (after its retries) or the embedder fails, the turn returns 200 with `kind: "error"` and a polite reply in the user's language, and nothing in the conversation state changes. Without LLM settings the app still starts; chat answers 503 `agent_unavailable`, while deletion and consent withdrawal keep working.
  - **Catalogue endpoints.** `GET /v1/schemes?state=&language=` lists active and provisional schemes (central plus the state's); `GET /v1/schemes/{id}` also finds discontinued ones; archived schemes are 404.
  - **Embedding cache fails open.** The Redis embedding cache (M1) now computes vectors directly when Redis is unreachable, so a cache outage costs latency instead of failing every turn.
- **Alternatives considered:** a 503 for LLM failures (clients, and WhatsApp later, would each need their own error wording); a fixed 30-day expiry from creation (an active conversation would expire mid-use); a Postgres advisory lock (the row lock already exists and is released with the transaction).
- **Consequences:** A turn holds a database connection and a row lock for its whole duration (a few seconds), which caps concurrent turns at the pool size (5 + 10 overflow). That is acceptable for the MVP; M12's load test will show whether the pool needs to grow. On Windows, run the dev server with `--reload` (`just dev`): uvicorn then uses the selector event loop that psycopg needs.

## D-039 — Per-node tracing: latency, tokens and cost

- **Date:** 2026-10-18
- **Task:** M4-T14
- **Status:** accepted
- **Context:** TRD §5 asks for OpenTelemetry traces and structured logs covering every LangGraph node and LLM call, with latency, tokens and cost. LLM calls happen deep inside nodes, and nodes run in LangGraph's own tasks.
- **Decision:**
  - **Spans.** Every node runs inside a span `agent.<node>` (via a wrapper applied when the graph is built), and a turn inside `agent.turn`. The existing `llm.complete` / `llm.structured` spans nest under them. Span attributes: node name, latency, LLM calls, input and output tokens and cost.
  - **Logs.** Each node logs `agent_node` (node, status, latency, LLM calls, tokens, cost) and each turn `agent_turn` (intent, language, reply kind, guard outcome, number of schemes shown, number of identifiers masked, latency, LLM totals). Neither ever carries message text or profile values; a test checks that a name in the message appears in no log event or span attribute.
  - **Usage meters.** `metered()` opens a meter in a context variable; every successful LLM call adds its tokens and cost to every open meter, so a node's meter and its turn's meter both count it, across LangGraph's tasks. The turn's totals are also returned to the caller (the dialogue eval reports them).
  - **Cost** uses list prices per million tokens, matched by the longest prefix of the model name the API returns: gpt-4o $2.50 / $10.00, gpt-4o-mini $0.15 / $0.60 (OpenAI standard processing). A model without a listed price makes the cost unknown (null), never zero.
- **Alternatives considered:** LangSmith or Langfuse callbacks (another vendor and account; TRD §7 makes Langfuse optional); estimating cost per node from token counts afterwards (needs the model per call, which only the provider knows).
- **Consequences:** Prices must be updated by hand when a model or its price changes. Claude models have no price yet, so with `LLM_PROVIDER=anthropic` costs are logged as unknown until one is added. Retries count once, with the tokens of the call that succeeded.

## D-040 — Dialogue eval: personas, two simulated users, and what is scored

- **Date:** 2026-10-18
- **Task:** M4-T13
- **Status:** accepted
- **Context:** TRD §12.2 asks for about 30 scripted multi-turn dialogues in hi, mr and en, played by a simulated user (an LLM acting from a fixed profile), measuring final verdict accuracy, questions asked and turns. M4's exit criterion also requires that no non-eligible scheme ever appears in a reply, and CI must not call paid APIs.
- **Decision:**
  - **Dataset** (`evals/dialogues.jsonl`): one dialogue per persona of the hand-checked eligibility set (p001–p030), so the reference verdicts are the checked labels. Conversation languages are balanced at 10 each (personas' own languages kept where possible, 7 English ones moved to hi or mr). Each dialogue has a natural opening written as a person would say it ("माझ्याकडे दोन एकर शेती आहे…", TRD §3's farmer), the profile facts the opening states, and for four dialogues a topic or a named scheme with the schemes it is scored on (`scope`: savings for a daughter, pension, "can I get PM Kisan?", "will I get Ayushman Bharat?"). Openings state only facts said in words, never ones read from grammatical gender (D-033).
  - **Two simulated users.** `ScriptedUser` writes facts as `field=value` and "I don't know" as `unknown=<field>`. With a fake LLM that reads that format, all 30 dialogues run in CI on the M1 catalogue fixture (the 10 draft rules) in about a second. That fake composer is adversarial: every draft names every scheme, so each reply has to come through the guardrails. `LLMSimulatedUser` (prompt `simulated_user@v1`) plays the persona in natural Hindi, Marathi or English for live runs with `sahayak eval --suite dialogues`. It answers only what was asked, says it doesn't know what the profile leaves out, and never shares identifiers.
  - **Scores.** Per dialogue: the schemes shown at the end against the reference labels (exact set, and per scheme), against the rule engine on the persona's complete profile (did the conversation find everything the current rules can decide?), shown precision and eligible recall, questions, turns, the share of the opening's facts saved after turn 1, turn latency, and LLM calls, tokens and cost. A violation is any reply naming a scheme that is neither shown in that turn nor named by the user so far; the suite fails on any violation. Reports go to `evals/reports/<date>-dialogues.md` with transcripts, and runs to `eval_runs`.
  - **CI gate.** Every scripted dialogue must end with exactly the engine's schemes, ask at most 8 questions, read every opening fact and have no violations. Agreement with the reference labels is reported but not gated: it measures the draft rules, which M1-T14 fixes.
- **Alternatives considered:** separate dialogue personas (the checked labels would have to be produced again); LLM-only dialogues (not reproducible and not free, so unusable in CI); gating CI on reference accuracy (would fail on known draft mistakes that review will fix).
- **Consequences:** The scripted CI run on the drafts shows engine agreement 100%, 0 violations, reference accuracy 83% of dialogues exact and 97.7% per scheme (all misses are the D-028 draft mistakes), and 5.7 questions on average, often reaching the limit of 8 because the drafts ask about many fields across 10 schemes. Live runs cost real LLM calls (about 600 per full run) and depend on the local index and rules, like the retrieval eval (D-031).
- **Live run (2026-10-18, gpt-4o for the agent and the simulated user, the M1 index and draft rules, local Postgres, multilingual-e5-large):** 30 dialogues, 0 violations, 0 errors, 414 agent LLM calls, $1.46. Reference: 73.3% of dialogues exact, 96.6% per scheme. Engine agreement 83.3% (en 90%, hi 90%, mr 70%), shown precision 92.6%, recall 90.9%. 5.9 questions and 6.9 turns on average, 83.3% of opening facts read on turn 1, turn latency p50 1.96 s (max 5.7 s). Every engine disagreement traces to the simulated user or the question limit, not to the agent misreading what was said: in d021 and d022 the simulated user invented facts its persona doesn't have (an income, 6 acres, "no one pays income tax"), in d017 it rounded the boundary income ₹2,50,001 to "2.5 lakh", and in d003 the 8-question limit ran out before marital status was asked (a miss, the safe direction). The simulated-user prompt should list the unknown fields explicitly and keep exact amounts for boundary personas (a `simulated_user@v2`, not done here so that this run stays reproducible).

## D-041 — Speech providers: Sarvam Saaras and Bulbul, OpenAI as the fallback

- **Date:** 2026-10-18
- **Task:** M5-T02, M5-T03, M5-T04
- **Status:** accepted
- **Context:** TRD §7 asks for Sarvam AI by default (Saarika for STT, Bulbul for TTS) and one fallback each, with model names checked against the current docs and kept in config.
- **Decision:**
  - **STT: Sarvam `saaras:v4`** (`SARVAM_STT_MODEL`), `POST https://api.sarvam.ai/speech-to-text`, multipart `file` + `model` + `language_code` (`unknown` to detect, or `hi-IN` / `mr-IN` / `en-IN`), header `api-subscription-key`. The REST API is for clips under about 30 s; it takes WAV, MP3, AAC, OGG/Opus, FLAC, MP4/M4A, WebM and more, so browser recordings and WhatsApp voice notes need no conversion. The response has `transcript`, `language_code` and `language_probability`, and **no confidence for the words** (D-043). `saaras:v4` is the documented default; the older `saarika:v2.5` still answered but is not the recommended model. Checked live on 2026-10-18: 0.2 to 0.5 s per short utterance.
  - **TTS: Sarvam `bulbul:v3`, voice `shubh`** (`SARVAM_TTS_MODEL`, `SARVAM_TTS_VOICE`; the voice is the model's documented default), `POST https://api.sarvam.ai/text-to-speech`, JSON `text`, `language_code`, `model`, `speaker`, `speech_sample_rate` (24000), `output_audio_codec` (`wav`); the response is base64 audio in `audios[0]`. v3 takes 2500 characters (v2 1500), and v3 rejects Opus output, so OGG/Opus is made locally (D-042). `bulbul:v4-flash` exists with per-language persona voices; v3 is used because one voice speaks all three languages. Live: 0.7 to 1.3 s per sentence.
  - **Fallbacks: OpenAI**, reusing `OPENAI_API_KEY` (`STT_FALLBACK_PROVIDER=whisper`, `TTS_FALLBACK_PROVIDER=openai`): `gpt-4o-mini-transcribe` (token log-probabilities give a real confidence) and `gpt-4o-mini-tts` with voice `alloy`. `whisper-1` is supported but not the default: without a language hint it wrote Hindi in Urdu script. A fallback with a missing key is left out with a warning instead of disabling voice.
  - **Not implemented:** AI4Bharat IndicConformer and Indic Parler-TTS (self-hosted models that need a GPU); `STT_PROVIDER=indicconformer` and `TTS_PROVIDER=indic_parler` raise a clear "not implemented" config error.
  - **Retries:** speech is interactive, so each provider gets 2 tries with a 0.25 s backoff, then the fallback. 429 and 5xx are retried; 400/422 mean bad audio and are not.
  - **Limits and cost** (Sarvam docs, 2026-10-18): STT 60 requests/min on the Starter plan; ₹30 per hour of audio for STT; ₹30 per 10,000 characters for Bulbul v3.
- **Alternatives considered:** Sarvam's streaming STT WebSocket (D-044); OpenAI as the default (worse on Indian languages, see D-045); self-hosted AI4Bharat models (no GPU in the dev setup or the planned Container Apps).
- **Consequences:** Model names and voices are config (`.env.example`). The local `.env` holds the Sarvam key (M5-T01). Sarvam's account-wide rate limit will matter under load (M12-T10).

## D-042 — Audio: PyAV instead of an ffmpeg binary, sentence-level cache, no stored recordings

- **Date:** 2026-10-18
- **Task:** M5-T04, M5-T05, M5-T06
- **Status:** accepted
- **Context:** The web wants MP3/WAV and WhatsApp OGG/Opus (TRD §7), and TTS should be cached (NFR Cost). The dev machine has no ffmpeg (and Smart App Control blocks unsigned binaries); the image should stay small.
- **Decision:**
  - **PyAV** (`av`, FFmpeg's libraries in a wheel) does all decoding and encoding: mono, 24 kHz, MP3 at 48 kbps, Opus at 32 kbps in Ogg, or 16-bit WAV. No `ffmpeg` binary on the host or in the image. Conversions run in a worker thread.
  - **The unit of synthesis and caching is a sentence.** Replies are split at full stops, question marks and dandas (short pieces joined to the next). Each sentence is cached in Redis as MP3 under `tts:v1:<provider>:<model>:<voice>:<language>:<sha256(text)>` for `TTS_CACHE_TTL_DAYS` (7). Fixed wording (questions, notices, caveats) is therefore synthesised once for everyone, and MP3 sentences join by concatenation (no ID3 tag or Xing header in a part). A whole reply in another format is re-encoded from the cached MP3. Audio from a fallback voice is not cached, so the main voice returns with its provider. The cache fails open like the embedding cache (skip Redis for 30 s after an error).
  - **What is spoken:** the reply without citation markers, without the sources list, and with URLs replaced by "the official website" (in the reply's language); the text reply keeps them.
  - **Audio URL per reply:** `GET /v1/sessions/{id}/messages/{message_id}/audio?format=mp3|wav|ogg`, synthesised on first request from the stored reply. Text turns return it too (synthesised only if played); voice turns synthesise eagerly, so the URL is served from the cache. Served with `Cache-Control: private`.
  - **Recordings are never stored.** A voice message is saved as its masked transcript (with `asr_confidence`); `messages.audio_ref` stays empty. Deleting a session or withdrawing the profile consent also deletes the cached audio of its replies, which may repeat what the user said.
- **Alternatives considered:** shelling out to `ffmpeg` (a binary to install, blocked locally); caching whole replies (no reuse of repeated sentences, and one cache entry per format); returning audio inline as base64 in the JSON (bigger responses, no reuse by URL).
- **Consequences:** Joined MP3 parts carry about 50 ms of encoder padding each, heard as a slightly longer pause between sentences. Cache memory is about 6 KB per second of speech.

## D-043 — Language detection and ASR confidence (FR-2, FR-14)

- **Date:** 2026-10-18
- **Task:** M5-T03, M5-T07
- **Status:** accepted
- **Context:** FR-2 asks for automatic language detection, and FR-14 for confirming key values when ASR confidence is low. Sarvam was measured on 2026-10-18 with Bulbul-spoken phrases, clean and with added white noise (5 dB and -5 dB SNR):
  - auto-detect (`unknown`) is unreliable on short answers: "हाँ" came back as English "Yeah." (language probability 0.70), "पैंतालीस" as "45." (0.55), "नाही" as "No." (0.91). The agent would then switch to replying in English.
  - with a language hint, short answers were right ("हाँ।", "पैंतालीस।"), but the hint forces the language: Marathi speech with `hi-IN` came back **translated into Hindi**, English speech transliterated into Devanagari.
  - `language_probability` says nothing about the words: noisy "पैंतालीस" became "47." with 0.98, while clean "पैंतालीस" scored 0.55. Under heavy noise Sarvam produced fluent wrong text ("कहता है", "डूम.") with no signal.
  - OpenAI's token probabilities did track garbage (0.12 to 0.4 on noise) but were low even on clean short Hindi (0.45 to 0.64, with wrong transcripts).
- **Decision:**
  - **Sarvam detects first, then re-checks.** The first try uses `unknown`. If the detected language differs from the conversation's and the answer has at most 3 words or the language probability is below 0.9, the clip is transcribed again with the conversation's language as the hint. A clear sentence in another language is kept, so a user can switch languages by speaking. The first turn of a session has no hint.
  - **`Transcript.confidence` is the provider's confidence in the words, or None.** Sarvam's is None: the language probability is not used as a stand-in, because it would trigger confirmations on correct short answers and miss real mistakes. OpenAI's is the geometric mean of the token probabilities.
  - **FR-14 on voice:** when a voice turn's confidence is below `ASR_CONFIRM_THRESHOLD` (0.6), `extract_profile` saves nothing new from it. Every value it would add or change becomes an `unclear_audio` item, and `plan_question` reads back what was heard ("I may not have heard you clearly. I heard: "…". Is that right? If not, please say it again, or type it."), in fixed wording in all three languages. The next turn's understanding prompt (`turn_understanding@v2`) lists the values that were read back. Values the user confirms or corrects are saved; the rest are dropped and may be asked again later. The read-back counts as one question towards the limit of 8. A known value repeated is not read back. `FieldSource.confidence` records the turn's ASR confidence.
  - Nothing heard (empty transcript) or no transcript at all (every provider failed) gives a fixed spoken notice and no agent turn.
- **Alternatives considered:** always hinting after the first turn (locks a misdetected first turn into the wrong language for the whole session); never hinting (short answers flip the reply language); the language probability as confidence (shown to be misleading above); transcribing every turn with two providers and comparing them (twice the cost, and the slower provider's latency on every turn).
- **Consequences:** With Sarvam as the main provider, the read-back only happens when the fallback transcribed the turn, which is when errors are most likely. Mishearings by Sarvam itself are not caught by confidence. The contradiction check (a new value that disagrees with a known one is confirmed) still applies, and every saved value is shown in "what Sahayak knows about me" (M6). The 0.6 threshold is a starting point: on the clean FLEURS clips OpenAI's mean confidence was 0.94 (hi) and 0.89 (mr) (D-045).

## D-044 — Streaming voice: PCM over a WebSocket, partial transcripts by re-transcription, audio by sentence

- **Date:** 2026-10-18
- **Task:** M5-T08
- **Status:** accepted
- **Context:** TRD §11 asks for `WS /v1/sessions/{id}/stream` with audio chunks in and partial transcripts, reply text and audio chunks out, for the web app's live transcript (M6-T05) and the first-audio latency target (TRD §5).
- **Decision:**
  - **In:** the client sends raw 16-bit mono PCM (8 to 48 kHz; 16 kHz by default) as binary frames between optional `start` and `stop` messages. The browser can produce PCM with an AudioWorklet; PCM chunks, unlike MediaRecorder's WebM fragments, can be cut anywhere and wrapped as WAV for any provider.
  - **Partial transcripts:** every `STREAM_PARTIAL_INTERVAL_SEC` (2) of new audio, the recording so far is transcribed again through the normal STT provider (only one at a time; a partial still running at `stop` is cancelled first). This is provider-neutral and reuses the fallback chain. It costs extra STT on long utterances (about 2.5x the audio length for a 10 s one), which `0` turns off. The final transcript is one transcription of the whole utterance.
  - **Out:** `transcript`, then `reply` (the `/messages` body), then the reply's audio one sentence per frame as soon as each is ready (later sentences are synthesised meanwhile, 3 at a time), then `done` with the server's stage timings. Session errors close the connection with code 1008. One connection carries many utterances.
  - Each utterance opens its own database session; nothing holds a transaction or row lock between turns.
- **Alternatives considered:** Sarvam's streaming STT WebSocket (`wss://api.sarvam.ai/speech-to-text/ws`): it sends a transcript per VAD segment, which for a one-sentence answer arrives about when the user stops (little earlier than the final one), it is Sarvam-only (no fallback), and its end-of-stream needs a timeout heuristic; worth revisiting if users speak long turns. Binary audio frames out (harder for clients to keep in order with JSON events).
- **Consequences:** First audio after `stop` is STT + agent + the first sentence's TTS. The same protocol works for any future STT provider.

## D-045 — Speech evals: FLEURS clips for WER, and a voice round trip over HTTP

- **Date:** 2026-10-18
- **Task:** M5-T09, M5-T10
- **Status:** accepted
- **Context:** TRD §12.3 asks for WER per language on about 50 Hindi and Marathi clips from open datasets; §12.5 and the M5 exit criteria ask for p50/p95 per stage, and a voice round trip in hi, mr and en.
- **Decision:**
  - **ASR clips:** Google FLEURS (CC BY 4.0, Hugging Face `google/fleurs`), dev split, the first 25 distinct sentences in each of the `hi_in` and `mr_in` audio archives (one speaker per sentence). `evals/asr.jsonl` keeps the clip ids, file names, SHA-256 and reference transcripts (attribution: FLEURS, Conneau et al. 2022). The audio is not committed: `sahayak eval --suite asr` streams each archive once and stops as soon as its listed clips are found (about 40 MB kept in `data/raw/asr/`, gitignored), checking every checksum. Common Voice needs a login and AI4Bharat's sets are gated, so FLEURS was the simplest open source.
  - **Scoring:** NFC, lower case, punctuation (danda included) removed; corpus-level WER and CER per language. A lenient WER also folds the nukta and chandrabindu/anusvara spellings that FLEURS references and ASR output use interchangeably (ज़मीन/जमीन, गाँव/गांव). One provider at a time (`--stt`), no fallback, with the clip's language as the hint by default (as on every turn after the first).
  - **Latency:** every voice turn logs `voice_turn` (channel, outcome, STT provider, confidence, transcript length, `stt_ms`, `agent_ms`, `tts_ms`, `total_ms`; never words). `sahayak latency [LOG...]` prints p50/p95/max per channel and stage from JSON logs, alongside `agent_turn` for text turns.
  - **Round trip:** `sahayak eval --suite voice` serves the real app with uvicorn on a local port and, for each of the 30 dialogue openings (10 per language) spoken by the TTS voice, runs a fresh session's first turn through `POST /voice` and through the stream. First audio is measured by the client from the end of speech: for uploads until the reply's MP3 is downloaded, for the stream until the first audio frame. It fails on any failed turn or if first audio misses p50 3.5 s / p95 6 s (TRD §5). Synthetic speech is clean, so its transcript WER is only a sanity check.
- **Alternatives considered:** committing the clips (40 MB of binaries in git); a WER gate (the TRD sets no target); timing only server-side (misses upload and download).
- **Consequences:** Both suites call paid APIs and run on demand, not in CI.
- **Runs (2026-10-18):** ASR, Sarvam `saaras:v4` with hint: WER hi **6.4%** (lenient 4.7%), mr **13.0%** (13.0%), CER 1.9% / 3.8%, p50 0.8 s per clip, 0 failures. OpenAI `gpt-4o-mini-transcribe` with hint: hi 9.3% (7.5%), mr 25.1%, mean confidence 0.94 / 0.89, p50 1.5 s. Voice round trip, 30 openings x 2 channels, two runs (Sarvam STT/TTS, gpt-4o, local Postgres with the M1 index, multilingual-e5-large, a local fakeredis server for the caches): 60 of 60 turns answered in each run, every reply in the speaker's language, transcript WER on synthetic speech 8.9% / 7.7%. **Stream first audio p50 2.96 s / 3.16 s, p95 4.30 s / 5.59 s: within the TRD targets in both runs.** Upload (whole reply file downloaded) p50 3.30 s / 3.76 s, p95 7.07 s / 7.90 s: misses p95 (and p50 in the second run), because the client waits for every sentence. Server stages, second run, stream: STT p50 0.41 s, agent 1.90 s (p95 3.3 s, max 5.7 s), first sentence TTS 0.65 s. The agent (two gpt-4o calls per turn) is the largest and most variable stage; the TRD's mitigations (a faster model for understanding, streaming the LLM's reply into TTS) are the next levers. The first run was recorded with an older gate that also required the upload channel to meet the targets, so its `eval_runs` row says `passed: false`.
