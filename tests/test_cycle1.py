"""Synthetic contract and mocked-scoring tests; real SQLite ORM and API routes.

No live RAG/model, Celery broker, Redis or PostgreSQL accuracy claim is made.
"""
from copy import deepcopy
import json
import os
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI
import httpx
import pandas as pd
import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.models.run import EvalRunRequest
from api.routes import evaluate, results
from core.baselines import resolve_baseline
from core.configuration import load_evaluation
from core.evaluator import RAGEvaluator, aggregate_by_category, category_snapshots, score_to_dict
from core.fingerprinter import RegressionFingerprinter
from core.gate import check_report
from core.queue import _async_run_evaluation
from core.reports import Completion, FingerprintReport, METRICS, Provenance, digest, metric_value, severity_for
from db import database
from db.crud import create_run, get_run_by_id, save_fingerprint
from db.models import Base, FingerprintReport as StoredReport, RunStatus

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "tests/test_suite.json"


def snapshot(value=.9):
    return {"question_count": 1, **{m: (1-value if m == "hallucination_risk" else value) for m in METRICS}}


def provenance():
    config = {"category_counts": {"one": 1, "two": 1}, "category_thresholds": {c: {"correctness": .1} for c in ("one", "two")},
              "global_thresholds": {"correctness": .1}, "overall_correctness_threshold": .1}
    return Provenance(dataset_sha256=digest(["q1", "q2"]), configuration_sha256=digest(config),
                      configuration=config, commit_sha="a"*40, rag_endpoint="http://rag/query")


def make_report(current=None, **overrides):
    p = provenance()
    args = dict(run_id=uuid4(), baseline_run_id=uuid4(), completion=Completion(expected=2, attempted=2, successfully_scored=2, failed=0),
                provenance=p, baseline_provenance=p, current=current or {"one": snapshot(), "two": snapshot()},
                baseline={"one": snapshot(), "two": snapshot()}, quality_passed=True)
    args.update(overrides)
    return RegressionFingerprinter().report(**args)


@pytest.mark.parametrize("pattern,component", [
    ({}, None),
    ({"context_recall": .65, "context_precision": .65, "correctness": .7}, "retriever"),
    ({"correctness": .65, "groundedness": .65, "hallucination_risk": .35}, "generator"),
    ({"context_recall": .8, "correctness": .6, "groundedness": .6, "hallucination_risk": .4}, "mixed"),
    ({"hallucination_risk": .3}, "inconclusive"),
])
def test_synthetic_attribution(pattern, component):
    current = {"one": snapshot(), "two": snapshot()}
    current["one"].update(pattern)
    report = make_report(current)
    assert report.top_failing_component == component
    if component:
        assert all(r.evidence and r.suggested_fix for r in report.regressions)
    else:
        assert not report.regressions
        assert all(sum(c.attribution_weights.values()) == 0 for c in report.comparisons)
    assert "not calibrated probabilities" in report.attribution_method


@pytest.mark.parametrize("loss,severity", [(0,"NONE"),(.019999,"NONE"),(.02,"LOW"),(.049999,"LOW"),(.05,"MEDIUM"),(.149999,"MEDIUM"),(.15,"HIGH"),(.150001,"HIGH")])
@pytest.mark.parametrize("metric,direction", [("correctness",-1),("hallucination_risk",1)])
def test_inclusive_boundaries(loss, severity, metric, direction):
    assert severity_for(metric, direction*loss) == severity
    assert severity_for(metric, -direction*loss) == "NONE"


def test_improvements_and_per_category_regression():
    healthy = make_report({"one": snapshot(1), "two": snapshot(1)})
    assert check_report(healthy.model_dump(mode="json")).top_failing_component is None
    # Improving one category cannot conceal a high regression in another.
    report = make_report({"one": snapshot(.7), "two": snapshot(1)})
    with pytest.raises(ValueError, match="HIGH"):
        check_report(report.model_dump(mode="json"))
    assert {r.category for r in report.regressions} == {"one"}


