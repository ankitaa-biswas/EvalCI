# EvalCI

EvalCI evaluates RAG answers and supports regression investigation and release decisions. The fixed evaluation dataset contains **50 questions across 11 categories** in `tests/test_suite.json` and `tests/categories.json`.

The current work implements **Cycle 1 only: evaluation and regression decision correctness**. Containerizing the complete stack, incremental SSE and the complete CI/CD workflow remain pending. See [the verification record](docs/cycle1-verification.md) for implemented, verified and externally configured functionality.

## Evaluation contract

`core/reports.py::FingerprintReport` is the versioned contract used by fingerprint computation, persistence, the results API and `python -m core.gate`. A successful comparison always saves a report, including when no regression occurs. The optional legacy route modules re-use this contract; the application registers `/evaluate`, `/results` and `/stream`.

Every category has signed `current - baseline` comparisons for correctness, groundedness, context recall, context precision and derived hallucination risk (`1 - groundedness`). Higher is better for the first four; lower is better for hallucination risk. Hallucination risk and groundedness are dependent signals, so their regression entries must not be interpreted as independent evidence.

| Direction-adjusted absolute loss | Severity | Regression gate |
| --- | --- | --- |
| Less than 0.02, unchanged or improving | NONE | Allowed |
| 0.02 up to but excluding 0.05 | LOW | Recorded, allowed |
| 0.05 up to but excluding 0.15 | MEDIUM | Recorded, allowed |
| 0.15 or greater | HIGH | Failed |

Boundaries are inclusive at 0.02, 0.05 and 0.15. These are absolute score differences, not relative percentages. Signed deltas are rounded to 12 decimal places before classification to avoid floating-point boundary artifacts. The old `regression_thresholds` metadata in `categories.json` is retained for dataset integrity; `core/reports.py` and the persisted configuration define this policy. In particular, LOW now explicitly starts at 0.02.

The gate also requires a completed comparison and all absolute quality checks: per-category thresholds and global thresholds from `categories.json`, plus `PASS_THRESHOLD` for overall correctness (default 0.70). Threshold equality passes. Missing, malformed, non-finite, boolean, nonnumeric or out-of-range metrics cannot become valid scores. Missing/malformed reports, missing baselines, scoring/request failures and incomplete evaluations fail closed.

Runs retain expected, attempted, successfully scored and failed counts, plus question-level stage/error records. Completed reports require all expected questions to have been attempted and successfully scored, with zero failures. A failed evaluation saves its accounting and errors and is not eligible to supply a comparison report. Successfully scored rows from an incomplete batch are counted but are not persisted as a completed result set. Counts are saved after the scoring batch, not streamed incrementally; a killed worker can leave an unfinished run requiring investigation.

## Attribution rules

The report provides category-specific metric evidence and investigation steps. Retrieval loss is the larger recall/precision loss. Generation loss is the larger groundedness/correctness loss.

- Retrieval loss of at least 0.02 supports retriever investigation.
- Generation loss of at least 0.02 with stable retrieval supports generator investigation.
- When retrieval also drops, generation loss exceeding retrieval loss by at least 0.05 supports mixed investigation. Retrieval loss can propagate into answer metrics, so this rule does not establish separate causes.
- A regression without either signal is inconclusive. Conflicting category signals can also produce an inconclusive overall attribution.
- Unchanged, improving and below-threshold comparisons have no failure attribution and zero attribution weights.

Signal strengths are normalized into **heuristic weights**, not calibrated probabilities. No prompt-versus-knowledge-base causal distinction or live attribution accuracy is claimed. The tests use synthetic metric patterns and mocked scoring.

## Baselines and provenance

There is no automatic latest-run fallback. Every request must contain exactly one of `baseline_run_id` or `baseline_bootstrap: true`. Selection is checked both before dispatch and in the worker. Self-comparison, missing/failed/incomplete baselines and incompatible datasets/configurations are rejected. Baselines require complete persisted question/category coverage and a validated quality-passing report. Previously compared baselines must also have passed their regression gate.

Provenance includes a SHA-256 digest of canonical question JSON, the exact evaluation configuration and its digest, judge and embedding model names, temperature, Ragas and relevant scoring dependency versions, provider base-URL digest, metric set, category counts, quality/severity thresholds, request timeout, target endpoint and supplied commit SHA. API/worker settings and dataset must match between dispatch and execution. Endpoint and commit are recorded separately from compatibility settings so changed RAG targets/revisions can be compared. Commit metadata alone does not prove which code a remote target serves; that is deferred to Cycle 3.

For the first baseline:

