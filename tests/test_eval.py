"""
tests/test_eval.py
──────────────────
Pytest integration tests for EvalCI's core algorithmic components.

Covers:
  - RegressionFingerprinter attribution (retriever, generator, no-regression)
  - HeatmapBuilder category grouping
  - FailureClusterer small-dataset fast-path (< 3 questions → trivial clusters)

All external dependencies (LLM, HTTP, Redis, sentence-transformers) are mocked
so these tests run fully offline with no real API calls.
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Lightweight stubs for heavy optional dependencies
# ---------------------------------------------------------------------------
# Prevent ImportError when sentence_transformers / sklearn / torch are absent
# from the test environment.  The clusterer uses lazy imports, so patching the
# top-level module namespace is sufficient.

def _install_stub(name: str, **attrs) -> MagicMock:
    """Install a MagicMock as sys.modules[name] if not already present."""
    if name not in sys.modules:
        mod = MagicMock()
        for k, v in attrs.items():
            setattr(mod, k, v)
        sys.modules[name] = mod
    return sys.modules[name]


_install_stub("sentence_transformers")
_install_stub("sklearn")
_install_stub("sklearn.cluster")
_install_stub("redis")
_install_stub("celery")
_install_stub("ragas")
_install_stub("httpx")


# ---------------------------------------------------------------------------
# Imports under test — after stubs are in place
# ---------------------------------------------------------------------------

from core.fingerprinter import RegressionFingerprinter  # noqa: E402
from core.heatmap import HeatmapBuilder                 # noqa: E402
from core.clusterer import FailureClusterer             # noqa: E402


# ===========================================================================
# Helpers / fixtures
# ===========================================================================

def _make_question(
    qid: str,
    category: str,
    dominant_failure: str = "retriever",
    *,
    context_recall: float = 0.8,
    context_precision: float = 0.8,
    groundedness: float = 0.8,
    correctness: float = 0.8,
) -> dict:
    """Return a minimal question result dict accepted by HeatmapBuilder / FailureClusterer."""
    return {
        "question_id":      qid,
        "question":         f"Sample question {qid}?",
        "category":         category,
        "dominant_failure": dominant_failure,
        "context_recall":    context_recall,
        "context_precision": context_precision,
        "groundedness":      groundedness,
        "correctness":       correctness,
    }


@pytest.fixture()
def fingerprinter() -> RegressionFingerprinter:
    return RegressionFingerprinter()


@pytest.fixture()
def heatmap_builder() -> HeatmapBuilder:
    return HeatmapBuilder()


@pytest.fixture()
def clusterer() -> FailureClusterer:
    return FailureClusterer()


# ===========================================================================
# test_fingerprinter_retriever_failure
# ===========================================================================

class TestFingerprintRetrieverFailure:
    """Metric deltas that clearly indicate the retriever has degraded."""

    # Retriever signal: both context_recall AND context_precision dropped
    # significantly.  Correctness followed, as expected when chunks are bad.
    BASELINE = {
        "context_recall":    0.85,
        "context_precision": 0.80,
        "groundedness":      0.82,
        "correctness":       0.78,
    }
    CURRENT = {
        "context_recall":    0.65,   # Δ = -0.20  (dropped hard)
        "context_precision": 0.60,   # Δ = -0.20  (dropped hard)
        "groundedness":      0.75,   # Δ = -0.07  (slight, secondary)
        "correctness":       0.60,   # Δ = -0.18  (followed retrieval down)
    }

    def test_dominant_failure_is_retriever(self, fingerprinter):
        result = fingerprinter.compute(self.BASELINE, self.CURRENT)
        assert result["dominant_failure"] == "retriever", (
            f"Expected dominant_failure='retriever', got {result['dominant_failure']!r}. "
            f"Full attribution: {result['attribution']}"
        )

    def test_retriever_weight_is_highest(self, fingerprinter):
        result = fingerprinter.compute(self.BASELINE, self.CURRENT)
        attr = result["attribution"]
        assert attr["retriever"] == max(attr.values()), (
            f"retriever weight {attr['retriever']:.3f} is not the highest in {attr}"
        )

    def test_severity_reflects_correctness_drop(self, fingerprinter):
        result = fingerprinter.compute(self.BASELINE, self.CURRENT)
        # correctness dropped 0.18 → severity ≈ 1.8
        assert result["severity"] == pytest.approx(1.8, abs=0.05)

    def test_deltas_are_negative_for_recall_and_precision(self, fingerprinter):
        result = fingerprinter.compute(self.BASELINE, self.CURRENT)
        assert result["deltas"]["context_recall"] < -0.05
        assert result["deltas"]["context_precision"] < -0.05


# ===========================================================================
# test_fingerprinter_generator_failure
# ===========================================================================

class TestFingerprintGeneratorFailure:
    """Metric deltas that point to the generator as the culprit.

    Pattern: groundedness drops (LLM ignoring good context) while retrieval
    metrics are stable.
    """

    BASELINE = {
        "context_recall":    0.84,
        "context_precision": 0.80,
        "groundedness":      0.85,
        "correctness":       0.78,
    }
    CURRENT = {
        "context_recall":    0.83,   # Δ = -0.01  (stable — noise only)
        "context_precision": 0.80,   # Δ =  0.00  (stable)
        "groundedness":      0.62,   # Δ = -0.23  (hard drop — LLM hallucinating)
        "correctness":       0.60,   # Δ = -0.18  (followed grounding down)
    }

    def test_dominant_failure_is_generator(self, fingerprinter):
        result = fingerprinter.compute(self.BASELINE, self.CURRENT)
        assert result["dominant_failure"] == "generator", (
            f"Expected dominant_failure='generator', got {result['dominant_failure']!r}. "
            f"Full attribution: {result['attribution']}"
        )

    def test_generator_weight_is_highest(self, fingerprinter):
        result = fingerprinter.compute(self.BASELINE, self.CURRENT)
        attr = result["attribution"]
        assert attr["generator"] == max(attr.values()), (
            f"generator weight {attr['generator']:.3f} is not the highest in {attr}"
        )

    def test_retrieval_stable(self, fingerprinter):
        """Recall and precision deltas must be within noise floor."""
        result = fingerprinter.compute(self.BASELINE, self.CURRENT)
        assert result["deltas"]["context_recall"]    > -0.05
        assert result["deltas"]["context_precision"] > -0.05

    def test_groundedness_dropped(self, fingerprinter):
        result = fingerprinter.compute(self.BASELINE, self.CURRENT)
        assert result["deltas"]["groundedness"] < -0.05


# ===========================================================================
# test_fingerprinter_no_regression
# ===========================================================================

class TestFingerprintNoRegression:
    """Stable or improving metrics should produce very low severity."""

    BASELINE = {
        "context_recall":    0.80,
        "context_precision": 0.75,
        "groundedness":      0.82,
        "correctness":       0.76,
    }
    # Tiny improvements — well within noise floor
    CURRENT = {
        "context_recall":    0.81,
        "context_precision": 0.76,
        "groundedness":      0.83,
        "correctness":       0.77,
    }

    def test_severity_below_threshold(self, fingerprinter):
        result = fingerprinter.compute(self.BASELINE, self.CURRENT)
        assert result["severity"] < 1.0, (
            f"Expected severity < 1.0 for a stable run, got {result['severity']}"
        )

    def test_all_deltas_non_negative(self, fingerprinter):
        result = fingerprinter.compute(self.BASELINE, self.CURRENT)
        for metric, delta in result["deltas"].items():
            assert delta >= 0, (
                f"Expected non-negative delta for {metric!r} in a no-regression run, "
                f"got {delta:.4f}"
            )

    def test_attribution_sums_to_one(self, fingerprinter):
        """Normalization invariant: attribution weights always sum to 1.0."""
        result = fingerprinter.compute(self.BASELINE, self.CURRENT)
        total = sum(result["attribution"].values())
        assert total == pytest.approx(1.0, abs=1e-9)


# ===========================================================================
# test_heatmap_build
# ===========================================================================

class TestHeatmapBuild:
    """HeatmapBuilder should correctly group question results by category."""

    # Two categories, two questions each.
    RESULTS = [
        _make_question("q1", "billing",  context_recall=0.9, context_precision=0.85, groundedness=0.88, correctness=0.82),
        _make_question("q2", "billing",  context_recall=0.7, context_precision=0.65, groundedness=0.70, correctness=0.60),
        _make_question("q3", "security", context_recall=0.8, context_precision=0.80, groundedness=0.75, correctness=0.72),
        _make_question("q4", "security", context_recall=0.6, context_precision=0.60, groundedness=0.65, correctness=0.55),
    ]

    def test_categories_are_present(self, heatmap_builder):
        result = heatmap_builder.build(self.RESULTS)
        assert set(result["categories"]) == {"billing", "security"}

    def test_categories_are_sorted(self, heatmap_builder):
        result = heatmap_builder.build(self.RESULTS)
        assert result["categories"] == sorted(result["categories"])

    def test_billing_correctness_is_average(self, heatmap_builder):
        """Mean correctness for 'billing' = (0.82 + 0.60) / 2 = 0.71."""
        result = heatmap_builder.build(self.RESULTS)
        expected = round((0.82 + 0.60) / 2, 3)
        assert result["scores"]["billing"]["correctness"] == pytest.approx(expected, abs=0.001)

    def test_security_context_recall_is_average(self, heatmap_builder):
        """Mean context_recall for 'security' = (0.8 + 0.6) / 2 = 0.70."""
        result = heatmap_builder.build(self.RESULTS)
        expected = round((0.8 + 0.6) / 2, 3)
        assert result["scores"]["security"]["context_recall"] == pytest.approx(expected, abs=0.001)

    def test_deltas_are_none_without_baseline(self, heatmap_builder):
        """When no baseline is provided every delta cell must be None."""
        result = heatmap_builder.build(self.RESULTS)
        for cat in result["categories"]:
            for metric, val in result["deltas"][cat].items():
                assert val is None, (
                    f"Expected None delta for {cat}/{metric} (no baseline), got {val}"
                )

    def test_deltas_computed_when_baseline_given(self, heatmap_builder):
        """With a baseline, deltas should be signed floats, not None."""
        # Use the same results as baseline — all deltas must be 0.
        result = heatmap_builder.build(self.RESULTS, baseline_results=self.RESULTS)
        for cat in result["categories"]:
            for metric, val in result["deltas"][cat].items():
                assert val == pytest.approx(0.0, abs=0.001), (
                    f"Expected zero delta for {cat}/{metric} (same-run baseline), got {val}"
                )

    def test_metrics_list_is_complete(self, heatmap_builder):
        result = heatmap_builder.build(self.RESULTS)
        assert set(result["metrics"]) == {
            "context_recall", "context_precision", "groundedness", "correctness"
        }


# ===========================================================================
# test_clusterer_min_questions
# ===========================================================================

class TestClustererMinQuestions:
    """With fewer than 3 questions the clusterer should bypass KMeans
    and assign each question to its own trivial cluster."""

    def test_single_question_gives_one_cluster(self, clusterer):
        qs = [_make_question("q1", "billing", "retriever")]
        clusters = clusterer.cluster(qs)
        assert len(clusters) == 1

    def test_two_questions_give_two_clusters(self, clusterer):
        qs = [
            _make_question("q1", "billing",  "retriever"),
            _make_question("q2", "security", "generator"),
        ]
        clusters = clusterer.cluster(qs)
        assert len(clusters) == 2

    def test_each_cluster_has_size_one(self, clusterer):
        qs = [
            _make_question("q1", "billing",  "retriever"),
            _make_question("q2", "security", "generator"),
        ]
        clusters = clusterer.cluster(qs)
        for c in clusters:
            assert c["size"] == 1, (
                f"Trivial cluster for '{c['cluster_label']}' should have size=1, got {c['size']}"
            )

    def test_each_cluster_contains_its_question_id(self, clusterer):
        qs = [
            _make_question("q1", "billing",  "retriever"),
            _make_question("q2", "security", "generator"),
        ]
        clusters = clusterer.cluster(qs)
        all_ids = {qid for c in clusters for qid in c["question_ids"]}
        assert all_ids == {"q1", "q2"}

    def test_cluster_label_derived_from_category(self, clusterer):
        qs = [_make_question("q1", "billing", "retriever")]
        clusters = clusterer.cluster(qs)
        assert clusters[0]["cluster_label"] == "Billing Questions"

    def test_dominant_failure_inherited(self, clusterer):
        qs = [_make_question("q1", "billing", "generator")]
        clusters = clusterer.cluster(qs)
        assert clusters[0]["dominant_failure"] == "generator"

    def test_empty_input_returns_empty_list(self, clusterer):
        clusters = clusterer.cluster([])
        assert clusters == []

    def test_no_kmeans_called_for_small_dataset(self, clusterer):
        """_embed and _kmeans must NOT be called when len(questions) < 3."""
        qs = [
            _make_question("q1", "billing",  "retriever"),
            _make_question("q2", "security", "generator"),
        ]
        with (
            patch.object(FailureClusterer, "_embed",  side_effect=AssertionError("_embed called")) as mock_embed,
            patch.object(FailureClusterer, "_kmeans", side_effect=AssertionError("_kmeans called")) as mock_kmeans,
        ):
            clusters = clusterer.cluster(qs)
            mock_embed.assert_not_called()
            mock_kmeans.assert_not_called()
        assert len(clusters) == 2
