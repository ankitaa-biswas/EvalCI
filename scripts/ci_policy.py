"""Always-run workflow aggregation. Missing, failed, cancelled and required skips fail."""
import argparse
import json


def check_jobs(jobs, *, require_live=False):
    for job in ("context","unit-tests","rag-regression"):
        if jobs.get(job,{}).get("result") != "success": raise ValueError(f"Required job {job} did not succeed")
    live = jobs.get("live-evaluation",{}).get("result")
    if live not in {"success","skipped"}: raise ValueError("Live evaluation failed, was cancelled, or is missing")
    if require_live and live != "success": raise ValueError("Live evaluation is required by repository policy but unavailable or skipped")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--jobs",required=True)
    parser.add_argument("--require-live",choices=["true","false"],default="false")
    args = parser.parse_args()
    try:
        check_jobs(json.loads(args.jobs),require_live=args.require_live=="true")
    except (ValueError,TypeError,AttributeError) as exc:
        print(f"Quality Gate FAILED: {exc}")
        return 1
    print("Quality Gate passed. Mode and optional live-check status are recorded in the workflow summary.")
    return 0


if __name__ == "__main__": raise SystemExit(main())
