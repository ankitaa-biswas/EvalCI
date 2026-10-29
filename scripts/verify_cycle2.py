"""Exercise a running fixture stack through HTTP, Celery and durable SSE.

Writes clearly labeled transport evidence. Does not make live LLM quality claims.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time

import httpx
from dotenv import dotenv_values

from core.gate import check_report


def packets(response):
    packet = {}
    for line in response.iter_lines():
        if not line:
            if "data" in packet:
                yield {**packet, "data": json.loads(packet["data"])}
            packet = {}
        elif ":" in line:
            field, value = line.split(":",1)
            packet[field] = value.lstrip()


def collect(client, run_id, *, cursor=None, first_only=False):
    headers = {"Last-Event-ID":str(cursor)} if cursor else {}
    output = []
    deadline = time.monotonic() + 180
    with client.stream("GET",f"/stream/{run_id}",headers=headers) as response:
        response.raise_for_status()
        for packet in packets(response):
            if time.monotonic() > deadline:
                raise RuntimeError(f"Evaluation {run_id} did not reach terminal state within 180 seconds")
            if packet.get("event") == "ping": continue
            if packet.get("event") == "progress" and packet["data"]["stage"] in {"rag_requests","scoring"} and 0 < packet["data"]["completed"] < packet["data"]["total"]:
                # At least one current event in each stage must arrive before
                # the evaluation's terminal state, rather than replay only.
                if not any(e.get("observed_running_stage") == packet["data"]["stage"] for e in output):
                    status = client.get(f"/evaluate/{run_id}/status").json()["status"]
                    if status == "running": packet["observed_running_stage"] = packet["data"]["stage"]
            output.append(packet)
            if first_only or packet.get("event") in {"done","error"}: break
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url",default="http://localhost:8080")
    parser.add_argument("--env-file",type=Path)
    parser.add_argument("--commit-sha",help="Metadata only; required when running in the image without .git")
    parser.add_argument("--target-url",default="http://rag-target:8001/query")
    parser.add_argument("--failure-target-url",default="http://rag-target:1/query")
    parser.add_argument("--output",type=Path,default=Path("artifacts/cycle2"))
    args = parser.parse_args()
    values = dotenv_values(args.env_file) if args.env_file else os.environ
    key = values.get("EVALCI_API_KEYS", "").split(",")[0]
    if not key: raise RuntimeError("Set EVALCI_API_KEYS or provide --env-file")
    args.output.mkdir(parents=True,exist_ok=True)
    sha = args.commit_sha or subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip()
    summary = {"mode":"fixture", "claim":"deterministic transport verification; not live quality validation", "runs":[]}
    with httpx.Client(base_url=args.api_url,headers={"X-API-Key":key},timeout=180) as client:
        ready = client.get("/ready")
        ready.raise_for_status()
        if ready.json().get("evaluation_mode") != "fixture":
            raise RuntimeError("This verification command requires the explicit fixture stack; refusing live model calls")
        assert httpx.get(args.api_url+"/results/").status_code == 401
        assert httpx.get(args.api_url+"/dashboard/").status_code == 200
        def submit(target, baseline=None):
            payload = {"commit_sha":sha,"branch":"cycle2-transport-verification","rag_endpoint":target}
            payload.update({"baseline_run_id":baseline} if baseline else {"baseline_bootstrap":True})
            response = client.post("/evaluate/",json=payload)
            response.raise_for_status()
            return response.json()["run_id"]
        baseline = submit(args.target_url)
        first = collect(client,baseline,first_only=True)
        rest = collect(client,baseline,cursor=first[-1]["id"])
        events = first + rest
        assert all(int(e["id"]) > int(first[-1]["id"]) for e in rest if "id" in e)
        assert events[-1]["event"] == "done", events[-1]
        for stage in ("rag_requests","scoring"):
            assert any(e["data"]["stage"] == stage and 0 < e["data"]["completed"] < 50 for e in events)
        assert {e.get("observed_running_stage") for e in events} >= {"rag_requests","scoring"}
        late = collect(client,baseline)
        assert late[-1]["event"] == "done" and len(late) >= len(events)
        for role, run_id in (("bootstrap",baseline),("comparison",submit(args.target_url,baseline))):
            if role == "comparison":
                run_events = collect(client,run_id)
                assert run_events[-1]["event"] == "done"
            else: run_events = events
            detail = client.get(f"/results/{run_id}").json()
            report_response = client.get(f"/results/{run_id}/fingerprint")
            report_response.raise_for_status()
            report = report_response.json()
            assert len(detail["per_question_scores"]) == 50 and len(detail["category_scores"]) == 11
            assert detail["completion"] == dict(expected=50,attempted=50,successfully_scored=50,failed=0)
            assert report["provenance"]["configuration"]["scoring_mode"] == "fixture"
            if role == "comparison": assert check_report(report,run_id=run_id,commit_sha=sha).gate_passed
            else: assert detail["passed"] is False and report["comparison_status"] == "bootstrap"
            for suffix,data in (("results",detail),("report",report),("events",run_events)):
                (args.output/f"{role}-{suffix}.json").write_text(json.dumps(data,indent=2))
            summary["runs"].append({"role":role,"run_id":run_id,"event_count":len(run_events),"completion":detail["completion"]})
        failed = submit(args.failure_target_url,baseline)
        failed_events = collect(client,failed)
        assert failed_events[-1]["event"] == "error"
        detail = client.get(f"/results/{failed}").json()
        assert detail["status"] == "failed" and detail["passed"] is False
        assert detail["completion"]["failed"] == 50 and detail["completion"]["successfully_scored"] == 0
        assert client.get(f"/results/{failed}/fingerprint").status_code == 409
        (args.output/"unavailable-target-results.json").write_text(json.dumps(detail,indent=2))
        (args.output/"unavailable-target-events.json").write_text(json.dumps(failed_events,indent=2))
        summary["runs"].append({"role":"unavailable_target","run_id":failed,"completion":detail["completion"]})
        summary.update(readiness=ready.json(),incremental_request_events=True,incremental_scoring_events=True,
            reconnect_cursor=True,late_subscriber_terminal=True,authentication_checked=True)
    (args.output/"summary.json").write_text(json.dumps(summary,indent=2))
    print(json.dumps(summary,indent=2))


if __name__ == "__main__": main()
