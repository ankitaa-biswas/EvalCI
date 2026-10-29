"""Synthetic contract/policy tests and mocked CI HTTP responses, not live LLM evidence."""
from copy import deepcopy
import json
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import yaml

from core.evaluator import aggregate_by_category
from core.fingerprinter import RegressionFingerprinter
from core.gate import check_evaluation
from core.reports import Completion, METRICS, Provenance, digest
from scripts.ci_context import choose_baseline
from scripts.ci_evaluate import wait_for_run
from scripts.ci_policy import check_jobs
from scripts.deliver import publication_allowed, validate_bundle
from scripts.image_identity import build_identity, read_identity, source_digest
from tests.integration.fixture_scoring import score_fixture

SHA = "a"*40
BASE_SHA = "b"*40
ROOT = Path(__file__).resolve().parents[1]


def evidence(value=.9):
    config = {"scoring_mode":"fixture", "category_counts":{"one":1,"two":1},
        "category_thresholds":{c:{"correctness":.1} for c in ("one","two")},
        "global_thresholds":{"correctness":.1},"overall_correctness_threshold":.1}
    def prov(sha):
        return Provenance(dataset_sha256=digest(["q1","q2"]),configuration_sha256=digest(config),configuration=config,commit_sha=sha,rag_endpoint="http://target/query")
    baseline_id,run_id=uuid4(),uuid4()
    completion=Completion(expected=2,attempted=2,successfully_scored=2,failed=0)
    def snapshot(v): return {"question_count":1,**{m:1-v if m=="hallucination_risk" else v for m in METRICS}}
    baseline={c:snapshot(.9) for c in config["category_counts"]}
    current={c:snapshot(value) for c in config["category_counts"]}
    base=RegressionFingerprinter().report(run_id=baseline_id,baseline_run_id=None,completion=completion,provenance=prov(BASE_SHA),baseline_provenance=None,current=baseline,baseline={},quality_passed=True)
    report=RegressionFingerprinter().report(run_id=run_id,baseline_run_id=baseline_id,completion=completion,provenance=prov(SHA),baseline_provenance=prov(BASE_SHA),current=current,baseline=baseline,quality_passed=True)
    def detail(r,v):
        rows=[{"question_id":qid,"category":cat,"question":"synthetic?","ground_truth":"expected","answer":"synthetic answer","contexts":["synthetic context"],
            "correctness":v,"grounding":v,"context_recall":v,"context_precision":v,"hallucination_risk":1-v} for qid,cat in (("q1","one"),("q2","two"))]
        return {"run_id":str(r.run_id),"commit_sha":r.provenance.commit_sha,"status":"complete","passed":r.gate_passed,
            "baseline_run_id":str(r.baseline_run_id) if r.baseline_run_id else None,"completion":completion.model_dump(),
            "provenance":r.provenance.model_dump(mode="json"),"evaluation_errors":[],"error_message":None,
            "per_question_scores":rows,"category_scores":aggregate_by_category(rows,config["category_thresholds"]),"overall_score":v}
    return detail(report,value),report.model_dump(mode="json"),detail(base,.9),base.model_dump(mode="json")


@pytest.mark.parametrize("value",[.9,.88,.85])
def test_results_and_report_gate_allow_explicit_low_medium_policy(value):
    results,report,_,_=evidence(value)
    assert check_evaluation(results,report,commit_sha=SHA,mode="fixture").gate_passed


@pytest.mark.parametrize("mutation",[
    lambda r:r.update(status="failed"),lambda r:r.update(passed=False),
    lambda r:r.update(overall_score=.1),lambda r:r["per_question_scores"].pop(),
    lambda r:r["per_question_scores"][0].update(correctness=float("nan")),
    lambda r:r["per_question_scores"][0].update(question_id="q2"),
    lambda r:r["category_scores"]["one"].update(avg_correctness=.1),
    lambda r:r.update(evaluation_errors=[{"error":"synthetic scoring failure"}]),
    lambda r:r.update(baseline_run_id=str(uuid4())),lambda r:r.update(completion={}),
])
def test_inconsistent_results_fail_closed(mutation):
    results,report,_,_=evidence()
    mutation(results)
    with pytest.raises(ValueError):check_evaluation(results,report,commit_sha=SHA,mode="fixture")