@pytest.mark.parametrize("value", [None, "bad", "0.8", True, float("nan"), float("inf"), -.1, 1.1])
def test_invalid_metrics_fail(value):
    with pytest.raises(ValueError):
        metric_value(value, "correctness")
    b = {m: .8 for m in METRICS[:4]}
    with pytest.raises(ValueError):
        RegressionFingerprinter().compute(b, {**b, "correctness": value})
    with pytest.raises(ValueError):
        score_to_dict({"id":"q", "category":"c", "question":"?", "ground_truth":"a"},
                      {"answer":"a", "retrieved_chunks":[]},
                      {"answer_correctness":value, "faithfulness":.9,"context_recall":.9,"context_precision":.9})


@pytest.mark.parametrize("payload", [{}, {"overall_regressed":False}, [], None])
def test_malformed_gate_reports_fail(payload):
    with pytest.raises(ValueError):
        check_report(payload)


def test_report_tampering_fails():
    report = make_report().model_dump(mode="json")
    for change in (lambda r:r["comparisons"].pop(),
                   lambda r:r["comparisons"][0].update(delta=-.5),
                   lambda r:r["comparisons"][0].update(current_score="0.9"),
                   lambda r:r.update(overall_regressed=True),
                   lambda r:r.update(quality_passed=False),
                   lambda r:r.update(baseline_run_id=r["run_id"]),
                   lambda r:r["completion"].update(successfully_scored=1, failed=1)):
        bad = deepcopy(report)
        change(bad)
        with pytest.raises(ValueError):
            check_report(bad)
    with pytest.raises(ValueError, match="another run"):
        check_report(report, run_id=uuid4())
    with pytest.raises(ValueError, match="another revision"):
        check_report(report, commit_sha="b"*40)


def test_bootstrap_and_missing_baseline_never_pass():
    with pytest.raises(ValueError):
        make_report(baseline_run_id=None)
    report = make_report(baseline_run_id=None, baseline_provenance=None)
    assert not report.gate_passed
    with pytest.raises(ValueError, match="Bootstrap"):
        check_report(report.model_dump(mode="json"))
    with pytest.raises(ValueError):
        EvalRunRequest(commit_sha="a"*40, branch="main", rag_endpoint="http://rag/query")
    with pytest.raises(ValueError):
        EvalRunRequest(commit_sha="a"*40, branch="main", baseline_bootstrap=True)


def test_quality_threshold_failure_and_low_medium_policy():
    report = make_report({"one":snapshot(.01), "two":snapshot(.01)}, quality_passed=False)
    with pytest.raises(ValueError, match="absolute quality"):
        check_report(report.model_dump(mode="json"))
    for value in (.88,.85):
        report = make_report({"one":snapshot(value),"two":snapshot()})
        assert check_report(report.model_dump(mode="json")).gate_passed


@pytest.fixture
def scoring_env(monkeypatch):
    monkeypatch.setenv("EVAL_JUDGE_MODEL", "synthetic-judge")
    monkeypatch.setenv("EVAL_EMBEDDING_MODEL", "synthetic-embedding")
    monkeypatch.setenv("PASS_THRESHOLD", ".70")


def test_dataset_intact_and_config_errors(scoring_env, monkeypatch):
    questions, p = load_evaluation(DATASET, "a"*40, "http://rag/query")
    assert len(questions) == 50
    assert len(p.configuration["category_counts"]) == 11
    assert len({q["id"] for q in questions}) == 50
    with pytest.raises(ValueError, match="rag_endpoint"):
        load_evaluation(DATASET, "a"*40, None)
    monkeypatch.delenv("EVAL_JUDGE_MODEL")
    with pytest.raises(ValueError, match="EVAL_JUDGE_MODEL"):
        load_evaluation(DATASET, "a"*40, "http://rag/query")


