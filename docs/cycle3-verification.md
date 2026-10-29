# Cycle 3 verification record — 2026-10-29

Cycle 3 implements the revision-bound CI/CD workflow. Cycles 1 and 2 remain in place; their records describe checks executed in those cycles. Current local checks use synthetic scoring with real Git revisions, containers, HTTP, Celery, PostgreSQL, Redis and Chroma. They do **not** establish live model quality, attribution accuracy, successful GitHub Actions execution, merge blocking, publication or deployment.

## Changes

| Files | Result |
| --- | --- |
| `.github/workflows/evalci.yml` | All-branch push/PR triggers; merge-revision PR checkout; obsolete-run cancellation; pinned actions and read-only defaults; stable aggregate `Quality Gate`; protected opt-in live evaluation and image delivery |
| `scripts/ci_context.py`, `scripts/ci_policy.py` | Explicit immutable baseline policy; aggregate gate rejects unsuccessful required jobs and enforces configured live policy |
| `scripts/ci.py`, `scripts/ci_evaluate.py` | Build and verify candidate/baseline RAG trees; isolated private Compose environment; explicit endpoints/auth; full API evaluation/report validation; timeouts, evidence capture, tested-image archives and project-owned cleanup |
| `scripts/image_identity.py`, `Dockerfile`, `api/main.py`, `rag_target/app.py` | Actual source hashes checked at build/runtime; RAG and harness revision identities; authenticated API identity and running image checks |
| `docker-compose.ci.yml`, `docker-compose.ci-fixture.yml`, `docker-compose.yml`, `core/queue.py` | Separate baseline target; no published CI ports; distinct collections; worker deadlines configurable to match CI's evaluation budget |
| `core/gate.py`, `core/configuration.py`, `tests/integration/fixture_scoring.py` | Cross-check question/category/raw-report consistency and all quality thresholds; revised fixture contract version; synthetic first-passage-copy scoring detects an actual changed target answer |
| `scripts/deliver.py` | Validate passing report/source/image evidence; preserve tested image bytes; reject fixture-only publication; default-branch/live-success publication policy |
| `scripts/verify_cycle3.py`, `tests/test_cycle3.py` | Disposable local Git revisions and full container proof; synthetic/mocked gate, malformed evidence, timeout, policy and source-integrity tests |
| `.gitignore`, `README.md`, `docs/external-setup.md`, `docs/resume-items.tex`, this record | Ignore temporary build sources; runnable commands; exact pending external setup; evidence-supported resume wording |

Baselines use the selected historical **RAG source** with the **current harness and dependencies**, making evaluation configuration comparable; both identities are recorded. The build verifies the copied target digest, and the runner recomputes source hashes inside the actual running target containers and checks their image IDs. A supplied SHA alone is not accepted as proof. Configuration/fixture contract versions changed, so previously stored baselines need deliberate rebootstrap.

The report gate validates all expected scored rows, category membership, counts, finite/ranged metrics, derived risk, aggregates, absolute-quality flags, report provenance and comparison identity. HIGH losses fail. LOW/MEDIUM losses are recorded and allowed only if required absolute quality passes. Bootstrap quality is validated but bootstrap never passes a regression comparison. Missing/non-200/malformed reports, incomplete runs, request/scoring errors and timeouts cannot authorize delivery.

## Executed checks

All commands ran from `evalci/` using the local Python 3.11 virtual environment. Docker execution was Linux ARM64 on Docker Desktop; hosted Linux AMD64 remains unexecuted.

| Command/check | Observed result |
| --- | --- |
| `.venv/bin/python -m pytest -q --tb=short` | **126 passed**; one existing Starlette multipart deprecation warning. Includes synthetic regression rules, strict result/report checks, mocked HTTP errors/timeouts, source mutation detection, baseline policy and delivery gating. |
| `node --test tests/test_stream_client.cjs` | **3 passed** for parsing, authenticated reconnect/cursor and authentication-failure behavior. |
| `/private/tmp/evalci-ci-tools/actionlint .github/workflows/evalci.yml` (actionlint 1.7.7) | Passed workflow syntax/expression validation. Action revision pins were also checked against upstream Git tags. |
| `.venv/bin/python -m compileall -q api core db rag_target scripts tests`; `git diff --check` | Passed. |
| Dataset count and Git diff | Still **50 questions / 11 categories**; neither dataset file changed. |
| `.venv/bin/python -m scripts.verify_cycle3 --output artifacts/cycle3-final` | Full local healthy/degraded/error scenarios described below; source and image evidence saved. |
| Read-only GitHub REST branch/rules audit | `main` returned `protected: false`; active rules returned `[]`. No settings changed or merge attempt made. |

The earlier successful full container run is also retained at `artifacts/cycle3/`. The final run includes configurable worker deadlines, fail-closed cleanup and a Git bundle of the synthetic verification history. Repeating the verifier requires a fresh output directory. It does not push or publish.

### Local gate and delivery proof

