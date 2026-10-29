# Cycle 2 verification record — 2026-10-29

Cycle 2 implements the complete local container stack and durable incremental progress. Cycle 3 has not been started. The GitHub workflow and 50-question/11-category dataset are unchanged in this cycle.

## Changed files

| Files | Result |
| --- | --- |
| `Dockerfile`, `.dockerignore`, `docker-compose.yml`, `docker-compose.fixture.yml`, `docker-compose.dev.yml` | Non-root application image; API, worker, migration job and HTTP RAG target alongside PostgreSQL, Redis and Chroma; internal networking, persistent project volumes, dependency/readiness checks; optional host development ports and Flower profile |
| `.env.example`, `.gitignore`, `scripts/create_local_env.py` | Placeholder-only example, private env exclusion and non-overwriting credential generation |
| `requirements.in`, `requirements.lock`, `requirements.txt`, `requirements-test.txt` | Pinned/hash-verified Python runtime, immutable primary image references, additional test tools and build-time import/dataset smoke checks |
| `core/evaluator.py`, `core/configuration.py`, `core/queue.py`, `core/cache.py`, `core/progress.py`, `core/health.py` | Real per-request/scoring outcome callbacks, configurable batches, mode provenance, typed progress, durable commits, terminal events, worker checks and task-owned Redis lifecycle |
| `db/models.py`, `db/database.py`, `db/migrations/versions/c2_progress_events.py`, `alembic.ini` | Ordered event history, migration/import path, connection validation and worker-compatible connection ownership |
| `api/main.py`, `api/routes/{evaluate,stream}.py`, `api/middleware/{auth,rate_limit}.py` | Readiness, queued events/latest progress, cursor replay and terminal recovery; authenticated data/streams with a public static dashboard |
| `dashboard/{index.html,app.js,stream.js}` | Existing registered results routes, API-key input, authenticated fetch streaming, stage counts, reconnect cursors, selected-run state reset and explicit fixture labels |
| `rag_target/{app,service,ingest,query}.py` | HTTP target; corpus/model-versioned Chroma indexes; explicit live/fixture modes; preserved standalone ingestion behavior and corrected Gemini class import |
| `tests/test_cycle1.py`, `tests/test_cycle2.py`, `tests/test_stream_client.cjs`, test fixture modules | Batch-compatible prior failure test; durable storage/replay/auth/disconnect checks; deterministic scoring fixture clearly separated from live quality validation |
| `scripts/verify_cycle2.py`, `scripts/verify_target_outage.py`, `scripts/verify_dashboard.cjs`, `README.md`, this record | Runnable stack, outage and real-browser verification; clean-start and live configuration instructions |

## Executed checks

Commands below used project `evalci-cycle2-823fa9`, a private generated env file at `/private/tmp/evalci-cycle2-823fa9/compose.env`, and both Compose files (`docker-compose.yml`, `docker-compose.fixture.yml`). Host API/RAG ports were 18088/18089. Only newly created test resources were reset; no unrelated containers or data were removed.

| Check / command | Observed result |
| --- | --- |
| `python -m pytest -q --tb=short` | **86 passed**; real SQLite event persistence and API route execution, mocked HTTP/model/publication dependencies in unit tests |
| `node --test tests/test_stream_client.cjs` | **3 passed**: partial/CRLF/multiline SSE parsing; header authentication and reconnect cursor; authentication failure stops retries |
| `python -m compileall -q api core db rag_target scripts tests` and `git diff --check` | Passed |
| `docker compose … build api` | Passed with Python 3.11.14/Linux ARM64, locked artifact hashes, `uv pip check`, dataset/Ragas smoke check and existing provider-helper imports |
| `docker compose … up -d --wait --wait-timeout 180 --no-build` | All six long-running services healthy; migration job exited 0 |
| `docker compose … exec -T api python -m scripts.verify_cycle2 --api-url http://api:8080 --commit-sha <local HEAD> --output /tmp/cycle2-evidence` | Full 50-question bootstrap and explicit-baseline comparison completed; comparison gate passed; required completion fields were 50/50/50/0; 11 categories and 55 category metric comparisons persisted |
| Same verifier, live stream/reconnect checks | **106 durable events** per healthy run; request and scoring progress observed while run status was still running; reconnect resumes after consumed cursor; late subscriber receives terminal completion; unauthorized data access rejected |
| Same verifier, unavailable internal endpoint | All 50 attempts failed, zero scored; run failed, error event emitted and fingerprint endpoint returned 409 |
| `node scripts/verify_dashboard.cjs http://127.0.0.1:18088 <private env file>` | Actual headless Chrome run-row click displayed request/scoring progress before completion, fixture label and terminal results; **0 uncaught JavaScript exceptions**; screenshots and observations saved |
| `docker compose … stop rag-target`, then `python -m scripts.verify_target_outage …` | Actual stopped-service failure: expected 50, attempted 50, scored 0, failed 50; terminal error and no passing comparison report; service subsequently restored |
| `docker compose … exec -T postgres psql …` | Direct database queries confirm question/category/report/event rows for named verified runs; failed runs have no question/category/comparison report rows |
| Recreate application services and fetch prior comparison/results/stream | Prior 50 scored rows and passing comparison remained available; late terminal replay still worked |