def test_missing_malformed_wrong_mode_report_fails():
    results,report,_,_=evidence()
    for malformed in ({},None,{"overall_regressed":False}):
        with pytest.raises(ValueError):check_evaluation(results,malformed)
    with pytest.raises(ValueError,match="mode"):check_evaluation(results,report,mode="ragas")
    with pytest.raises(ValueError,match="question IDs"):
        check_evaluation(results,report,questions=[{"id":"wrong","category":"one"}])


def test_fixture_detects_actual_answer_contract_change():
    good=score_fixture([{"answer":"first","contexts":["first","second"]}]).iloc[0]
    wrong=score_fixture([{"answer":"second","contexts":["first","second"]}]).iloc[0]
    assert good["answer_correctness"]==.99 and wrong["answer_correctness"]==.84
    assert wrong["faithfulness"]==.99  # Synthetic correctness HIGH; grounding unchanged.
    with pytest.raises(ValueError):score_fixture([{"answer":"x","contexts":[]}])


def test_source_identity_verifies_actual_packaged_files(tmp_path):
    for part in ("api","core","db","rag_target","scripts","tests"):
        (tmp_path/part).mkdir();(tmp_path/part/"module.py").write_text("x = 1\n")
    for name in ("requirements.lock","alembic.ini"):(tmp_path/name).write_text("synthetic fixture\n")
    identity=build_identity(tmp_path,SHA,SHA)
    (tmp_path/"build-info.json").write_text(json.dumps(identity))
    assert read_identity(tmp_path)["source_verified"]
    (tmp_path/"rag_target/module.py").write_text("x = 2\n")
    with pytest.raises(ValueError,match="changed"):read_identity(tmp_path)


def test_baseline_selection_is_explicit_and_immutable():
    event={"pull_request":{"base":{"sha":BASE_SHA}},"before":BASE_SHA}
    assert choose_baseline("pull_request",event,None,None,None)==(BASE_SHA,"pull_request_base_sha")
    assert choose_baseline("push",event,None,None,None)==(BASE_SHA,"push_before_sha")
    assert choose_baseline("push",{"before":"0"*40},None,BASE_SHA,None)==(BASE_SHA,"new_branch_default_sha")
    assert choose_baseline("push",event,SHA,None,None)==(SHA,"repository_pinned_sha")
    with pytest.raises(ValueError):choose_baseline("push",{"before":"0"*40},None,None,None)


@pytest.mark.parametrize("status",["failure","cancelled","skipped",None,""])
@pytest.mark.parametrize("job",["context","unit-tests","rag-regression"])
def test_always_run_gate_rejects_every_unsuccessful_required_job(job,status):
    jobs={name:{"result":"success"} for name in ("context","unit-tests","rag-regression")}
    jobs["live-evaluation"]={"result":"skipped"}
    jobs[job]={"result":status}
    with pytest.raises(ValueError):check_jobs(jobs)


def test_live_policy_and_delivery_fail_closed():
    jobs={name:{"result":"success"} for name in ("context","unit-tests","rag-regression")}
    jobs["live-evaluation"]={"result":"skipped"}
    check_jobs(jobs)
    with pytest.raises(ValueError):check_jobs(jobs,require_live=True)
    jobs["live-evaluation"]["result"]="failure"
    with pytest.raises(ValueError):check_jobs(jobs)
    assert publication_allowed("push","refs/heads/main","main","true",True)
    for event,ref,enabled,live in (("pull_request","refs/heads/main","true",True),("push","refs/heads/feature","true",True),("push","refs/heads/main","false",True),("push","refs/heads/main","true",False)):
        assert not publication_allowed(event,ref,"main",enabled,live)