1. **Healthy:** a clean temporary Git revision is built as the candidate; the separately built baseline completes a quality-passing bootstrap. Candidate evaluation scores all 50 questions, covers 11 categories and passes the comparison gate.
2. **Degraded:** a new committed RAG source revision changes the fixture answer from `contexts[0]` to `contexts[-1]`. The actual target source digest changes. The synthetic scorer records correctness 0.84 versus baseline 0.99: an inclusive 0.15 HIGH loss in all 11 categories, with stable retrieval/grounding signals. Absolute quality still passes, proving the regression gate itself rejects the candidate. The helper exits 1 with **`11 HIGH-severity metric regressions`** and creates no deliverable archive. This is evidence for the explicit synthetic contract, not semantic accuracy or causal attribution accuracy.
3. **Evaluation error:** the healthy source revision is evaluated through an unavailable candidate endpoint. All 50 requests fail, zero score, the run is terminally failed and the fingerprint endpoint returns no successful report. The runner exits 1 and creates no deliverable archive.
4. **Delivery:** the real delivery helper rejects the degraded bundle. The healthy bundle is checksum-verified, loaded and retagged locally without a rebuild; the resulting image ID exactly matches the tested candidate. Its evidence explicitly says `published: false` and `live_quality_verified: false`. Unit policy checks separately reject PR, non-default branch, disabled publication and unsuccessful live evaluation cases.

Evidence under ignored `artifacts/cycle3-final/` includes `verification.json`, `verification-revisions.bundle`, scenario logs and each scenario's `manifest.json`, submissions, raw API results, canonical fingerprint reports, `gate.json`, harness identity and Compose logs. The healthy bundle also contains `tested-images.tar.gz`; failed scenarios do not. The bundle contains only the disposable snapshot's synthetic Git history, not the user's original repository history. Verification removes its own containers, networks, volumes, private env and temporary Git checkout; images and captured evidence remain for inspection.

### Recorded synthetic revisions

| Role | Full local Git SHA | Gate |
| --- | --- | --- |
| Baseline | `cc02e7175d78e6bde62713a7b710fea444116413` | Quality-passing bootstrap |
| healthy | `7ef7bc9ccad0dd456d3199fcda41098a208477f9` | PASS |
| degraded | `fb1b9a7e74e984aa89915207cfac0d2798144884` | FAIL (expected) |
| evaluation-error | `7ef7bc9ccad0dd456d3199fcda41098a208477f9` | FAIL (expected) |

Healthy tested/delivery image ID: `sha256:c40c57e93d9d8f62254f978c85760a83988bfd5f80a9eaea97700a0809ff25af`. These commits belong to the retained local verification Git bundle, not to a claimed GitHub Actions run. Full target source digests and per-scenario image IDs are in `artifacts/cycle3-final/verification.json` and manifests.

## Final claim-to-evidence table

| Requested capability | Implemented | Verified evidence | External/unverified limit |
| --- | --- | --- | --- |
| 50-question/11-category push/PR evaluation | All-branch Actions workflow, explicit endpoints/auth, isolated full stack and immutable revision/source binding | Dataset unchanged; local real Git/container/API runs cover all 50 questions and 11 categories | Workflow not pushed/run on GitHub; hosted AMD64 not executed |
| Required HIGH-blocking quality check | Stable `Quality Gate`; HIGH/errors/timeouts/incomplete/malformed evidence/failed absolute quality fail; LOW/MEDIUM policy explicit | Synthetic/mocked failure tests; actual changed revision rejected for 11 HIGH losses with absolute quality still passing | `main` currently unprotected with no active rules; required check and failing-PR merge demonstration pending |
| Regression Fingerprint engine | Canonical persisted per-category signed deltas, baseline provenance, direction-aware severities and evidence/actionable explanations | Cycle 1 synthetic healthy/retrieval/generation/mixed/unchanged tests and API/persistence tests; Cycle 3 actual target mutation | Heuristic attribution weights are not calibrated probabilities; no live attribution accuracy or causal certainty claimed |
| Async FastAPI/Celery/Redis/PostgreSQL backend | Complete Compose stack, migrations, readiness, persistent results/events and strict completion accounting | Cycle 2 actual worker/broker/database tests; Cycle 3 fresh isolated stacks and unavailable-target failure | Live judge/Gemini credentials/models not configured for execution |
| Incremental SSE and dashboard | Durable request/scoring events, API-key header streaming, cursors, reconnect and late terminal recovery | Cycle 2 real Chrome display before completion, direct PostgreSQL persistence, reconnect/restart replay; current 3 JS tests | No token streaming; events depend on retained database history; killed runs can remain unfinished and cannot pass |
| Tested revision delivered only after checks | Protected opt-in default-branch delivery, passing live evidence required, archive checksum and exact image IDs, SHA-versioned GHCR tag | Actual local load/retag preserves tested image ID; actual failed bundle rejected; synthetic publication policy tests | GHCR publication and protected environments not exercised; continuous delivery prepared, continuous deployment not implemented or claimed |
| Supports investigation and release decisions | Complete accounting, explainable evidence, reproducible workflow configuration and artifacts | Local results and failure decisions above, plus Cycles 1/2 records | No production usage, operational reliability benchmark or live model benchmark claimed |

## Remaining work

Follow [the exact external setup](external-setup.md) for authorized push, Actions runs, `Quality Gate` branch rules, protected evaluation/provider configuration, protected delivery and GHCR permissions. Configure both live-enable and live-required variables plus an approved PR execution path before describing merge protection as live-quality validation. Fork PRs never receive provider secrets; live-required policy fails closed when their live job is skipped.

No staging target is configured or authorized. Image publication is continuous delivery, not deployment. Remote Actions success, a blocked failing PR, registry delivery, staging health/release identity and operational production evidence remain unverified. No remote changes were made in this cycle.

The three final evidence-supported LaTeX bullets are in [resume-items.tex](resume-items.tex).
