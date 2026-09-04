"""
core/fingerprinter.py
─────────────────────
THE CORE NOVEL ALGORITHM — Regression Fingerprint attribution engine.

This module provides a single class, ``RegressionFingerprinter``, whose
``compute`` method accepts two snapshot dicts (baseline and current) of RAG
evaluation metrics and returns a structured fingerprint that answers:

  1. *How much* did each metric change?       → ``deltas``
  2. *Which component* is most to blame?      → ``attribution`` + ``dominant_failure``
  3. *How bad* is the regression?             → ``severity`` (0 – 10 scale)

Dependencies: Python standard library only (``math``).  No third-party packages.
"""

from __future__ import annotations

import math

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

#: A metric delta must fall *below* this threshold (i.e. more negative than
#: −0.05) to be considered a meaningful drop rather than random noise.
_NOISE_THRESHOLD: float = -0.05

#: Human-readable labels for the four attribution buckets returned by
#: ``compute``.
_COMPONENTS = ("retriever", "generator", "prompt", "kb")


# ─────────────────────────────────────────────────────────────────────────────
# Helper — private
# ─────────────────────────────────────────────────────────────────────────────


def _dropped(delta: float) -> bool:
    """Return True when *delta* represents a meaningful regression.

    A metric is considered *dropped* if its change falls below the noise
    threshold (``< -0.05``), filtering out trivial fluctuations that should
    not trigger an attribution signal.

    Args:
        delta: Signed difference ``current_score − baseline_score``.

    Returns:
        ``True`` if the metric regressed beyond the noise floor.
    """
    return delta < _NOISE_THRESHOLD


def _normalize(weights: dict[str, float]) -> dict[str, float]:
    """Normalize a weight dict so all values sum to exactly 1.0.

    If every component weight is zero (no regression signal detected), every
    component receives an equal share of 0.25 so that ``dominant_failure`` can
    still return a well-defined value.

    Args:
        weights: Mapping of component name → raw attribution weight.

    Returns:
        New dict with the same keys and values scaled to sum to 1.0.
    """
    total = sum(weights.values())
    if total == 0.0:
        # No signal — distribute evenly; prevents division-by-zero.
        n = len(weights)
        return {k: 1.0 / n for k in weights}
    return {k: v / total for k, v in weights.items()}


# ─────────────────────────────────────────────────────────────────────────────
# Public class
# ─────────────────────────────────────────────────────────────────────────────


