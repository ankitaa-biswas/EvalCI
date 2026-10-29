"""Strict consumer of the canonical report. python -m core.gate REPORT [--run-id UUID]."""
import argparse
import json
import math
import statistics
from collections import Counter
from pathlib import Path
import sys

from core.reports import FingerprintReport, METRICS, DIRECTIONS, metric_value


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


def check_evaluation(results, payload, *, run_id=None, commit_sha=None, mode=None,
                     questions=None, allow_bootstrap=False):
    """Validate required results and report together before making the gate decision."""
    report = FingerprintReport.model_validate(payload)
    if not isinstance(results, dict) or results.get("status") != "complete":
        raise ValueError("Evaluation did not complete successfully")
    if results.get("run_id") != str(report.run_id) or results.get("commit_sha") != report.provenance.commit_sha:
        raise ValueError("Results and report identities differ")
    if results.get("completion") != report.completion.model_dump() or results.get("provenance") != report.provenance.model_dump(mode="json"):
        raise ValueError("Results/report completion or provenance mismatch")
    expected_baseline = str(report.baseline_run_id) if report.baseline_run_id else None
    if results.get("baseline_run_id") != expected_baseline:
        raise ValueError("Results/report baseline mismatch")
    if type(results.get("passed")) is not bool or results["passed"] != report.gate_passed:
        raise ValueError("Results/report quality gate mismatch")
    if results.get("evaluation_errors") != [] or results.get("error_message"):
        raise ValueError("Evaluation contains errors")
    if run_id is not None and str(report.run_id) != str(run_id):
        raise ValueError("Report belongs to another run")
    if commit_sha is not None and report.provenance.commit_sha != commit_sha:
        raise ValueError("Report belongs to another revision")
    config = report.provenance.configuration
    if mode is not None and config.get("scoring_mode") != mode:
        raise ValueError("Evaluation mode differs from required mode")
    rows = results.get("per_question_scores")
    counts = config["category_counts"]
    if not isinstance(rows, list) or len(rows) != report.completion.expected:
        raise ValueError("Missing per-question results")
    if len({r.get("question_id") for r in rows}) != len(rows) or any(not r.get("question_id") for r in rows):
        raise ValueError("Missing or duplicate question IDs")
    if Counter(r.get("category") for r in rows) != counts:
        raise ValueError("Per-question category coverage differs from dataset")
    if questions is not None and {r["question_id"]:r["category"] for r in rows} != {q["id"]:q["category"] for q in questions}:
        raise ValueError("Scored question IDs differ from the required dataset")
    names = {m: "grounding" if m == "groundedness" else m for m in METRICS}
    for row in rows:
        for metric, field in names.items(): metric_value(row.get(field), metric)
        if not math.isclose(row["hallucination_risk"], 1-row["grounding"], abs_tol=1e-9):
            raise ValueError("Derived hallucination risk is inconsistent")
    categories = results.get("category_scores")
    if not isinstance(categories, dict) or set(categories) != set(counts):
        raise ValueError("Missing category results")
    means = {}
    def passes(metrics, thresholds):
        return all(round((metrics["groundedness" if m == "grounding" else m] - metric_value(t,m)) * DIRECTIONS["groundedness" if m == "grounding" else m],12) >= 0 for m,t in thresholds.items())
    for category, count in counts.items():
        group = [r for r in rows if r["category"] == category]
        means[category] = {m:statistics.mean(r[field] for r in group) for m,field in names.items()}
        stored = categories[category]
        if stored.get("question_count") != count:
            raise ValueError("Category result count mismatch")
        for m,field in names.items():
            if not math.isclose(metric_value(stored.get("avg_"+field),m),means[category][m],rel_tol=0,abs_tol=1e-9):
                raise ValueError("Category aggregate differs from question scores")
        if type(stored.get("passed")) is not bool or stored["passed"] != passes(means[category],config["category_thresholds"][category]):
            raise ValueError("Category quality decision differs from question scores")
    for comparison in report.comparisons:
        if not math.isclose(comparison.current_score,means[comparison.category][comparison.metric],rel_tol=0,abs_tol=1e-9):
            raise ValueError("Report comparison differs from question scores")
    global_means = {m:statistics.mean(r[field] for r in rows) for m,field in names.items()}
    if not math.isclose(metric_value(results.get("overall_score"),"overall_score"),global_means["correctness"],rel_tol=0,abs_tol=1e-9):
        raise ValueError("Overall score differs from question scores")
    quality = (passes(global_means,config["global_thresholds"]) and
        passes(global_means,{"correctness":config["overall_correctness_threshold"]}) and all(c["passed"] for c in categories.values()))
    if report.quality_passed != quality:
        raise ValueError("Absolute quality decision differs from question scores")
    if allow_bootstrap and report.comparison_status == "bootstrap":
        if not quality: raise ValueError("Baseline bootstrap failed absolute quality thresholds")
        return report
    return check_report(payload,run_id=run_id,commit_sha=commit_sha)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--commit-sha")
    parser.add_argument("--results", type=Path)
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--mode", choices=["fixture", "ragas"])
    args = parser.parse_args()
    try:
        payload = json.loads(args.report.read_text())
        if args.results:
            report = check_evaluation(json.loads(args.results.read_text()),payload,run_id=args.run_id,
                commit_sha=args.commit_sha,mode=args.mode,questions=json.loads(args.dataset.read_text()) if args.dataset else None)
        else:
            report = check_report(payload,run_id=args.run_id,commit_sha=args.commit_sha)
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        print(f"Quality gate FAILED: {exc}", file=sys.stderr)
        return 1
    print(f"Quality gate passed; {len(report.regressions)} LOW/MEDIUM metric regressions recorded.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