@pytest.mark.parametrize("response",[httpx.Response(500),httpx.Response(200,json={"run_id":"run","status":"unknown"}),httpx.Response(200,json={"run_id":"other","status":"complete"}),httpx.Response(200,json={"run_id":"run","status":"failed","error_message":"synthetic failure"})])
def test_mocked_status_errors_fail(response):
    with httpx.Client(base_url="http://api",transport=httpx.MockTransport(lambda request:response)) as client:
        with pytest.raises((ValueError,httpx.HTTPStatusError)):wait_for_run(client,"run",1,0)


def test_mocked_timeout_fails(monkeypatch):
    import scripts.ci_evaluate as module
    ticks=iter([0,.5,.8,2])
    monkeypatch.setattr(module.time,"monotonic",lambda:next(ticks))
    monkeypatch.setattr(module.time,"sleep",lambda _:None)
    with httpx.Client(base_url="http://api",transport=httpx.MockTransport(lambda request:httpx.Response(200,json={"run_id":"run","status":"running"}))) as client:
        with pytest.raises(TimeoutError):wait_for_run(client,"run",1,.1)


def test_delivery_bundle_validation_and_failure_rejection(tmp_path):
    results,report,base_results,base_report=evidence()
    def identity(sha):return {"revision_sha":sha,"harness_revision_sha":SHA,"target_source_sha256":"1"*64,"source_verified":True}
    manifest={"passed":True,"dirty":False,"mode":"fixture","candidate":{"revision_sha":SHA,"source_sha256":"1"*64,"runtime_identity":identity(SHA)},"baseline":{"revision_sha":BASE_SHA,"source_sha256":"1"*64,"runtime_identity":identity(BASE_SHA)}}
    files={"manifest.json":manifest,"candidate-results.json":results,"candidate-report.json":report,"baseline-results.json":base_results,"baseline-report.json":base_report,
        "gate.json":{"passed":True,"run_id":report["run_id"],"commit_sha":SHA},"harness-identity.json":identity(SHA)}
    for name,data in files.items():(tmp_path/name).write_text(json.dumps(data))
    assert validate_bundle(tmp_path,SHA,BASE_SHA,"fixture")["passed"]
    for bad in (False,"true",None):
        (tmp_path/"manifest.json").write_text(json.dumps({**manifest,"passed":bad}))
        with pytest.raises(ValueError):validate_bundle(tmp_path,SHA,BASE_SHA,"fixture")
    (tmp_path/"manifest.json").write_text(json.dumps(manifest))
    (tmp_path/"candidate-report.json").unlink()
    with pytest.raises(FileNotFoundError):validate_bundle(tmp_path,SHA,BASE_SHA,"fixture")


def test_workflow_trigger_security_and_stable_gate_contract():
    workflow=yaml.load((ROOT/".github/workflows/evalci.yml").read_text(),Loader=yaml.BaseLoader)
    assert workflow["on"]["push"]["branches"]==["**"]
    assert "pull_request" in workflow["on"] and "pull_request_target" not in workflow["on"]
    assert workflow["permissions"]=={"contents":"read"}
    assert workflow["jobs"]["quality-gate"]["name"]=="Quality Gate"
    assert workflow["jobs"]["quality-gate"]["if"]=="always()"
    for name in ("context","unit-tests","rag-regression"):
        assert "secrets." not in json.dumps(workflow["jobs"][name])
    delivery=workflow["jobs"]["delivery"]
    assert "github.event_name == 'push'" in delivery["if"]
    assert "needs.live-evaluation.result == 'success'" in delivery["if"]
    assert delivery["permissions"]=={"contents":"read","packages":"write"}
    assert delivery["environment"]=="delivery"
    for job in workflow["jobs"].values():
        for step in job["steps"]:
            if "uses" in step:assert len(step["uses"].rsplit("@",1)[1])==40