class RegressionFingerprinter:
    """Attribute RAG evaluation regressions to the component most likely at fault.

    The fingerprinter works in four conceptual stages:

    1. **Delta computation** — subtract baseline scores from current scores to
       obtain signed deltas for each metric.
    2. **Attribution scoring** — apply a rule-based heuristic to assign raw
       weights to four possible failure components: *retriever*, *generator*,
       *prompt*, and *knowledge-base*.
    3. **Normalization** — scale the four weights so they sum to 1.0, giving an
       interpretable probability-like breakdown.
    4. **Summary packaging** — derive ``dominant_failure`` (highest weight
       component) and ``severity`` (0–10 score based on correctness delta), then
       bundle everything into a single result dict.

    Usage::

        fp = RegressionFingerprinter()
        result = fp.compute(baseline, current)

    The returned dict has the following structure::

        {
            "deltas": {
                "context_recall":    float,   # current − baseline
                "context_precision": float,
                "groundedness":      float,
                "correctness":       float,
            },
            "attribution": {
                "retriever": float,  # normalized weight in [0, 1]
                "generator": float,
                "prompt":    float,
                "kb":        float,
            },
            "dominant_failure": str,   # component with highest weight
            "severity":         float, # abs(correctness_delta) × 10, clamped 0–10
        }
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compute(self, baseline: dict, current: dict) -> dict:
        """Run the full fingerprinting pipeline on one pair of metric snapshots.

        Args:
            baseline: Dict with keys ``context_recall``, ``context_precision``,
                ``groundedness``, and ``correctness``, each a float in [0, 1].
                Represents the *known-good* run to compare against.
            current:  Dict with the same four keys representing the run under
                evaluation.

        Returns:
            A dict with keys ``deltas``, ``attribution``, ``dominant_failure``,
            and ``severity``.  See class docstring for the full structure.
        """
        # ── Step 1: compute signed deltas ─────────────────────────────────────
        deltas = self._compute_deltas(baseline, current)

        # ── Step 2: assign raw attribution weights ────────────────────────────
        raw_weights = self._attribute(deltas)

        # ── Step 3: normalize weights to sum to 1.0 ───────────────────────────
        attribution = _normalize(raw_weights)

        # ── Step 4: derive summary fields and return ──────────────────────────
        dominant_failure = self._dominant(attribution)
        severity = self._severity(deltas["correctness"])

        return {
            "deltas": deltas,
            "attribution": attribution,
            "dominant_failure": dominant_failure,
            "severity": severity,
        }

    # ------------------------------------------------------------------
    # Stage 1 — Delta computation
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_deltas(baseline: dict, current: dict) -> dict[str, float]:
        """Compute signed per-metric deltas (current − baseline).

        A negative delta means the metric *worsened* in the current run.
        The four expected keys are ``context_recall``, ``context_precision``,
        ``groundedness``, and ``correctness``.  Missing keys default to 0.0 so
        the caller does not need to guarantee completeness.

        Args:
            baseline: Baseline metric snapshot.
            current:  Current metric snapshot.

        Returns:
            Dict mapping each metric name to its signed delta.
        """
        metrics = ("context_recall", "context_precision", "groundedness", "correctness")
        return {
            m: current.get(m, 0.0) - baseline.get(m, 0.0)
            for m in metrics
        }

    # ------------------------------------------------------------------
    # Stage 2 — Attribution scoring (the core heuristic)
    # ------------------------------------------------------------------

    @staticmethod
    def _attribute(deltas: dict[str, float]) -> dict[str, float]:
        """Apply the four-rule heuristic to produce raw component weights.

        Each rule examines the *pattern* of metric drops to infer which system
        component is most likely responsible.  Rules are additive — multiple
        rules can fire simultaneously, giving nuanced mixed attributions.

        Rule table
        ──────────
        ┌──────────────────────────────────────────┬────────────────────────────┐
        │ Condition                                │ Effect                     │
        ├──────────────────────────────────────────┼────────────────────────────┤
        │ recall dropped AND precision dropped     │ retriever_weight += 0.5    │
        │   → retriever is returning bad/fewer     │                            │
        │     relevant chunks                      │                            │
        ├──────────────────────────────────────────┼────────────────────────────┤
        │ groundedness dropped BUT recall/prec ok  │ generator_weight += 0.4    │
        │   → good context available but LLM       │                            │
        │     ignores or misuses it                │                            │
        ├──────────────────────────────────────────┼────────────────────────────┤
        │ correctness dropped, groundedness stable,│ prompt_weight += 0.3       │
        │ retrieval stable                         │                            │
        │   → likely a prompt template regression  │                            │
        ├──────────────────────────────────────────┼────────────────────────────┤
        │ correctness dropped, low-delta pattern   │ kb_weight += 0.2           │
        │ across all metrics (no single dominant   │                            │
        │ drop)                                    │                            │
        │   → stale / noisy knowledge-base content │                            │
        └──────────────────────────────────────────┴────────────────────────────┘

        Args:
            deltas: Output of ``_compute_deltas``.

        Returns:
            Dict of raw (un-normalized) weights for the four components.
        """
        # Unpack booleans for readability inside the rule conditions.
        recall_dropped    = _dropped(deltas["context_recall"])
        precision_dropped = _dropped(deltas["context_precision"])
        grounding_dropped = _dropped(deltas["groundedness"])
        correct_dropped   = _dropped(deltas["correctness"])

        retrieval_stable  = not recall_dropped and not precision_dropped
        grounding_stable  = not grounding_dropped

        # Initialise all four component weights to zero.
        weights: dict[str, float] = {c: 0.0 for c in _COMPONENTS}

        # ── Rule 1: Retriever ──────────────────────────────────────────────────
        # Both recall and precision fell → the retriever is fetching fewer
        # relevant chunks or has become noisier.
        if recall_dropped and precision_dropped:
            weights["retriever"] += 0.5

        # ── Rule 2: Generator ─────────────────────────────────────────────────
        # Groundedness (faithfulness to retrieved context) dropped but the
        # retrieval metrics are stable → the LLM is failing to use good context.
        if grounding_dropped and retrieval_stable:
            weights["generator"] += 0.4

        # ── Rule 3: Prompt ────────────────────────────────────────────────────
        # Correctness fell while groundedness and retrieval are both stable →
        # the answer format or reasoning changed, likely due to a prompt edit.
        if correct_dropped and grounding_stable and retrieval_stable:
            weights["prompt"] += 0.3

        # ── Rule 4: Knowledge-base ────────────────────────────────────────────
        # Correctness fell but no single metric dominates (all deltas are
        # individually small) → the knowledge-base content may be outdated or
        # inconsistent, causing a diffuse quality decline.
        all_deltas_small = all(
            abs(v) < abs(_NOISE_THRESHOLD)          # magnitude < 0.05
            for v in deltas.values()
        )
        if correct_dropped and all_deltas_small:
            weights["kb"] += 0.2

        return weights

    # ------------------------------------------------------------------
    # Stage 4 helpers — summary derivation
    # ------------------------------------------------------------------

    @staticmethod
    def _dominant(attribution: dict[str, float]) -> str:
        """Return the component name with the highest normalized weight.

        In the event of a tie, Python's ``max`` returns the first key
        encountered (insertion order), which follows the priority order
        retriever → generator → prompt → kb defined in ``_COMPONENTS``.

        Args:
            attribution: Normalized weight dict from ``_normalize``.

        Returns:
            The name of the dominant failing component.
        """
        return max(attribution, key=lambda k: attribution[k])

    @staticmethod
    def _severity(correctness_delta: float) -> float:
        """Translate the correctness delta into a 0–10 severity score.

        Formula::

            severity = min(abs(correctness_delta) × 10, 10.0)

        A delta of −0.10 → severity 1.0 (minor).
        A delta of −0.50 → severity 5.0 (moderate).
        A delta of −1.00 → severity 10.0 (catastrophic).

        The result is clamped to [0, 10] and rounded to two decimal places for
        clean display in dashboard UIs.

        Args:
            correctness_delta: The signed correctness delta from ``_compute_deltas``.

        Returns:
            Severity score as a float in [0.0, 10.0].
        """
        raw = abs(correctness_delta) * 10.0
        return round(min(raw, 10.0), 2)
