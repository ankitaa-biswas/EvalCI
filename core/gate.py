"""Strict consumer of the canonical report. python -m core.gate REPORT [--run-id UUID]."""
import argparse
import json
from pathlib import Path
import sys

from core.reports import FingerprintReport


def check_report(payload, *, run_id=None, commit_sha=None):
    report = FingerprintReport.model_validate(payload)
    if run_id is not None and str(report.run_id) != str(run_id):
        raise ValueError("Report belongs to another run")
    if commit_sha is not None and report.provenance.commit_sha != commit_sha:
        raise ValueError("Report belongs to another revision")
    if report.comparison_status != "compared":
        raise ValueError("Bootstrap did not perform a regression comparison")
    if not report.quality_passed:
        raise ValueError("Required absolute quality thresholds failed")
    high = [r for r in report.regressions if r.severity == "HIGH"]
    if high:
        raise ValueError(f"{len(high)} HIGH-severity metric regressions")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--commit-sha")
    args = parser.parse_args()
    try:
        report = check_report(json.loads(args.report.read_text()), run_id=args.run_id, commit_sha=args.commit_sha)
    except (OSError, ValueError, TypeError) as exc:
        print(f"Quality gate FAILED: {exc}", file=sys.stderr)
        return 1
    print(f"Quality gate passed; {len(report.regressions)} LOW/MEDIUM metric regressions recorded.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