@pytest.mark.parametrize("failure", ["http", "malformed_rag", "scorer", "nan", "missing_metric", "short_frame", "all_http"])
def test_mocked_request_and_scoring_failures(monkeypatch, failure):
    questions = [{"id":f"q{i}", "category":"c", "question":f"{i}", "ground_truth":"expected"} for i in range(2)]
    original = httpx.Client
    def handler(request):
        q = json.loads(request.content)["query"]
        if failure == "all_http" or failure == "http" and q == "0":
            return httpx.Response(503)
        if failure == "malformed_rag" and q == "0":
            return httpx.Response(200, json={"answer":None})
        return httpx.Response(200, json={"answer":"answer", "contexts":["evidence"]})
    monkeypatch.setattr("core.evaluator.httpx.Client", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    evaluator = RAGEvaluator()
    monkeypatch.setattr(evaluator, "_build_ragas_dataset", lambda q,r: q)
    def score(dataset):
        if failure == "scorer":
            raise RuntimeError("Synthetic scorer failure")
        rows = [{"answer_correctness":.9,"faithfulness":.9,"context_recall":.9,"context_precision":.9} for _ in dataset]
        if failure == "nan": rows[0]["faithfulness"] = float("nan")
        if failure == "missing_metric": rows[0].pop("faithfulness")
        if failure == "short_frame": rows.pop()
        return pd.DataFrame(rows)
    monkeypatch.setattr(evaluator, "_run_ragas", score)
    batch = evaluator.evaluate(questions,"http://rag/query")
    assert not batch.completion.complete
    assert batch.completion.expected == batch.completion.attempted == 2
    assert batch.completion.failed > 0
    assert len(batch.errors) == batch.completion.failed


@pytest_asyncio.fixture
async def storage(tmp_path):
    url = os.getenv("EVALCI_TEST_DATABASE_URL")
    schema = "cycle1_" + uuid4().hex if url else None
    engine = create_async_engine(url or f"sqlite+aiosqlite:///{tmp_path / 'cycle1.db'}",
        **({"connect_args": {"server_settings": {"search_path": schema}}} if schema else {}))
    try:
        async with engine.begin() as conn:
            if schema:
                await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
            await conn.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        yield sessions
    finally:
        if schema:
            async with engine.begin() as conn:
                await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await engine.dispose()



@pytest_asyncio.fixture
async def client(storage):
    app = FastAPI()
    app.include_router(evaluate.router, prefix="/evaluate")
    app.include_router(results.router, prefix="/results")
    async def get_db():
        async with storage() as db:
            yield db
    app.dependency_overrides[database.get_db] = get_db
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client


@pytest_asyncio.fixture
async def pipeline(storage, client, scoring_env, monkeypatch):
    from unittest.mock import AsyncMock
    import core.cache
    monkeypatch.setattr(database, "AsyncSessionLocal", storage)
    monkeypatch.setattr(core.cache, "publish_score_event", AsyncMock())
    tasks = {}
    def dispatch(**kwargs):
        tasks[kwargs["run_id"]] = kwargs
        return "synthetic-task-id"
    monkeypatch.setattr(evaluate, "dispatch_eval_task", dispatch)
    monkeypatch.setattr(RAGEvaluator, "_call_rag_endpoint", staticmethod(lambda c,e,q: {"answer":"synthetic answer", "retrieved_chunks":["synthetic context"]}))
    monkeypatch.setattr(RAGEvaluator, "_build_ragas_dataset", staticmethod(lambda q,r:q))
    monkeypatch.setattr(RAGEvaluator, "_run_ragas", lambda self,d: pd.DataFrame([
        {"answer_correctness":.99,"faithfulness":.99,"context_recall":.99,"context_precision":.99} for _ in d]))
    async def submit(baseline_id=None):
        payload = {"commit_sha":"a"*40,"branch":"test","rag_endpoint":"http://rag/query"}
        payload.update({"baseline_run_id":baseline_id} if baseline_id else {"baseline_bootstrap":True})
        response = await client.post("/evaluate/",json=payload)
        assert response.status_code == 202, response.text
        run_id = response.json()["run_id"]
        await _async_run_evaluation(None, **tasks[run_id])
        return run_id
    return submit, tasks


@pytest.mark.asyncio
async def test_real_persistence_api_and_mocked_worker_roundtrip(pipeline, client, storage):
    submit, _ = pipeline
    baseline = await submit()
    bootstrap = (await client.get(f"/results/{baseline}/fingerprint")).json()
    assert bootstrap["comparison_status"] == "bootstrap"
    assert not (await client.get(f"/results/{baseline}")).json()["passed"]
    current = await submit(baseline)
    response = await client.get(f"/results/{current}/fingerprint")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert len(payload["comparisons"]) == 55
    assert check_report(payload, run_id=current).gate_passed
    detail = (await client.get(f"/results/{current}")).json()
    assert detail["status"] == "complete" and detail["passed"]
    assert len(detail["per_question_scores"]) == 50 and len(detail["category_scores"]) == 11
    assert detail["completion"] == dict(expected=50,attempted=50,successfully_scored=50,failed=0)
    assert detail["baseline_run_id"] == baseline
    assert (await client.get(f"/evaluate/{current}/status")).json()["status"] == "complete"
    assert len((await client.get("/results/")).json()) == 2
    async with storage() as db:
        stored = (await db.execute(select(StoredReport).where(StoredReport.run_id == __import__('uuid').UUID(current)))).scalar_one()
        assert stored.report == payload
        run = await get_run_by_id(db,current)
        with pytest.raises(ValueError, match="own baseline"):
            await resolve_baseline(db,current,current,Provenance.model_validate(run.provenance))
        changed = Provenance.model_validate(run.provenance).model_copy(update={"dataset_sha256":digest("different")})
        with pytest.raises(ValueError, match="incompatible"):
            await resolve_baseline(db,baseline,current,changed)
        run.status = RunStatus.FAILED
        await db.commit()
        with pytest.raises(ValueError, match="complete"):
            await resolve_baseline(db,current,None,Provenance.model_validate(run.provenance))


@pytest.mark.asyncio
async def test_worker_scoring_failure_is_failed_not_partial_pass(pipeline, client, monkeypatch):
    submit, tasks = pipeline
    baseline = await submit()
    monkeypatch.setattr(RAGEvaluator,"_run_ragas",lambda self,d: pd.DataFrame([
        {"answer_correctness":.99,"faithfulness":float("nan") if i == 0 else .99,
         "context_recall":.99,"context_precision":.99} for i,_ in enumerate(d)]))
    with pytest.raises(ValueError, match="Incomplete"):
        await submit(baseline)
    failed = list(tasks)[-1]
    detail = (await client.get(f"/results/{failed}")).json()
    assert detail["status"] == "failed" and not detail["passed"]
    assert detail["completion"]["successfully_scored"] == 49
    assert detail["completion"]["failed"] == 1
    assert detail["evaluation_errors"][0]["stage"] == "scoring"
    assert (await client.get(f"/results/{failed}/fingerprint")).status_code == 409


@pytest.mark.asyncio
async def test_invalid_baseline_rejected_before_dispatch(client, scoring_env):
    response = await client.post("/evaluate/",json={"commit_sha":"a"*40,"branch":"test",
        "rag_endpoint":"http://rag/query", "baseline_run_id":str(uuid4())})
    assert response.status_code == 422 and "Baseline run does not exist" in response.text


@pytest.mark.asyncio
async def test_malformed_persisted_reports_fail_api(pipeline, client, storage):
    submit, _ = pipeline
    baseline = await submit()
    current = await submit(baseline)
    async with storage() as db:
        row = (await db.execute(select(StoredReport).where(StoredReport.run_id == __import__('uuid').UUID(current)))).scalar_one()
        row.report = {"overall_regressed":False}
        await db.commit()
    assert (await client.get(f"/results/{current}/fingerprint")).status_code == 500


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("backend", ["sqlite"] + (["postgresql"] if os.getenv("EVALCI_TEST_DATABASE_URL") else []))
def test_alembic_fresh_and_legacy_database(tmp_path, monkeypatch, legacy, backend):
    from alembic import command
    from alembic.config import Config
    import sqlalchemy as sa
    path = tmp_path / "migration.sqlite"
    schema = "cycle1_migration_" + uuid4().hex if backend == "postgresql" else None
    url = sa.engine.make_url(os.getenv("EVALCI_TEST_DATABASE_URL")).set(drivername="postgresql+psycopg2") if schema else sa.engine.make_url(f"sqlite:///{path}")
    if schema:
        with sa.create_engine(url).begin() as conn:
            conn.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
        url = url.update_query_dict({"options": f"-csearch_path={schema}"})
    engine = sa.create_engine(url)
    marker = uuid4()
    if legacy:
        metadata = sa.MetaData()
        for table in Base.metadata.sorted_tables:
            table.to_metadata(metadata)
        runs = metadata.tables["eval_runs"]
        for name in ("provenance", "completion", "evaluation_errors", "baseline_bootstrap", "baseline_run_id"):
            runs._columns.remove(runs.c[name])
        reports = metadata.tables["fingerprint_reports"]
        reports._columns.remove(reports.c.report)
        reports.c.baseline_run_id.nullable = False
        metadata.create_all(engine)
        with engine.begin() as conn:
            conn.execute(runs.insert().values(id=marker, commit_sha="a"*40,branch="legacy",status="complete"))
    monkeypatch.setenv("DATABASE_URL", url.render_as_string(hide_password=False))
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "db/migrations"))
    if legacy:
        command.stamp(config, "f072ddcd1e53")
    command.upgrade(config, "head")
    inspector = sa.inspect(engine)
    assert {"eval_runs", "question_scores", "category_scores", "fingerprint_reports"} <= set(inspector.get_table_names())
    assert {"provenance", "completion", "evaluation_errors", "baseline_bootstrap", "baseline_run_id"} <= {c["name"] for c in inspector.get_columns("eval_runs")}
    assert "report" in {c["name"] for c in inspector.get_columns("fingerprint_reports")}
    assert next(c for c in inspector.get_columns("fingerprint_reports") if c["name"] == "baseline_run_id")["nullable"]
    if legacy:
        with engine.connect() as conn:
            assert conn.execute(sa.text("SELECT count(*) FROM eval_runs WHERE branch='legacy'")).scalar_one() == 1
    if schema:
        with engine.begin() as conn:
            conn.execute(sa.text(f'DROP SCHEMA "{schema}" CASCADE'))
    engine.dispose()


