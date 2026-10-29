"""Run after stopping this test project's rag-target service; restart it afterward."""
import argparse
import json
from pathlib import Path
import subprocess

import httpx
from dotenv import dotenv_values
from scripts.verify_cycle2 import collect


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url",default="http://localhost:8080")
    parser.add_argument("--env-file",type=Path,required=True)
    parser.add_argument("--output",type=Path,default=Path("artifacts/cycle2"))
    args = parser.parse_args()
    baseline = json.loads((args.output/"bootstrap-report.json").read_text())["run_id"]
    key = dotenv_values(args.env_file)["EVALCI_API_KEYS"].split(",")[0]
    with httpx.Client(base_url=args.api_url,headers={"X-API-Key":key},timeout=180) as client:
        assert client.get("/ready").json()["evaluation_mode"] == "fixture"
        payload = {"commit_sha":subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip(),
            "branch":"cycle2-target-outage", "baseline_run_id":baseline,"rag_endpoint":"http://rag-target:8001/query"}
        response = client.post("/evaluate/",json=payload)
        response.raise_for_status()
        run_id = response.json()["run_id"]
        events = collect(client,run_id)
        detail = client.get(f"/results/{run_id}").json()
        assert events[-1]["event"] == "error"
        assert detail["status"] == "failed" and detail["passed"] is False
        assert detail["completion"] == {"expected":50,"attempted":50,"successfully_scored":0,"failed":50}
        assert client.get(f"/results/{run_id}/fingerprint").status_code == 409
        (args.output/"stopped-target-results.json").write_text(json.dumps(detail,indent=2))
        (args.output/"stopped-target-events.json").write_text(json.dumps(events,indent=2))
        print(json.dumps({"run_id":run_id,"stopped_service_failure_verified":True,"completion":detail["completion"]}))


if __name__ == "__main__": main()
