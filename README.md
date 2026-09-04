# EvalCI — Evaluation & Regression Testing Harness for RAG Systems

> **Automatically test your RAG pipeline on every Git push. Score it. Fingerprint what broke. Ship with confidence.**

[![CI](https://github.com/ankitaa-biswas/evalci/actions/workflows/evalci.yml/badge.svg)](https://github.com/ankitaa-biswas/evalci/actions)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.111-green)](https://fastapi.tiangolo.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## What is EvalCI?

EvalCI is a **continuous integration harness for Retrieval-Augmented Generation (RAG) systems**. Every time code is pushed to GitHub, EvalCI automatically:

1. **Queries your RAG system** with a curated set of 50 test questions across 10 categories.
2. **Scores each answer** using the [Ragas](https://docs.ragas.io) library on three axes — correctness, grounding (faithfulness), and hallucination risk.
3. **Detects regressions** by comparing scores against a baseline run.
4. **Generates a Regression Fingerprint Report** — EvalCI's core novel output — that identifies *which* question categories degraded and attributes the failure to the *retriever* or the *generator*.
5. **Fails the CI pipeline** if a HIGH-severity regression is detected, preventing degraded RAG code from reaching production.

---

## The Problem EvalCI Solves

RAG systems have two distinct components that can independently break:

| Component | Failure Mode | Traditional CI Detection |
|-----------|-------------|--------------------------|
| **Retriever** | Wrong chunks fetched, embedding drift, index staleness |  Not detected |
| **Generator** | Hallucination, prompt template regression, model version change |  Not detected |

Standard test suites check *that* quality dropped. They cannot tell you *why* or *which component* to fix. A developer staring at a dashboard showing "correctness dropped from 0.82 to 0.71" has no idea whether to look at ChromaDB, the prompt template, or the LLM model version.

**EvalCI solves this with the Regression Fingerprint.**

---

##  The Regression Fingerprint — EvalCI's Core Innovation

The **Regression Fingerprint Report** is a structured diagnostic document generated whenever an evaluation run scores lower than the baseline. It answers two questions no existing RAG eval tool automates:

### Question 1: Which categories regressed?

EvalCI groups the 50 test questions into 10 semantic categories (factual recall, multi-hop reasoning, definition lookup, causal reasoning, etc.). When scores drop, the fingerprint identifies *exactly* which categories were affected — giving developers a surgical view rather than a blunt overall score.

### Question 2: Was it the Retriever or the Generator?

This is the novel attribution step. The Fingerprinter examines the **divergence pattern between `grounding` (faithfulness) and `correctness`** metrics:

```
┌──────────────────────────┬─────────────────┬──────────────────────────────┐
│ Grounding Delta          │ Correctness Δ   │ Attribution                  │
├──────────────────────────┼─────────────────┼──────────────────────────────┤
│ Dropped ≥ 0.05           │ Also dropped    │ RETRIEVER — bad chunks       │
│                          │                 │ propagate to bad answers     │
├──────────────────────────┼─────────────────┼──────────────────────────────┤
│ Stable (< 0.05 drop)     │ Dropped         │ GENERATOR — model ignoring   │
│                          │                 │ good context                 │
├──────────────────────────┼─────────────────┼──────────────────────────────┤
│ Dropped ≥ 0.05           │ Stable/High     │ BOTH — fragile lucky correct │
│                          │                 │ answers on bad retrieval     │
├──────────────────────────┼─────────────────┼──────────────────────────────┤
│ Stable                   │ Stable          │ No regression                │
└──────────────────────────┴─────────────────┴──────────────────────────────┘
```

`context_recall` and `context_precision` serve as confirming signals:
- If `context_recall` also drops → **confirms retriever** (failing to fetch relevant chunks)
- If `context_precision` drops but `recall` is stable → **noisy retrieval** (fetching irrelevant chunks)

The result is a concrete, actionable report like:

> *"EvalCI detected HIGH-severity regressions in `factual_recall` and `definition_lookup` categories. Attribution: RETRIEVER. Grounding dropped 0.13 and context_recall dropped 0.11, while correctness followed at -0.14. Recommended actions: (1) URGENT: Block this PR. (2) Verify ChromaDB collection was re-indexed with the latest documents. (3) Check embedding model version hasn't changed."*

### Why this is novel

No existing production RAG evaluation tool (including bare Ragas, TruLens, RAGAS-CI integrations, or DeepEval) performs **automated per-category, component-attributed regression fingerprinting**. They report aggregate scores; EvalCI tells you what to fix.

---

## Tech Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| **API** | FastAPI + Uvicorn | REST endpoints + SSE streaming |
| **Background Jobs** | Celery + Redis | Async evaluation runs |
| **Database** | PostgreSQL + SQLAlchemy 2.0 (async) | Result persistence |
| **Cache** | Redis | Score caching by commit SHA |
| **Scoring** | Ragas | correctness, faithfulness, context_recall, context_precision |
| **Vector DB** | ChromaDB | RAG document retrieval target |
| **LLM** | Google Gemini 1.5 Flash | RAG generation + Ragas judge |
| **Streaming** | Server-Sent Events (SSE) | Live dashboard updates |
| **CI** | GitHub Actions | Automated trigger + quality gate |
| **Frontend** | Plain HTML + JavaScript | Real-time evaluation dashboard |
| **Dev Infra** | Docker Compose | Local PostgreSQL + Redis + ChromaDB |

---

## Project Structure

```
evalci/
├── api/                          # FastAPI application layer
│   ├── main.py                   # App entry point, middleware, route registration
│   ├── routes/
│   │   ├── evaluate.py           # POST /evaluate — trigger a run
│   │   ├── results.py            # GET /results — fetch run history + fingerprints
│   │   └── stream.py             # GET /stream/{run_id} — SSE live updates
│   └── models/
│       ├── run.py                # Pydantic models: EvalRunRequest, EvalRunResponse
│       └── score.py              # Pydantic models: QuestionScore, FingerprintReport
│
├── core/                         # Business logic
│   ├── evaluator.py              # Ragas scoring orchestration
│   ├── fingerprinter.py          # ★ NOVEL: Regression Fingerprint algorithm
│   ├── cache.py                  # Redis score caching + SSE pub/sub
│   └── queue.py                  # Celery task definitions
│
├── db/                           # Database layer
│   ├── database.py               # SQLAlchemy async engine + session factory
│   ├── models.py                 # ORM tables: EvalRun, QuestionScore, CategoryScore, FingerprintReport
│   └── crud.py                   # All DB read/write operations
│
├── rag_target/                   # The RAG system being evaluated
│   ├── ingest.py                 # Load documents into ChromaDB
│   ├── query.py                  # Query ChromaDB + generate answer via LLM
│   └── sample_docs/              # Knowledge base documents for testing
│
├── tests/
│   ├── test_suite.json           # 50 questions + ground-truth answers (10 categories)
│   └── categories.json           # Category definitions + per-category thresholds
│
├── dashboard/
│   ├── index.html                # Main evaluation dashboard UI
│   └── stream.js                 # SSE client (SSEClient class + event handlers)
│
├── .github/
│   └── workflows/
│       └── evalci.yml            # CI: trigger → poll → fetch results → quality gate
│
├── docker-compose.yml            # PostgreSQL + Redis + ChromaDB + Flower
├── requirements.txt              # All Python dependencies
├── .env.example                  # Environment variable template
└── README.md                     # This file
```

---

## Quick Start

### 1. Prerequisites

- Python 3.11+
- Docker + Docker Compose
- OpenAI API key

### 2. Clone and install

```bash
git clone https://github.com/ankitaa-biswas/evalci.git
cd evalci
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Configure environment

```bash
cp .env.example .env
# Edit .env — set GOOGLE_API_KEY, EVALCI_API_KEYS, DATABASE_URL, REDIS_URL at minimum
```

### 4. Start infrastructure

```bash
docker compose up -d
```

### 5. Ingest sample documents

```bash
python -m rag_target.ingest
```

### 6. Start the API server

```bash
uvicorn api.main:app --reload --port 8080
```

### 7. Start the Celery worker

```bash
celery -A core.queue.celery_app worker --loglevel=info --concurrency=2
```

### 8. Trigger an evaluation

```bash
curl -X POST http://localhost:8080/evaluate \
  -H "Content-Type: application/json" \
  -d '{
    "commit_sha": "'"$(git rev-parse HEAD)"'",
    "branch": "main",
    "triggered_by": "manual"
  }'
```

### 9. View the dashboard

Open `http://localhost:8080/dashboard` in your browser.

---

## API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/evaluate` | Trigger a new evaluation run |
| `GET` | `/evaluate/{run_id}/status` | Poll run status (PENDING/RUNNING/COMPLETE/FAILED) |
| `GET` | `/results` | List all historical runs (paginated) |
| `GET` | `/results/{run_id}` | Full result payload for a run |
| `GET` | `/results/{run_id}/fingerprint` | Regression Fingerprint Report |
| `GET` | `/stream/{run_id}` | SSE stream of live score events |
| `GET` | `/health` | Liveness probe |

---

## GitHub Actions Integration

Add these secrets to your repository (`Settings → Secrets and variables → Actions`):

| Secret | Value |
|--------|-------|
| `EVALCI_API_URL` | Your deployed EvalCI API URL |
| `EVALCI_API_KEYS` | Bearer token for API authentication |
| `GOOGLE_API_KEY` | Gemini API key for scoring and diagnosis |

The workflow (`.github/workflows/evalci.yml`) runs on every push to `main` and every pull request. It:
1. Triggers an evaluation via `POST /evaluate`
2. Polls for completion (up to 20 minutes)
3. Downloads the Regression Fingerprint Report
4. Posts a summary comment to the PR
5. **Fails the job** if any category has a HIGH-severity regression

---

## Evaluation Metrics

EvalCI uses four Ragas metrics:

| Metric | What it measures | Primary use |
|--------|------------------|------------|
| `answer_correctness` | Semantic similarity to ground truth | Overall quality |
| `faithfulness` (grounding) | Is the answer supported by retrieved context? | Retriever/Generator split |
| `context_recall` | Did retrieval cover the ground-truth information? | Retriever diagnosis |
| `context_precision` | Are retrieved chunks relevant (low noise)? | Retriever diagnosis |

`hallucination_risk = 1 - faithfulness` is derived and reported alongside.

---



## License

MIT — see [LICENSE](LICENSE).

---

*EvalCI was designed to give RAG developers the same regression visibility that unit tests give application developers — but for the non-deterministic, multi-component world of retrieval-augmented AI.*