@pytest.mark.asyncio
async def test_worker_high_regression_report_and_baseline_eligibility(pipeline, client, storage, monkeypatch):
    submit, _ = pipeline
    baseline = await submit()
    monkeypatch.setattr(RAGEvaluator, "_run_ragas", lambda self,d: pd.DataFrame([
        {"answer_correctness":.8,"faithfulness":.99,"context_recall":.99,"context_precision":.99} for _ in d]))
    current = await submit(baseline)
    response = await client.get(f"/results/{current}/fingerprint")
    assert response.status_code == 200, response.text
    assert response.json()["quality_passed"]  # Absolute thresholds pass, regression gate fails.
    with pytest.raises(ValueError, match="HIGH"):
        check_report(response.json())
    assert (await client.get(f"/results/{current}")).json()["passed"] is False
    async with storage() as db:
        run = await get_run_by_id(db,current)
        with pytest.raises(ValueError, match="thresholds"):
            await resolve_baseline(db,current,None,Provenance.model_validate(run.provenance))


@pytest.mark.asyncio
async def test_post_dispatch_configuration_change_fails(pipeline, client, monkeypatch):
    submit, tasks = pipeline
    baseline = await submit()
    response = await client.post("/evaluate/",json={"commit_sha":"a"*40,"branch":"test",
        "rag_endpoint":"http://rag/query", "baseline_run_id":baseline})
    run_id = response.json()["run_id"]
    monkeypatch.setenv("EVAL_JUDGE_MODEL", "changed-judge")
    with pytest.raises(ValueError, match="changed after dispatch"):
        await _async_run_evaluation(None, **tasks[run_id])
    detail = (await client.get(f"/results/{run_id}")).json()
    assert detail["status"] == "failed" and detail["passed"] is False
    assert detail["completion"]["attempted"] == 0


def test_gate_cli_fails_missing_and_malformed_reports(tmp_path):
    import subprocess
    import sys
    path = tmp_path / "report.json"
    assert subprocess.run([sys.executable,"-m","core.gate",str(path)], cwd=ROOT, capture_output=True).returncode == 1
    path.write_text("{}")
    assert subprocess.run([sys.executable,"-m","core.gate",str(path)], cwd=ROOT, capture_output=True).returncode == 1
    path.write_text(json.dumps(make_report().model_dump(mode="json")))
    assert subprocess.run([sys.executable,"-m","core.gate",str(path)], cwd=ROOT, capture_output=True).returncode == 0
