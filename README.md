# EvalCI

EvalCI evaluates RAG answers and supports regression investigation and release decisions. The fixed evaluation dataset contains **50 questions across 11 categories** in `tests/test_suite.json` and `tests/categories.json`.

The current work implements **Cycles 1–3**: evaluation decision correctness, the complete container stack, durable incremental progress, and revision-bound CI/CD. The workflow is locally verified; GitHub Actions execution, required merge checks, live model evaluation and registry publication remain external setup.

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

Runs retain expected, attempted, successfully scored and failed counts, plus question-level stage/error records. Completed reports require all expected questions to have been attempted and successfully scored, with zero failures. A failed evaluation saves its accounting and errors and is not eligible to supply a comparison report. Successfully scored rows from an incomplete batch are counted but are not persisted as a completed result set. The final completion summary is saved after scoring. Request and scoring outcomes are also logged incrementally as durable progress events. A killed worker can leave an unfinished run requiring investigation; the event history records its last observed progress.

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

Provenance includes a SHA-256 digest of canonical question JSON, the exact evaluation configuration and its digest, scoring mode and batch size, judge and embedding model names, temperature, Ragas and relevant scoring dependency versions, provider base-URL digest, metric set, category counts, quality/severity thresholds, request timeout, target endpoint and supplied commit SHA. API/worker settings and dataset must match between dispatch and execution. Endpoint and commit are recorded separately from compatibility settings so changed RAG targets/revisions can be compared. Commit metadata alone does not prove which code an arbitrary remote target serves. The Cycle 3 CI runner instead builds selected Git RAG trees and verifies source digests and running image identities.

For the first baseline:

1. Submit `baseline_bootstrap: true` with a working HTTP RAG endpoint.
2. Require a complete run and inspect the results, question outputs, configuration and `quality_passed` in its report.
3. Explicitly select that reviewed run ID in future requests. Bootstrap is never selected automatically and **never passes the regression gate** (`passed=false`, `comparison_status=bootstrap`). A bootstrap that fails absolute quality is ineligible as a baseline.
4. Bootstrap again deliberately when the dataset or evaluation configuration changes. Legacy rows without canonical provenance/reports cannot serve as baselines.

## Clean start through Docker Compose

Docker with Compose is sufficient to run the services. All commands below run from the repository root. Use a project name of your own so volumes and containers stay isolated.

