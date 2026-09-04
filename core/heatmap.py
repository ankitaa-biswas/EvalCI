"""
core/heatmap.py
───────────────
Heatmap data builder for EvalCI's evaluation dashboard.

Aggregates per-question metric scores into a category × metric matrix suitable
for direct rendering as a colour heatmap.  Optionally computes signed deltas
against a baseline run to highlight regressions.

Dependencies: Python standard library only.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from statistics import mean
from typing import Optional

logger = logging.getLogger(__name__)

#: The four evaluation metrics exposed in every heatmap, in display order.
HEATMAP_METRICS: list[str] = [
    "context_recall",
    "context_precision",
    "groundedness",
    "correctness",
]


class HeatmapBuilder:
    """Build a category × metric score matrix from per-question evaluation results.

    The produced dict is structured for direct consumption by the dashboard's
    heatmap visualisation component: ``scores[category][metric]`` gives the
    average score for that cell, and ``deltas[category][metric]`` gives the
    change versus a baseline run (or ``None`` if no baseline was provided).

    Typical usage::

        builder = HeatmapBuilder()
        heatmap = builder.build(question_results, baseline_results=baseline)
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build(
        self,
        question_results: list[dict],
        baseline_results: Optional[list[dict]] = None,
    ) -> dict:
        """Aggregate question scores into a category × metric heatmap dict.

        Steps
        ─────
        1. Group *question_results* by ``category``.
        2. For each category × metric cell compute the arithmetic mean,
           rounded to 3 decimal places.
        3. If *baseline_results* is provided, repeat step 1–2 for the baseline
           and compute signed deltas (current − baseline, rounded to 3 dp).
           If no baseline is given, all delta values are ``None``.
        4. Return the assembled heatmap dict.

        Args:
            question_results: List of per-question result dicts.  Each must
                contain ``category`` (str) and float values for all four
                metric keys (``context_recall``, ``context_precision``,
                ``groundedness``, ``correctness``).
            baseline_results: Optional list of per-question result dicts for
                the baseline run, in the same format as *question_results*.
                When provided, ``deltas`` will contain signed differences
                (positive = current is worse).

        Returns:
            Dict with keys:
            - ``categories`` (list[str])                     — sorted category labels.
            - ``metrics``    (list[str])                     — fixed metric column order.
            - ``scores``     (dict[str, dict[str, float]])   — category → metric → mean.
            - ``deltas``     (dict[str, dict[str, float|None]]) — category → metric → delta or None.
        """
        # ── Step 1 & 2: Compute current scores ────────────────────────────────
        current_means = self._compute_means(question_results)

        # ── Step 3: Compute baseline means and deltas ─────────────────────────
        if baseline_results is not None:
            baseline_means = self._compute_means(baseline_results)
            deltas = self._compute_deltas(current_means, baseline_means)
        else:
            deltas = self._null_deltas(current_means)

        # ── Step 4: Assemble and return ───────────────────────────────────────
        categories = sorted(current_means.keys())

        return {
            "categories": categories,
            "metrics":    HEATMAP_METRICS,
            "scores":     {cat: current_means[cat] for cat in categories},
            "deltas":     {cat: deltas[cat] for cat in categories},
        }

    # ------------------------------------------------------------------
    # Step 1 & 2 — Mean computation
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_means(results: list[dict]) -> dict[str, dict[str, float]]:
        """Group results by category and compute per-metric arithmetic means.

        Missing metric keys default to ``0.0`` so a partially-populated result
        dict does not crash the aggregation.

        Args:
            results: List of question result dicts.

        Returns:
            Nested dict ``{category: {metric: mean_value}}``.
            Mean values are rounded to 3 decimal places.
        """
        # Accumulate raw values per category per metric
        groups: dict[str, dict[str, list[float]]] = defaultdict(
            lambda: defaultdict(list)
        )
        for r in results:
            cat = r.get("category", "unknown")
            for metric in HEATMAP_METRICS:
                groups[cat][metric].append(float(r.get(metric, 0.0)))

        # Compute means
        means: dict[str, dict[str, float]] = {}
        for cat, metric_values in groups.items():
            means[cat] = {
                metric: round(mean(values) if values else 0.0, 3)
                for metric, values in metric_values.items()
            }
        return means

    # ------------------------------------------------------------------
    # Step 3 — Delta computation
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_deltas(
        current_means: dict[str, dict[str, float]],
        baseline_means: dict[str, dict[str, float]],
    ) -> dict[str, dict[str, float]]:
        """Compute signed deltas (current − baseline) for all shared categories.

        Categories present in *current_means* but absent in *baseline_means*
        receive ``None`` deltas (no baseline to compare against).

        Args:
            current_means:  Output of ``_compute_means`` for the current run.
            baseline_means: Output of ``_compute_means`` for the baseline run.

        Returns:
            Nested dict ``{category: {metric: delta_or_None}}``.
            Delta values are rounded to 3 decimal places.
        """
        deltas: dict[str, dict[str, float | None]] = {}
        for cat, current_metrics in current_means.items():
            if cat not in baseline_means:
                # No baseline for this category — all deltas are None
                deltas[cat] = {metric: None for metric in HEATMAP_METRICS}
            else:
                baseline_metrics = baseline_means[cat]
                deltas[cat] = {
                    metric: round(
                        current_metrics.get(metric, 0.0)
                        - baseline_metrics.get(metric, 0.0),
                        3,
                    )
                    for metric in HEATMAP_METRICS
                }
        return deltas

    @staticmethod
    def _null_deltas(
        current_means: dict[str, dict[str, float]],
    ) -> dict[str, dict[str, None]]:
        """Return a deltas dict where every value is ``None`` (no baseline case).

        Args:
            current_means: The current run's means dict (used only for key shape).

        Returns:
            ``{category: {metric: None}}`` mirroring the shape of *current_means*.
        """
        return {
            cat: {metric: None for metric in HEATMAP_METRICS}
            for cat in current_means
        }
