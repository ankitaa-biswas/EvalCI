"""Rule-based regression evidence. Weights are not causal probabilities."""
from core.reports import (
    DIRECTIONS, METRICS, FingerprintReport, MetricComparison, metric_value, severity_for,
)


def attribute(deltas):
    losses = {m: max(0, round(-d * DIRECTIONS[m], 12)) for m, d in deltas.items()}
    retrieval = max(losses["context_recall"], losses["context_precision"])
    generation = max(losses["groundedness"], losses["correctness"])
    meaningful = any(severity_for(m, d) != "NONE" for m, d in deltas.items())
    evidence = [f"{m}: signed delta {d:+.4f} ({severity_for(m, d)})" for m, d in deltas.items()]
    # Retrieval losses may propagate downstream. Claim mixed evidence only when
    # generation degradation exceeds retrieval loss by at least .05.
    gen_signal = generation >= .02 and (retrieval < .02 or round(generation - retrieval, 12) >= .05)
    retr_signal = retrieval >= .02
    component = None if not meaningful else "mixed" if retr_signal and gen_signal else "retriever" if retr_signal else "generator" if gen_signal else "inconclusive"
    raw = {"retriever": retrieval if retr_signal else 0., "generator": generation if gen_signal else 0.}
    total = sum(raw.values())
    weights = {k: v / total if total else 0. for k, v in raw.items()}
    fixes = {
        None: "No failure investigation indicated.",
        "retriever": "Inspect relevant chunk coverage, ranking, chunking and index changes; compare retrieved passages.",
        "generator": "Inspect answer grounding, prompt/model changes and reasoning against the retrieved passages.",
        "mixed": "Inspect retrieval changes and downstream generation separately; retrieval losses may propagate into answers.",
        "inconclusive": "Inspect question-level outputs and scorer variability; metric evidence cannot isolate a component.",
    }
    evidence.append("Retrieval loss can propagate to generation metrics; these rules suggest investigation, not a proven cause.")
    return component, weights, evidence, fixes[component]


class RegressionFingerprinter:
    def compute(self, baseline: dict, current: dict) -> dict:
        """Single-category compatibility helper; report() is the worker contract."""
        metrics = METRICS[:4]
        deltas = {m: round(metric_value(current.get(m), m) - metric_value(baseline.get(m), m), 12) for m in metrics}
        component, weights, evidence, fix = attribute(deltas)
        return {"deltas": deltas, "attribution": weights, "dominant_failure": component,
                "severity": round(max(0., -deltas["correctness"]) * 10, 2),
                "evidence": evidence, "suggested_fix": fix}

    def report(self, *, run_id, baseline_run_id, completion, provenance,
               baseline_provenance, current, baseline, quality_passed):
        comparisons = []
        if baseline_run_id is not None:
            if set(current) != set(baseline):
                raise ValueError("Baseline category coverage differs")
            if {cat: row["question_count"] for cat, row in current.items()} != provenance.configuration["category_counts"]:
                raise ValueError("Category question coverage differs from dataset")
            for cat in sorted(current):
                b, c = baseline[cat], current[cat]
                if b["question_count"] != c["question_count"]:
                    raise ValueError(f"Baseline question count differs for {cat}")
                deltas = {m: round(metric_value(c.get(m), m) - metric_value(b.get(m), m), 12) for m in METRICS}
                component, weights, evidence, fix = attribute(deltas)
                for m in METRICS:
                    severity = severity_for(m, deltas[m])
                    comparisons.append(MetricComparison(category=cat, metric=m,
                        baseline_score=b[m], current_score=c[m], delta=deltas[m], severity=severity,
                        attributed_to=component if severity != "NONE" else None,
                        attribution_weights=weights if severity != "NONE" else {"retriever": 0., "generator": 0.},
                        evidence=evidence, suggested_fix=fix if severity != "NONE" else "No failure investigation indicated."))
        regressions = [c for c in comparisons if c.severity != "NONE"]
        components = {r.attributed_to for r in regressions}
        top = None if not components else next(iter(components)) if len(components) == 1 else "mixed" if "retriever" in components and "generator" in components or "mixed" in components else "inconclusive"
        return FingerprintReport(run_id=run_id, baseline_run_id=baseline_run_id,
            comparison_status="compared" if baseline_run_id else "bootstrap", completion=completion,
            provenance=provenance, baseline_provenance=baseline_provenance, quality_passed=quality_passed,
            overall_regressed=bool(regressions), comparisons=comparisons, regressions=regressions,
            top_failing_component=top, summary=f"{len(regressions)} category metric regression(s)." if baseline_run_id else "Baseline bootstrap only; no regression comparison performed.",
            action_items=sorted({r.suggested_fix for r in regressions}))
