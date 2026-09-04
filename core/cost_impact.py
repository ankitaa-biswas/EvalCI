"""
core/cost_impact.py
────────────────────
Business cost-impact estimator for EvalCI regression reports.

Translates a raw regression fingerprint + failure clusters into a
business-level impact score by weighting failures according to the
business criticality of the affected question categories (e.g. billing
questions are more costly to get wrong than shipping questions).

Dependencies: Python standard library only.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Step 1 — Category business-criticality weights
# ─────────────────────────────────────────────────────────────────────────────

#: Maps question category labels to a business-impact weight in [0.0, 1.0].
#: Higher weight = more costly when answers in that category are wrong.
#: Weights are calibrated for a typical B2B SaaS product.
CATEGORY_WEIGHTS: dict[str, float] = {
    "billing":      1.0,
    "payments":     1.0,
    "security":     0.9,
    "refunds":      0.8,
    "pricing":      0.7,
    "account":      0.6,
    "login":        0.6,
    "technical":    0.5,
    "shipping":     0.4,
    "integrations": 0.4,
    "privacy":      0.3,
}

#: Fallback weight for any category not present in ``CATEGORY_WEIGHTS``.
_DEFAULT_WEIGHT: float = 0.5

# ─────────────────────────────────────────────────────────────────────────────
# Step 4 — Impact level thresholds
# ─────────────────────────────────────────────────────────────────────────────

#: Ordered threshold → label mapping (checked from highest to lowest).
_IMPACT_LEVELS: list[tuple[float, str]] = [
    (7.0, "critical"),
    (5.0, "high"),
    (3.0, "medium"),
    (0.0, "low"),
]

# ─────────────────────────────────────────────────────────────────────────────
# Step 5 — Recommendation templates
# ─────────────────────────────────────────────────────────────────────────────

#: Maps dominant_failure value → one-sentence investigation recommendation.
_RECOMMENDATIONS: dict[str, str] = {
    "retriever": (
        "Investigate the retrieval pipeline first: re-index ChromaDB, verify "
        "the embedding model version has not changed, and inspect the top-k "
        "chunks returned for the highest-impact failing questions."
    ),
    "generator": (
        "Investigate the LLM generator first: review recent prompt template "
        "changes, confirm the model version and temperature settings, and "
        "check that retrieved context is being correctly injected into the prompt."
    ),
    "prompt": (
        "Investigate prompt engineering first: diff the prompt template against "
        "the last known-good commit, check for accidental truncation of context "
        "or instruction changes that may have altered answer formatting."
    ),
    "kb": (
        "Investigate the knowledge base first: verify that the source documents "
        "have not been inadvertently modified or removed, and confirm the "
        "ChromaDB collection was re-indexed after any document updates."
    ),
}

_DEFAULT_RECOMMENDATION: str = (
    "Run a manual inspection of the per-question scores for the highest-severity "
    "categories to isolate the root cause before attempting a fix."
)


class CostImpactEstimator:
    """Estimate the business cost impact of a RAG regression.

    Combines a regression fingerprint (severity + dominant failure component)
    with failure cluster data (which categories are affected and how many
    questions failed) to produce a single ``total_impact_score`` and a
    prioritised recommendation.

    The impact score is additive across clusters, weighted by:
    - The raw regression ``severity`` (0–10).
    - The business criticality of the affected ``category`` (0–1).
    - The proportion of total failed questions in the cluster.

    Typical usage::

        estimator = CostImpactEstimator()
        impact    = estimator.estimate(fingerprint, clusters)
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def estimate(
        self,
        fingerprint: dict,
        clusters: list[dict],
    ) -> dict:
        """Estimate the business cost impact of a regression.

        Steps
        ─────
        1. Extract severity from *fingerprint*.
        2. Compute per-cluster impact scores using category weights and cluster
           proportions, then sum into ``total_impact_score``.
        3. Map ``total_impact_score`` to a human-readable ``impact_level``.
        4. Collect and sort affected categories by their business weight.
        5. Select a one-sentence recommendation based on ``dominant_failure``.

        Args:
            fingerprint: Dict with at least:
                - ``severity``        (float, 0–10) — from ``RegressionFingerprinter``.
                - ``dominant_failure``(str)         — "retriever" | "generator" | "prompt" | "kb".
                - ``confidence``      (str)         — "high" | "medium" | "low".
            clusters:    List of cluster dicts, each with:
                - ``cluster_label``   (str) — e.g. "Billing Questions".
                - ``size``            (int) — number of questions in cluster.
                - ``dominant_failure``(str) — most common failure type.

        Returns:
            Dict with keys:
            - ``total_impact_score``  (float)      — aggregated weighted impact, rounded to 2 dp.
            - ``impact_level``        (str)         — "critical" | "high" | "medium" | "low".
            - ``affected_categories`` (list[str])   — unique categories sorted by weight desc.
            - ``dominant_failure``    (str)         — forwarded from *fingerprint*.
            - ``recommendation``      (str)         — one-sentence investigation guide.
        """
        severity: float = float(fingerprint.get("severity", 0.0))
        dominant_failure: str = str(fingerprint.get("dominant_failure", "unknown")).lower()

        # ── Step 2: Per-cluster impact scores ─────────────────────────────────
        total_failed: int = sum(c.get("size", 0) for c in clusters)

        cluster_impacts = self._compute_cluster_impacts(
            clusters, severity, total_failed
        )

        # ── Step 3: Sum and map to impact level ───────────────────────────────
        total_impact_score = round(sum(cluster_impacts.values()), 2)
        impact_level = self._map_impact_level(total_impact_score)

        # ── Step 4: Collect affected categories sorted by weight ──────────────
        affected_categories = self._sorted_affected_categories(clusters)

        # ── Step 5: Select recommendation ─────────────────────────────────────
        recommendation = _RECOMMENDATIONS.get(dominant_failure, _DEFAULT_RECOMMENDATION)

        return {
            "total_impact_score":  total_impact_score,
            "impact_level":        impact_level,
            "affected_categories": affected_categories,
            "dominant_failure":    fingerprint.get("dominant_failure", "unknown"),
            "recommendation":      recommendation,
        }

    # ------------------------------------------------------------------
    # Step 2 — Per-cluster impact computation
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_category(cluster_label: str) -> str:
        """Derive a normalised category name from a cluster label.

        Cluster labels are of the form ``"<Category> Questions"`` (e.g.
        ``"Billing Questions"``).  This strips the " Questions" suffix and
        lowercases the result so it can be looked up in ``CATEGORY_WEIGHTS``.

        Args:
            cluster_label: The human-readable cluster label string.

        Returns:
            Lowercased category string, e.g. ``"billing"``.
        """
        return cluster_label.lower().removesuffix(" questions").strip()

    def _compute_cluster_impacts(
        self,
        clusters: list[dict],
        severity: float,
        total_failed: int,
    ) -> dict[int, float]:
        """Compute the impact score for each cluster.

        Formula::

            cluster_impact = severity × category_weight × (cluster_size / total_failed)

        If *total_failed* is 0 (no failed questions), all impacts are 0.

        Args:
            clusters:     List of cluster dicts.
            severity:     Raw severity score from the fingerprint (0–10).
            total_failed: Total number of failed questions across all clusters.

        Returns:
            Dict mapping cluster index → impact score (float).
        """
        if total_failed == 0:
            return {idx: 0.0 for idx, _ in enumerate(clusters)}

        impacts: dict[int, float] = {}
        for idx, cluster in enumerate(clusters):
            label = cluster.get("cluster_label", "")
            category = self._extract_category(label)
            weight = CATEGORY_WEIGHTS.get(category, _DEFAULT_WEIGHT)
            size = int(cluster.get("size", 0))
            proportion = size / total_failed
            impacts[idx] = severity * weight * proportion

        return impacts

    # ------------------------------------------------------------------
    # Step 3 — Impact level mapping
    # ------------------------------------------------------------------

    @staticmethod
    def _map_impact_level(score: float) -> str:
        """Map a numeric impact score to a human-readable severity label.

        Thresholds::

            score > 7.0  → "critical"
            score > 5.0  → "high"
            score > 3.0  → "medium"
            else         → "low"

        Args:
            score: The ``total_impact_score`` float.

        Returns:
            One of ``"critical"``, ``"high"``, ``"medium"``, ``"low"``.
        """
        for threshold, label in _IMPACT_LEVELS:
            if score > threshold:
                return label
        return "low"

    # ------------------------------------------------------------------
    # Step 4 — Affected category extraction
    # ------------------------------------------------------------------

    def _sorted_affected_categories(self, clusters: list[dict]) -> list[str]:
        """Return unique affected category names sorted by business weight, descending.

        Deduplication preserves the highest-weight occurrence of each category.

        Args:
            clusters: List of cluster dicts with ``cluster_label`` keys.

        Returns:
            List of unique category strings, highest business weight first.
        """
        seen: set[str] = set()
        categories_with_weight: list[tuple[str, float]] = []

        for cluster in clusters:
            label = cluster.get("cluster_label", "")
            category = self._extract_category(label)
            if category not in seen:
                seen.add(category)
                weight = CATEGORY_WEIGHTS.get(category, _DEFAULT_WEIGHT)
                categories_with_weight.append((category, weight))

        # Sort by weight descending, then alphabetically for stable ordering
        categories_with_weight.sort(key=lambda x: (-x[1], x[0]))
        return [cat for cat, _ in categories_with_weight]
