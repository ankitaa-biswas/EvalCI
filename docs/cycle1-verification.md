# Cycle 1 verification record — 2026-10-29

Only Cycle 1 was implemented. Cycle 2 and Cycle 3 have not been started. The limited existing-workflow changes consume the Cycle 1 contract and remove its empty-report bypass; they do not establish CI/CD readiness.

## Changes

| Files | Change |
| --- | --- |
| `core/reports.py`, `core/fingerprinter.py`, `core/gate.py` | Canonical validated report, per-category signed comparisons, inclusive severity boundaries, direction handling, evidence/heuristic attribution and strict gate |
| `core/configuration.py`, `core/baselines.py` | Explicit endpoint/models/baseline, controlled bootstrap, dataset/configuration digests and compatibility checks |
| `core/evaluator.py`, `core/queue.py` | Required question accounting, strict scoring, terminal failure handling, persisted healthy reports and required absolute-quality checks |
| `db/models.py`, `db/crud.py`, `db/database.py` | Report/accounting/provenance storage, eagerly loaded result relationships, shared SQLAlchemy metadata and status-update correctness |
| `db/migrations/env.py`, `db/migrations/versions/c1_correct_reports.py` | Correct metadata/URL handling, fresh/legacy migration and timestamp compatibility |
| `api/models/run.py`, `api/models/score.py`, `api/schemas.py`, `api/routes/{evaluate,results,runs,fingerprints}.py` | Consistent request/report models, actionable configuration errors, strict results retrieval and lowercase persisted lifecycle status |
| `.github/workflows/evalci.yml` | Existing consumer calls the shared gate; missing fingerprint fails; poller matches lowercase status |
| `tests/test_eval.py`, `tests/test_cycle1.py`, `requirements-test.txt` | Synthetic and mocked-scoring coverage; real SQLite/PostgreSQL persistence, API and migration checks; removed process-global dependency stubs |
| `README.md`, this record | Current behavior, baseline procedure, runnable checks and limitations |

The 50-question/11-category dataset files are unchanged.

## Executed checks

| Command / environment | Observed result |
| --- | --- |
| `.venv/bin/python -m pytest -q` | 80 passed with SQLite persistence and fresh/legacy SQLite migrations |
| `EVALCI_TEST_DATABASE_URL='postgresql+asyncpg://evalci@/postgres?host=/private/tmp/evalci-cycle1-pg-a5ibixq7&port=55439' .venv/bin/python -m pytest -q --tb=short` | 82 passed; persistence/API/worker checks used real PostgreSQL 14.13 via asyncpg; added fresh/legacy PostgreSQL migration cases |
| `.venv/bin/python -m compileall -q api core db` | Passed |
| `.venv/bin/python -c 'import api.main, api.routes.runs, api.routes.fingerprints; print("API imports and route registration OK")'` | Passed |
| `.venv/bin/python -m pip check` | No broken requirements found in the existing virtual environment |
| `git diff --check` | Passed |

One dependency warning remains: Starlette imports the deprecated `multipart` module name. No test failures remain. This does not prove installation from a clean dependency environment; complete dependency controls remain Cycle 2 work.

The PostgreSQL cluster was initialized specifically for these checks under a unique temporary directory with TCP disabled. No existing PostgreSQL database, container or volume was modified. Sandbox restrictions initially blocked shared memory and Unix sockets; the relevant checks were rerun with approved access. PostgreSQL verification exposed an Alembic percent-escaped URL bug; it was fixed and the full suite then passed. The temporary PostgreSQL server was stopped after verification.

Coverage includes synthetic healthy/retrieval/generation/mixed/inconclusive/unchanged/improving patterns; both metric directions and severity boundaries; absent/self/incompatible/failed baselines; non-finite/missing/invalid metrics; HTTP and scorer failures; incomplete runs; malformed reports; absolute-quality failures; LOW/MEDIUM policy; CLI failure exits; persisted reports returned by the results API; HIGH regressions despite passing absolute quality; and settings changed between dispatch and worker execution.

## Capability evidence and remaining work

| Capability | Implemented in Cycle 1 | Verified here | Remaining/external |
| --- | --- | --- | --- |
| 50 questions / 11 categories | Existing dataset retained; membership and unique IDs validated | Dataset checks pass | Live evaluation against a served RAG target |
| Trustworthy gate decisions | Shared schema, accounting, explicit baseline/provenance, HIGH and absolute-quality rejection | Synthetic cases and real persistence/API/worker coroutine | Actual Actions execution, complete CI request configuration, checked-out RAG revision evaluation and branch protection are Cycle 3 |
| Regression investigation | Retrieval/generation evidence, mixed/inconclusive outcomes, actionable steps, no attribution for improvements | Synthetic rule behavior | Live attribution accuracy and probability calibration are unverified and unclaimed |
| Async backend | Existing FastAPI/Celery/Redis/PostgreSQL design retained; result persistence corrected | API routes + actual worker coroutine + SQLite/PostgreSQL, with mocked scoring and publication | Actual broker/worker process execution, full container stack and incremental SSE are Cycle 2 |
| Delivery / deployment | Not implemented in this cycle | Not executed | Cycle 3 plus explicit external authorization |

The current Actions trigger still lacks required endpoint, baseline and authentication configuration. The target helpers are not an HTTP RAG server. Compose still runs infrastructure while the API/worker run separately. Worker events remain terminal-only; dashboard SSE/authentication/replay work is deferred. No remote repository settings were changed; no release was published or deployed.

For manual Cycle 1 use, configure the API and worker with a database/broker, an OpenAI-compatible scoring key and explicit judge/embedding models, serve a compatible RAG endpoint, migrate the database, bootstrap/review a quality-passing baseline, and explicitly supply its UUID. See README for exact request fields and commands. A bootstrap's existence does not count as a successful regression comparison.