1. Submit `baseline_bootstrap: true` with a working HTTP RAG endpoint.
2. Require a complete run and inspect the results, question outputs, configuration and `quality_passed` in its report.
3. Explicitly select that reviewed run ID in future requests. Bootstrap is never selected automatically and **never passes the regression gate** (`passed=false`, `comparison_status=bootstrap`). A bootstrap that fails absolute quality is ineligible as a baseline.
4. Bootstrap again deliberately when the dataset or evaluation configuration changes. Legacy rows without canonical provenance/reports cannot serve as baselines.

## Local setup for Cycle 1

From the repository directory, install `requirements.txt` and `requirements-test.txt` in a Python 3.11+ virtual environment. Configure the following in both API and worker processes; environment files are not automatically loaded by these commands:

| Setting | Purpose |
| --- | --- |
| `DATABASE_URL` | SQLAlchemy async PostgreSQL URL, `postgresql+asyncpg://...` |
| `REDIS_URL` | Celery broker and terminal-event pub/sub |
| `OPENAI_API_KEY` | Credentials for OpenAI-compatible Ragas judge/embeddings |
| `EVAL_JUDGE_MODEL` | Explicit supported judge model name; no implicit default |
| `EVAL_EMBEDDING_MODEL` | Explicit supported embedding model name; no implicit default |
| `OPENAI_API_BASE` or `OPENAI_BASE_URL` | Optional compatible provider URL; `OPENAI_API_BASE` takes precedence |
| `EVALCI_API_KEYS` | Comma-separated API keys; requests use `X-API-Key` |
| `PASS_THRESHOLD` | Optional overall correctness threshold, default 0.70 |

Judge/embedding configuration uses the existing Ragas 0.1.x interface and OpenAI-compatible LangChain clients. Other providers require a separate adapter. No live model request was made during Cycle 1 verification.

Start existing PostgreSQL and Redis infrastructure (the existing Compose file still provides infrastructure only), then migrate and run separate processes:

```bash
alembic upgrade head
uvicorn api.main:app --port 8080
# In a second configured terminal:
celery -A core.queue.celery_app worker --loglevel=info --concurrency=2
```

The Cycle 1 migration handles fresh databases and existing evaluation tables while preserving legacy data. Old timestamps without timezone are interpreted as UTC. Back up a database before upgrading. Its automatic downgrade is deliberately disabled to avoid discarding evaluation data. Startup creates missing tables for development but cannot add columns to existing tables; run the migration before upgrading the application.

Supply a separately running HTTP RAG service with `POST {"query": "..."}` returning `{"answer": "...", "retrieved_chunks": ["..."]}` (`contexts` is also accepted). The existing `rag_target` helpers do not expose a query HTTP server yet.

Example first-baseline request (replace the endpoint and header with your own configuration):

```bash
curl --fail-with-body http://localhost:8080/evaluate/ \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: YOUR_API_KEY' \
  -d '{"commit_sha":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","branch":"main","rag_endpoint":"http://localhost:8001/query","baseline_bootstrap":true}'
```

For a comparison, replace `baseline_bootstrap` with `baseline_run_id` containing the reviewed run UUID. Poll `/evaluate/{run_id}/status`; statuses are `pending`, `running`, `complete`, `failed`. Fetch `/results/{run_id}` for scores, completion, provenance and errors. Fetch `/results/{run_id}/fingerprint` for the required canonical report. An unavailable report returns an error, never an empty success response.

```bash
python -m core.gate fingerprint_report.json --run-id RUN_UUID --commit-sha TESTED_COMMIT_SHA
```

The existing workflow now consumes this same gate and rejects unavailable fingerprint responses. Its endpoint/baseline/authentication request configuration, revision isolation, full push triggers, delivery and external branch protection remain **pending Cycle 3**. It is not yet a proven CI/CD pipeline.

## Checks

```bash
python -m pytest -q
python -m compileall -q api core db
git diff --check
```

By default the persistence/API tests use real SQLite. To use an **isolated test PostgreSQL database** with schema-creation privileges:

```bash
EVALCI_TEST_DATABASE_URL='postgresql+asyncpg://TEST_USER:TEST_PASSWORD@localhost/TEST_DATABASE' \
  python -m pytest -q
```

The PostgreSQL mode creates and drops only uniquely named test schemas. It also checks fresh and legacy migrations. HTTP responses, model scoring and terminal event publication are mocked; the worker coroutine, SQLAlchemy persistence, report validation and API handlers execute together. Redis, a Celery worker process, the complete HTTP server/middleware lifecycle, live LLM attribution accuracy, GitHub Actions and deployment are not verified by these tests.
