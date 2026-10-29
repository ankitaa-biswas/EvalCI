"""Authenticated CI evaluation client. All missing/error/incomplete data fails closed."""
import argparse
import json
import os
from pathlib import Path
import time

import httpx

from core.gate import check_evaluation


def dump(path, payload):
    path.write_text(json.dumps(payload,sort_keys=True,indent=2,allow_nan=False)+"\n")


def wait_for_run(client, run_id, timeout, interval):
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline:
        response = client.get(f"/evaluate/{run_id}/status")
        response.raise_for_status()
        payload = response.json()
        if payload.get("run_id") != run_id: raise ValueError("Status response belongs to another run")
        if payload.get("status") == "complete": return payload
        if payload.get("status") == "failed": raise ValueError(f"Evaluation failed: {payload.get('error_message')}")
        if payload.get("status") not in {"pending","running"}: raise ValueError("Malformed evaluation status")
        time.sleep(min(interval,max(0,deadline-time.monotonic())))
    raise TimeoutError(f"Evaluation {run_id} exceeded its {timeout}s CI deadline")


def evaluate(client, *, target, sha, role, output, timeout, mode, baseline=None):
    payload = {"commit_sha":sha,"branch":f"ci/{role}","triggered_by":"ci-container-harness","rag_endpoint":target}
    payload.update({"baseline_run_id":baseline} if baseline else {"baseline_bootstrap":True})
    response = client.post("/evaluate/",json=payload)
    response.raise_for_status()
    if response.status_code != 202: raise ValueError("Evaluation submission was not accepted")
    run_id = response.json()["run_id"]
    dump(output/f"{role}-submission.json",{"run_id":run_id,**payload})
    try:
        wait_for_run(client,run_id,timeout,.25 if mode == "fixture" else 2)
    finally:
        # Keep diagnostics even on failure; only validated successful payloads
        # can be passed to check_evaluation below.
        detail = client.get(f"/results/{run_id}")
        dump(output/f"{role}-results.json",detail.json())
    detail.raise_for_status()
    response = client.get(f"/results/{run_id}/fingerprint")
    response.raise_for_status()
    report = response.json()
    dump(output/f"{role}-report.json",report)
    questions = json.loads((Path(__file__).resolve().parents[1]/"tests/test_suite.json").read_text())
    checked = check_evaluation(detail.json(),report,run_id=run_id,commit_sha=sha,mode=mode,
        questions=questions,allow_bootstrap=baseline is None)
    if baseline and str(checked.baseline_run_id) != baseline: raise ValueError("Wrong explicit baseline selected")
    return run_id


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-sha",required=True)
    parser.add_argument("--candidate-sha",required=True)
    parser.add_argument("--mode",choices=["fixture","ragas"],required=True)
    parser.add_argument("--output",type=Path,default=Path("/tmp/ci-results"))
    parser.add_argument("--timeout-seconds",type=int,default=600)
    parser.add_argument("--candidate-endpoint",default="http://rag-target:8001/query")
    args = parser.parse_args()
    if args.timeout_seconds <= 0: parser.error("Timeout must be positive")
    args.output.mkdir(parents=True,exist_ok=True)
    key = os.environ["EVALCI_API_KEYS"].split(",")[0]
    try:
        with httpx.Client(base_url="http://api:8080",headers={"X-API-Key":key},timeout=30) as client:
            ready = client.get("/ready"); ready.raise_for_status()
            if ready.json().get("evaluation_mode") != args.mode: raise ValueError("Unexpected scoring mode")
            identity = client.get("/identity");identity.raise_for_status()
            dump(args.output/"harness-identity.json",identity.json())
            baseline = evaluate(client,target="http://rag-baseline:8001/query",sha=args.baseline_sha,
                role="baseline",output=args.output,timeout=args.timeout_seconds,mode=args.mode)
            candidate = evaluate(client,target=args.candidate_endpoint,sha=args.candidate_sha,
                role="candidate",output=args.output,timeout=args.timeout_seconds,mode=args.mode,baseline=baseline)
            dump(args.output/"gate.json",{"passed":True,"mode":args.mode,"baseline_run_id":baseline,"run_id":candidate,
                "commit_sha":args.candidate_sha,"low_medium_policy":"recorded; allowed only if absolute quality passes"})
        return 0
    except Exception as exc:
        dump(args.output/"gate.json",{"passed":False,"mode":args.mode,"commit_sha":args.candidate_sha,"error":str(exc),"error_type":type(exc).__name__})
        print(f"CI quality gate FAILED: {exc}")
        return 1


if __name__ == "__main__": raise SystemExit(main())