Create a private environment file with random local database/API credentials (requires only Python's standard library), or copy `.env.example` to `.env.local` and replace its placeholders:

```bash
python3 scripts/create_local_env.py .env.local
```

The helper refuses to overwrite existing files. `.env.local` and other private `.env.*` files are ignored by Git and excluded from image builds. Keep database passwords URL-safe (the helper uses hex).

For **deterministic integration mode**, which makes no model calls:

```bash
docker compose --project-name evalci-demo --env-file .env.local \
  -f docker-compose.yml -f docker-compose.fixture.yml build api
docker compose --project-name evalci-demo --env-file .env.local \
  -f docker-compose.yml -f docker-compose.fixture.yml up -d --wait --wait-timeout 180 --no-build
```

The first build installs the full pinned runtime. The default services are PostgreSQL, Redis, Chroma, the migration job, HTTP RAG target, Celery worker and FastAPI. Migration completion and dependency health control startup order. Application processes run as an unprivileged user. PostgreSQL, Redis and Chroma use project-scoped named volumes; infrastructure has no host-published ports. API and target bind to localhost on 8080 and 8001 by default (`API_PORT` / `RAG_PORT` override them).

Open `http://localhost:8080/dashboard/` and enter the API key from your private `.env.local`. The page is public; all evaluation data and streams require the API key. The key remains in that tab's memory and is sent in `X-API-Key`, including streaming requests. It is not stored in a URL, cookie or browser storage.

The fixture target uses deterministic hash embeddings with real Chroma retrieval and returns a retrieved passage. The synthetic fixture scorer checks an explicit first-passage-copy contract to test transport, accounting, persistence and regression decisions. It is not a semantic-quality judge. **Fixture scores are not live quality validation or attribution-accuracy evidence.** Mode is recorded in report provenance and displayed by the dashboard; fixture/live baselines are incompatible.

The verifier is included in the image so host Python dependencies are unnecessary:

```bash
docker compose --project-name evalci-demo --env-file .env.local \
  -f docker-compose.yml -f docker-compose.fixture.yml exec -T api \
  python -m scripts.verify_cycle2 --api-url http://api:8080 \
  --commit-sha "$(git rev-parse HEAD)" --output /tmp/cycle2-evidence
mkdir -p artifacts
docker compose --project-name evalci-demo --env-file .env.local \
  -f docker-compose.yml -f docker-compose.fixture.yml cp api:/tmp/cycle2-evidence artifacts/cycle2
```

It submits all 50 questions through the actual API and Celery worker, bootstraps a fixture baseline, compares a second run, checks authenticated SSE delivery during both stages, reconnect/late replay, persisted API results and an unavailable-endpoint failure. That Cycle 2 verifier supplies a metadata SHA; use the Cycle 3 verifier below to exercise actual distinct Git revisions.

For a manual request, export your private env values in your terminal and use:

```bash
curl --fail-with-body http://localhost:8080/evaluate/ \
  -H 'Content-Type: application/json' \
  -H "X-API-Key: $EVALCI_API_KEYS" \
  -d '{"commit_sha":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","branch":"main","rag_endpoint":"http://rag-target:8001/query","baseline_bootstrap":true}'
```

The endpoint in this request is resolved by the container worker, so use the Compose service hostname. Use an explicit reviewed `baseline_run_id` for a comparison, as described above.

Shutdown preserves your project's data:

```bash
docker compose --project-name evalci-demo --env-file .env.local \
  -f docker-compose.yml -f docker-compose.fixture.yml down
```

Restart with the same project/env/Compose files to reuse the indexes and evaluations. Removing volumes is an explicit destructive reset of that project's data; never run a global container/volume prune as part of verification. Optional Flower monitoring is available with `--profile monitoring`. To run API/worker directly on the host, add `docker-compose.dev.yml` and start only `postgres redis chromadb`; that override publishes localhost infrastructure ports. Set host `DATABASE_URL`/`REDIS_URL` explicitly and use `DB_POOL_MODE=null` for a host Celery worker, matching the container worker’s per-task event-loop lifecycle.

## Live model configuration

For a live stack, configure `.env.local` from `.env.example` and use **only** `docker-compose.yml`, omitting the fixture override. Required settings are:

| Setting | Purpose |
| --- | --- |
| `POSTGRES_PASSWORD`, `EVALCI_API_KEYS` | Private local database/API credentials |
| `OPENAI_API_KEY` | OpenAI-compatible Ragas judge/embedding credentials |
| `EVAL_JUDGE_MODEL`, `EVAL_EMBEDDING_MODEL` | Available model names, explicitly selected |
| `OPENAI_BASE_URL` | Compatible provider base URL, default official OpenAI URL |
| `GOOGLE_API_KEY` | Existing Gemini target provider credentials |
| `GEMINI_GENERATION_MODEL`, `GEMINI_EMBEDDING_MODEL` | Available target models, explicitly selected |

Target initialization embeds and indexes sample documents before readiness succeeds. Corpus/chunking/embedding changes select a new versioned collection; stable restarts reuse the index. Old collections are retained. The existing standalone `rag_target.query` / ingestion helpers remain available.

`EVAL_SCORING_MODE=ragas` and `RAG_MODE=live` are the default modes. `EVAL_SCORING_BATCH_SIZE` (default 5), `RAG_REQUEST_TIMEOUT_SECONDS` (default 30) and `PASS_THRESHOLD` (default 0.70) are recorded in provenance. The scoring configuration changed since Cycle 1; deliberately bootstrap/review a compatible baseline after upgrading.

Live Ragas/Gemini scoring and attribution accuracy are unverified in Cycle 2 because a complete configured judge/embedding credential set was unavailable. Provider model availability and quotas require external setup. No live evaluation benchmark is claimed.

## Progress and replay contract

`GET /stream/{run_id}` emits `progress`, `done` and `error` events. Each contains run ID, stage, completed/total counts, lifecycle status, attempted/scored/failed counts, optional question ID/error and timestamp. `completed/total` are **stage counts**: they reset when requests finish and scoring starts. Scoring occurs in configurable batches; an event is recorded for each scored/failed row after its batch returns. Scoring stage completion includes questions already failed during requests; separate scored/failed counters distinguish those outcomes. There is no claim of token-level model progress. `ping` heartbeats are emitted during quiet periods.

PostgreSQL `run_events` is the replay source. Events are committed before publication to Redis, and SSE tails the durable log every 250 ms. Redis pub/sub delivery is not required for replay. Event IDs are global database integers. Reconnect with `Last-Event-ID` to receive later retained events. Without a cursor, a late subscriber receives the full run history through its terminal event. A terminal database snapshot is returned even if the worker stopped between its status commit and event append, or the reconnect cursor already includes the terminal event.

Disconnects release the stream and its short-lived database sessions. Heartbeats and the browser's watchdog/reconnect loop handle idle periods and network interruption. Authentication failures stop retries and ask for a valid key. No automatic event pruning is implemented: replay is available while rows remain in the database. Manual database deletion/reset removes history. A process killed before committing terminal run state can leave a running run; this is distinguishable from a completed evaluation and cannot pass the gate.

`GET /evaluate/{run_id}/status` returns the latest durable progress and final completion accounting. `/results/{run_id}` and `/results/{run_id}/fingerprint` retain the Cycle 1 contract. `/health` is liveness; `/ready` checks the migrated database, Redis and a reachable worker. It does not certify remote model availability or arbitrary submitted RAG endpoints.

## Dependencies, checks and evidence

`requirements.in` pins runtime inputs. `requirements.lock` pins transitive Python versions and artifact hashes for Python 3.11/Linux; the container installer enforces hashes. Python, installer, PostgreSQL, Redis and Chroma image references include immutable digests. Debian build-tool packages come from the configured Bookworm repositories, so these controls do not promise bit-for-bit identical image bytes. Only Linux ARM64 image execution was verified here; Linux AMD64 was not executed.

Refresh the lock deliberately with uv 0.13.6:

```bash
uv pip compile requirements.in --python-version 3.11 \
  --python-platform x86_64-unknown-linux-gnu --generate-hashes -o requirements.lock
```

For host development, install the runtime first and additional test tools separately:

```bash
python -m pip install --require-hashes -r requirements.txt
python -m pip install -r requirements-test.txt
python -m pytest -q
node --test tests/test_stream_client.cjs
python -m compileall -q api core db rag_target scripts tests
git diff --check
```

With an isolated PostgreSQL test database, `EVALCI_TEST_DATABASE_URL=... python -m pytest -q` also runs PostgreSQL persistence and migration checks. For real-browser verification against the fixture stack, `node scripts/verify_dashboard.cjs API_URL PRIVATE_ENV_FILE [CHROME_EXECUTABLE]` launches a separate headless Chrome profile and saves screenshots/observations under `artifacts/cycle2`. The default executable is the standard macOS Chrome path. This uses the DevTools protocol and needs no browser automation package.

The verification records distinguish local execution from external configuration. Cycle 2's real Chrome and PostgreSQL checks remain documented in its record.

## Revision-bound CI/CD

The workflow runs on every branch push and PR. Its stable **Quality Gate** aggregates required jobs and fails closed on failed/cancelled checks. PRs evaluate GitHub's checked-out merge revision. The runner builds candidate and baseline RAG source with the same current harness and locked dependencies, checks source digests inside running containers, and uses private authentication plus isolated Compose networks/volumes with no published ports. Evidence includes raw results, fingerprints, completion counts, configuration, baseline/candidate SHAs, source hashes and image IDs. Artifacts retain successfully tested images for delivery without rebuilding.

The default gate uses synthetic fixture scoring. Live evaluation is separately opt-in and can be required; delivery additionally requires successful live evaluation of exactly the same image bytes. Delivery prepares a versioned GHCR image; it does not deploy an application. Merge protection and protected environments require repository administrator configuration. Neither remote execution nor publication is claimed from YAML.

With Docker, Git and host Python runtime dependencies installed, run the complete local proof from an uncommitted working tree:

```bash
python -m scripts.verify_cycle3 --output artifacts/cycle3-local
```

Use a fresh output directory. The verifier snapshots only Git-tracked/unignored source into its own temporary Git repository, creates healthy/degraded revisions, builds real images, and verifies healthy success, HIGH regression failure, unavailable-target failure, blocked failed delivery and a successful local image handoff. It preserves that synthetic Git history as `verification-revisions.bundle` for inspection and removes only its own temporary repository and Compose resources. This requires substantial image/archive disk space and makes no provider calls or remote changes.

For a single clean checkout with both full SHAs already fetched:

```bash
python -m scripts.ci --candidate-sha <checked-out-full-sha> \
  --baseline-sha <reviewed-full-sha> --mode fixture \
  --output artifacts/ci-local --archive-images
```

`--allow-dirty` is diagnostics only and cannot produce a release image bundle.