A second project, `evalci-cycle2-clean-823fa9`, used **the final image, fresh private credentials and entirely fresh volumes**, with host ports 18090/18091, to repeat the documented startup and full fixture verifier. Its evidence is saved under `artifacts/cycle2/clean-start/`.

The suite emits one existing Starlette `multipart` deprecation warning. Provider import smoke checks can also emit a Google Python-support future warning. No live provider request is part of these checks.

## Evidence and interpretation

Local generated evidence is under `artifacts/cycle2/` (ignored by Git):

- `summary.json`, `bootstrap-*`, `comparison-*`: API results, reports, event histories and observed running-stage checks.
- `postgres-evidence.csv`: direct PostgreSQL queries. Verified healthy runs have 50 question rows, 11 category rows, one report and 106 events; failed target cases have zero score/report rows and 55 events.
- `dashboard-progress.png`, `dashboard-complete.png`, `dashboard-verification.json`: real Chrome screenshots and observed stage/count changes.
- `stopped-target-*`, `unavailable-target-*`: actual stopped-service and unreachable-endpoint evidence.
- `restart-replay.json`: results and terminal replay surviving application-container recreation.
- `runtime-image-id.txt`, `source-digests.json`: locally tested image and runtime source identities.
- `clean-start/`: repeated final-image verification from fresh Compose resources.

The fixture target makes real HTTP requests and Chroma retrieval calls with local hash embeddings. The fixture scorer supplies fixed values to test plumbing. Its numeric scores **are not evidence of RAG answer quality, model performance or attribution accuracy**. Fixture mode is included in progress/report provenance and shown by the browser. Existing providers remain importable, but live model calls are unverified.

Earlier verification failures exposed and led to fixes for Alembic/Celery import paths, a PyArrow/NumPy runtime incompatibility not caught by dependency metadata, closed pooled connections after an SSE disconnect, stale dashboard data during run selection and the existing Gemini class-name import. The recorded successful runs occurred after the applicable fixes; the final-image clean-start repeats the complete transport verifier.

## Capability status and limitations

| Capability | Implemented | Verified | Remaining / external |
| --- | --- | --- | --- |
| Complete asynchronous backend | FastAPI, Celery, Redis, PostgreSQL, RAG target and Chroma in Compose with migrations, readiness and persistent storage | Actual API and prefork worker processes, full dataset, real PostgreSQL/Redis/Chroma; deterministic scores | Live judge/embedding credentials and supported model settings |
| Incremental progress | Request and scoring callbacks; durable event contract/history; authenticated browser consumption | Actual SSE before completion, cursor reconnect, late replay, Chrome display, terminal failure and restart recovery | No token-level model progress; batches report rows after a batch returns |
| Trustworthy regression decisions | Cycle 1 contract/accounting/baseline/gate retained; scoring mode/batch settings added to compatibility provenance | Complete fixture comparisons pass; request/service failures cannot produce passing reports | Live quality and attribution accuracy remain unverified; intentionally bootstrap a compatible baseline after configuration upgrade |
| Push/PR quality enforcement and delivery | Existing workflow retained from Cycle 1 | No Actions run, branch protection or delivery/deployment verification in Cycle 2 | **Cycle 3 pending** |

Replay is available for all retained PostgreSQL event rows; no automatic pruning/retention job is implemented. Manual deletion or a volume reset removes history. A terminal snapshot covers a process stopping after its terminal status commit but before the event append. A worker killed before committing terminal state can leave a running run; automatic stalled-run reconciliation is not implemented, and the run cannot supply a passing report.

Only Linux ARM64 container execution was performed. Linux AMD64, live provider calls, the optional Flower profile and host-process development mode were not executed. Main Compose/fixture configuration was parsed and the documented final-image clean start was exercised. The runtime lock and image digests control dependencies; Bookworm build-tool repository updates mean this is not a promise of bit-for-bit identical image bytes.

For live operation, configure `OPENAI_API_KEY`, `EVAL_JUDGE_MODEL`, `EVAL_EMBEDDING_MODEL`, `GOOGLE_API_KEY`, `GEMINI_GENERATION_MODEL` and `GEMINI_EMBEDDING_MODEL` in a private env file, use the base Compose file without the fixture override, migrate, then bootstrap/review an explicit compatible baseline. `.env.example` contains placeholders only. The available local settings did not include a complete configured judge/embedding credential set, so live scoring was deliberately left unexecuted.

Verification services were stopped after checks. The main project's volumes were retained for inspection; the second disposable clean-start project's volumes were removed after copying its evidence. Private verification env files remain outside the repository. No credentials, generated reports or screenshots were committed. No remote repository, protection setting, release or deployment was changed.
