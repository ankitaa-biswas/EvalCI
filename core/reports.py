"""Versioned evaluation contract shared by computation, storage, HTTP and gate.

All quality metrics are in [0, 1]. Deltas are current minus baseline.
Loss >= .02 is LOW, >= .05 MEDIUM, >= .15 HIGH (inclusive boundaries).
Loss below .02 is NONE. Decimal deltas are rounded to 12 places before rules.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator

METRICS = ("correctness", "groundedness", "context_recall", "context_precision", "hallucination_risk")
DIRECTIONS = {m: (-1 if m == "hallucination_risk" else 1) for m in METRICS}
Severity = Literal["NONE", "LOW", "MEDIUM", "HIGH"]
Component = Literal["retriever", "generator", "mixed", "inconclusive"]


def metric_value(value, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name}: required numeric metric is missing or invalid")
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError(f"{name}: metric must be finite and in [0, 1]")
    return float(value)


def severity_for(metric: str, delta: float) -> Severity:
    loss = round(-delta * DIRECTIONS[metric], 12)
    return "HIGH" if loss >= .15 else "MEDIUM" if loss >= .05 else "LOW" if loss >= .02 else "NONE"


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Completion(Contract):
    expected: int = Field(ge=1, strict=True)
    attempted: int = Field(ge=0, strict=True)
    successfully_scored: int = Field(ge=0, strict=True)
    failed: int = Field(ge=0, strict=True)

    @model_validator(mode="after")
    def accounting(self):
        if not self.successfully_scored + self.failed == self.attempted <= self.expected:
            raise ValueError("Completion counts are inconsistent")
        return self

    @property
    def complete(self):
        return self.expected == self.attempted == self.successfully_scored and self.failed == 0


class Provenance(Contract):
    dataset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    configuration: dict
    commit_sha: str
    rag_endpoint: str

    @model_validator(mode="after")
    def configuration_digest(self):
        if digest(self.configuration) != self.configuration_sha256:
            raise ValueError("Evaluation configuration digest mismatch")
        return self


class MetricComparison(Contract):
    category: str
    metric: Literal["correctness", "groundedness", "context_recall", "context_precision", "hallucination_risk"]
    baseline_score: float
    current_score: float
    delta: float
    severity: Severity
    attributed_to: Component | None
    attribution_weights: dict[str, float]
    evidence: list[str]
    suggested_fix: str

    @field_validator("baseline_score", "current_score", "delta", mode="before")
    @classmethod
    def numeric(cls, value):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("Metric scores and deltas must be numeric")
        return value

    @model_validator(mode="after")
    def consistent(self):
        metric_value(self.baseline_score, self.metric)
        metric_value(self.current_score, self.metric)
        if self.delta != round(self.current_score - self.baseline_score, 12):
            raise ValueError("Signed metric delta mismatch")
        if self.severity != severity_for(self.metric, self.delta):
            raise ValueError("Metric severity mismatch")
        if set(self.attribution_weights) != {"retriever", "generator"}:
            raise ValueError("Invalid attribution weights")
        if any(v < 0 or v > 1 for v in self.attribution_weights.values()):
            raise ValueError("Invalid attribution weight range")
        if self.severity == "NONE" and self.attributed_to is not None:
            raise ValueError("No failure attribution allowed for non-regression")
        return self


class FingerprintReport(Contract):
    schema_version: Literal[1] = 1
    run_id: UUID
    baseline_run_id: UUID | None
    comparison_status: Literal["compared", "bootstrap"]
    completion: Completion
    provenance: Provenance
    baseline_provenance: Provenance | None
    quality_passed: bool = Field(strict=True)
    overall_regressed: bool = Field(strict=True)
    comparisons: list[MetricComparison]
    regressions: list[MetricComparison]
    top_failing_component: Component | None
    attribution_method: Literal["rule-based weights; not calibrated probabilities or causal certainty"] = "rule-based weights; not calibrated probabilities or causal certainty"
    summary: str
    action_items: list[str]

    @model_validator(mode="after")
    def consistent(self):
        if not self.completion.complete:
            raise ValueError("Incomplete evaluation cannot produce a comparison report")
        expected_categories = self.provenance.configuration.get("category_counts", {})
        if not expected_categories or sum(expected_categories.values()) != self.completion.expected:
            raise ValueError("Dataset size does not match completion")
        if self.comparison_status == "bootstrap":
            if self.baseline_run_id or self.baseline_provenance or self.comparisons or self.regressions or self.overall_regressed or self.top_failing_component:
                raise ValueError("Bootstrap cannot claim a regression comparison")
            return self
        if not self.baseline_run_id or self.baseline_run_id == self.run_id or not self.baseline_provenance:
            raise ValueError("A distinct explicit baseline is required")
        if (self.provenance.dataset_sha256, self.provenance.configuration_sha256) != (self.baseline_provenance.dataset_sha256, self.baseline_provenance.configuration_sha256):
            raise ValueError("Incompatible baseline dataset or evaluation configuration")
        pairs = {(c.category, c.metric) for c in self.comparisons}
        if not expected_categories or pairs != {(c, m) for c in expected_categories for m in METRICS} or len(pairs) != len(self.comparisons):
            raise ValueError("Missing, duplicate or unexpected category metric comparisons")
        if sum(expected_categories.values()) != self.completion.expected:
            raise ValueError("Dataset size does not match completion")
        regressions = [c for c in self.comparisons if c.severity != "NONE"]
        if regressions != self.regressions or self.overall_regressed != bool(regressions):
            raise ValueError("Regression summary is inconsistent")
        if bool(regressions) != (self.top_failing_component is not None):
            raise ValueError("Failure attribution summary is inconsistent")
        # Verify absolute thresholds from the reported category metrics; a
        # misleading quality_passed flag must not bypass the gate.
        config = self.provenance.configuration
        by_category = {cat: {c.metric: c.current_score for c in self.comparisons if c.category == cat} for cat in expected_categories}
        def passes(metrics, thresholds):
            for name, threshold in thresholds.items():
                m = "groundedness" if name == "grounding" else name
                if m not in DIRECTIONS or round((metrics[m] - metric_value(threshold, name)) * DIRECTIONS[m], 12) < 0:
                    return False
            return True
        global_metrics = {m: sum(by_category[cat][m] * count for cat, count in expected_categories.items()) / self.completion.expected for m in METRICS}
        actual_quality = (passes(global_metrics, config["global_thresholds"]) and
            passes(global_metrics, {"correctness": config["overall_correctness_threshold"]}) and
            all(passes(values, config["category_thresholds"][cat]) for cat, values in by_category.items()))
        if self.quality_passed != actual_quality:
            raise ValueError("Absolute quality result differs from reported metrics")
        return self

    @property
    def gate_passed(self):
        return self.comparison_status == "compared" and self.quality_passed and not any(r.severity == "HIGH" for r in self.regressions)
