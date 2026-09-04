"""
core/agent.py
─────────────
LLM-powered root-cause diagnostic agent for EvalCI regression reports.

Design principle — structured input only
─────────────────────────────────────────
``RootCauseAgent`` does **not** speculate.  It receives a fully-computed
set of quantitative signals — attribution weights, cluster sizes, per-category
metric deltas, and an optional git diff summary — and asks the LLM to explain
*that specific evidence*, not to guess at causes unsupported by the data.

This matters for production use: a hallucinated root-cause recommendation is
worse than no recommendation at all, because it misdirects engineer attention.
By enforcing structured, fact-bound inputs and an explicit system instruction
("analyse only the structured data provided"), the agent operates as an
*evidence explainer*, not a free-form oracle.

Public API
──────────
    agent = RootCauseAgent()
    report = agent.diagnose(
        fingerprint=...,       # output of RegressionFingerprinter.compute()
        clusters=...,          # output of FailureClusterer.cluster()
        heatmap=...,           # output of HeatmapBuilder.build()
        git_diff_summary="...",
    )
    # → dict with keys: summary, evidence, recommended_action, confidence,
    #                   fingerprint_used

Dependencies: anthropic, json, os  (stdlib + anthropic SDK).
"""

from __future__ import annotations

import json
import os

import anthropic


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MODEL: str = "claude-haiku-4-5"

_SYSTEM_PROMPT: str = (
    "You are a RAG diagnostic assistant. "
    "Analyze only the structured data provided. "
    "Do not speculate beyond the evidence. "
    "Be concise and technical."
)

_USER_PROMPT_TEMPLATE: str = """\
You have been given a structured regression report for a RAG (Retrieval-Augmented \
Generation) system. Diagnose the root cause using only the evidence below.

## Attribution Vector
{attribution_block}

## Dominant Failure
Component: {dominant_failure}
Severity (0–10): {severity}
Confidence: {confidence}

## Failure Clusters
{clusters_block}

## Top Regressing Categories (largest negative correctness deltas)
{top_categories_block}

## Git Diff Summary
{git_diff_summary}

---

Return ONLY a JSON object with exactly these keys — no markdown fences, no extra text:
{{
  "summary": "<2–3 sentence diagnosis grounded in the data above>",
  "evidence": ["<specific data point 1>", "<specific data point 2>", "..."],
  "recommended_action": "<one concrete, actionable fix>",
  "confidence": "<high | medium | low>"
}}

The "evidence" list must contain 3 to 5 items, each citing a specific number or \
observation from the report above.
"""

_FALLBACK: dict = {
    "summary": "Diagnosis unavailable — LLM response could not be parsed.",
    "evidence": [],
    "recommended_action": "Review raw metrics manually.",
    "confidence": "low",
}

# Number of worst-delta categories to surface in the prompt.
_TOP_N_CATEGORIES: int = 3


# ---------------------------------------------------------------------------
# Public class
# ---------------------------------------------------------------------------


class RootCauseAgent:
    """Explain EvalCI regression evidence using a structured LLM call.

    The agent converts quantitative regression signals into a human-readable
    diagnostic report by:

    1. Extracting the top-N most-regressed categories from the heatmap.
    2. Formatting cluster sizes and labels as a concise bullet list.
    3. Serialising the full attribution vector so the LLM has exact weights.
    4. Calling Claude Haiku with a tightly-constrained system + user prompt.
    5. Parsing the returned JSON — falling back gracefully if the response
       is malformed or the API call fails.

    The returned dict always contains ``fingerprint_used`` so callers can
    persist the diagnostic alongside the original fingerprint data.
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def diagnose(
        self,
        fingerprint: dict,
        clusters: list[dict],
        heatmap: dict,
        git_diff_summary: str,
    ) -> dict:
        """Run the full diagnostic pipeline and return a structured report.

        Args:
            fingerprint:      Output of ``RegressionFingerprinter.compute()``.
                              Expected keys: ``attribution`` (dict), ``dominant_failure``
                              (str), ``severity`` (float), and optionally ``confidence``
                              (str).
            clusters:         Output of ``FailureClusterer.cluster()``.
                              Each element has ``cluster_label`` (str) and ``size`` (int).
            heatmap:          Output of ``HeatmapBuilder.build()``.
                              Expected keys: ``categories`` (list[str]) and
                              ``deltas`` (dict[str, dict[str, float | None]]).
            git_diff_summary: A plain-text summary of the git diff for the
                              commit under evaluation.  Passed verbatim to the
                              LLM.  Pass an empty string if unavailable.

        Returns:
            Dict with keys:
            - ``summary``            (str)        — 2–3 sentence diagnosis.
            - ``evidence``           (list[str])  — 3–5 specific data points.
            - ``recommended_action`` (str)        — one concrete fix.
            - ``confidence``         (str)        — high / medium / low.
            - ``fingerprint_used``   (dict)       — the input fingerprint dict.

            On any failure (API error, parse error), a safe fallback dict is
            returned that still includes ``fingerprint_used``.
        """
        # ── Step 1: Build the structured prompt ───────────────────────────────
        user_prompt = self._build_user_prompt(
            fingerprint, clusters, heatmap, git_diff_summary
        )

        # ── Steps 4–6: Call LLM, parse, return ───────────────────────────────
        raw_response = self._call_llm(user_prompt)
        result = self._parse_response(raw_response, fingerprint)
        return result

    # ------------------------------------------------------------------
    # Step 1 — Prompt construction
    # ------------------------------------------------------------------

    @staticmethod
    def _build_attribution_block(fingerprint: dict) -> str:
        """Format the attribution weight vector as a readable bullet list.

        Args:
            fingerprint: The fingerprint dict from ``RegressionFingerprinter``.

        Returns:
            Multi-line string, one line per component with percentage weight.
        """
        attribution: dict = fingerprint.get("attribution", {})
        if not attribution:
            return "  (no attribution data)"
        lines = []
        for component, weight in attribution.items():
            pct = f"{weight * 100:.1f}%"
            lines.append(f"  {component}: {pct}")
        return "\n".join(lines)

    @staticmethod
    def _build_clusters_block(clusters: list[dict]) -> str:
        """Format cluster labels and sizes as a bullet list.

        Args:
            clusters: List of cluster dicts from ``FailureClusterer``.

        Returns:
            Multi-line string, one cluster per line.  Falls back to a
            placeholder if the list is empty.
        """
        if not clusters:
            return "  (no failure clusters detected)"
        lines = []
        for c in clusters:
            label = c.get("cluster_label", "Unknown Cluster")
            size = c.get("size", 0)
            failure = c.get("dominant_failure", "unknown")
            lines.append(f"  • {label} — {size} failure(s), dominant: {failure}")
        return "\n".join(lines)

    @staticmethod
    def _build_top_categories_block(heatmap: dict, top_n: int = _TOP_N_CATEGORIES) -> str:
        """Extract the categories with the largest mean negative correctness delta.

        Only categories where the mean delta across all available metrics is
        negative are included.  The block is sorted worst-first.

        Args:
            heatmap: Dict from ``HeatmapBuilder.build()``.
            top_n:   Maximum number of categories to include.

        Returns:
            Multi-line string.  Falls back to a placeholder if no regressed
            categories are found (e.g. no baseline was provided).
        """
        deltas: dict = heatmap.get("deltas", {})
        if not deltas:
            return "  (no delta data — no baseline run available)"

        # Compute mean delta across all non-None metrics per category.
        category_means: list[tuple[str, float]] = []
        for cat, metric_deltas in deltas.items():
            numeric_deltas = [
                v for v in metric_deltas.values() if v is not None
            ]
            if not numeric_deltas:
                continue
            mean_delta = sum(numeric_deltas) / len(numeric_deltas)
            category_means.append((cat, mean_delta))

        # Keep only regressed categories, sorted worst first.
        regressed = sorted(
            [(cat, d) for cat, d in category_means if d < 0],
            key=lambda x: x[1],
        )

        if not regressed:
            return "  (no regressed categories detected — all deltas are non-negative)"

        lines = []
        for cat, mean_delta in regressed[:top_n]:
            lines.append(f"  • {cat}: mean delta {mean_delta:+.3f}")
        return "\n".join(lines)

    def _build_user_prompt(
        self,
        fingerprint: dict,
        clusters: list[dict],
        heatmap: dict,
        git_diff_summary: str,
    ) -> str:
        """Assemble the full user-turn prompt from all input signals.

        Args:
            fingerprint:      The fingerprint dict.
            clusters:         The cluster list.
            heatmap:          The heatmap dict.
            git_diff_summary: Raw git diff summary string.

        Returns:
            Formatted user prompt string ready for the LLM API call.
        """
        attribution_block = self._build_attribution_block(fingerprint)
        clusters_block = self._build_clusters_block(clusters)
        top_categories_block = self._build_top_categories_block(heatmap)

        # Ensure the git diff block is never empty so the template renders cleanly.
        diff_text = git_diff_summary.strip() if git_diff_summary.strip() else "(no git diff provided)"

        return _USER_PROMPT_TEMPLATE.format(
            attribution_block=attribution_block,
            dominant_failure=fingerprint.get("dominant_failure", "unknown"),
            severity=fingerprint.get("severity", "N/A"),
            confidence=fingerprint.get("confidence", "unknown"),
            clusters_block=clusters_block,
            top_categories_block=top_categories_block,
            git_diff_summary=diff_text,
        )

    # ------------------------------------------------------------------
    # Step 4 — LLM call
    # ------------------------------------------------------------------

    @staticmethod
    def _call_llm(user_prompt: str) -> str:
        """Send the structured prompt to Claude Haiku and return the raw text.

        Reads ``ANTHROPIC_API_KEY`` from the environment.  Raises no exceptions
        to callers — any error is caught by ``_parse_response`` which returns
        the safe fallback dict.

        Args:
            user_prompt: The fully-rendered user-turn prompt string.

        Returns:
            The model's reply as a plain string, or an empty string on error.
        """
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        client = anthropic.Anthropic(api_key=api_key)

        message = client.messages.create(
            model=_MODEL,
            max_tokens=1024,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        # Extract text from the first content block.
        return message.content[0].text

    # ------------------------------------------------------------------
    # Step 5 — Response parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _strip_markdown_fences(text: str) -> str:
        """Remove markdown code fences from the LLM response if present.

        Claude sometimes wraps JSON in triple-backtick blocks even when
        instructed not to.  This strips the opening and closing fences and
        any language tag (e.g. ```json).

        Args:
            text: Raw LLM response text.

        Returns:
            The inner content with fences removed, or the original text
            unchanged if no fences are found.
        """
        stripped = text.strip()
        if stripped.startswith("```"):
            # Drop opening fence (possibly with a language tag on the same line)
            first_newline = stripped.find("\n")
            if first_newline != -1:
                stripped = stripped[first_newline + 1:]
            # Drop closing fence
            if stripped.rstrip().endswith("```"):
                stripped = stripped.rstrip()[:-3].rstrip()
        return stripped

    def _parse_response(self, raw_text: str, fingerprint: dict) -> dict:
        """Parse the LLM's JSON reply and attach ``fingerprint_used``.

        If the response cannot be parsed — for any reason — returns the
        module-level ``_FALLBACK`` dict extended with ``fingerprint_used``.

        Args:
            raw_text:    Raw string returned by ``_call_llm``.
            fingerprint: The original fingerprint dict to attach.

        Returns:
            Parsed result dict with ``fingerprint_used`` key, or the fallback
            dict with ``fingerprint_used`` key on any failure.
        """
        # ── Step 5: Parse ─────────────────────────────────────────────────────
        try:
            cleaned = self._strip_markdown_fences(raw_text)
            parsed: dict = json.loads(cleaned)

            # Validate that the required keys are present.
            required = {"summary", "evidence", "recommended_action", "confidence"}
            if not required.issubset(parsed.keys()):
                raise ValueError(
                    f"LLM response missing required keys. "
                    f"Got: {set(parsed.keys())}  Expected: {required}"
                )
        except Exception:
            # ── Step 5 fallback ───────────────────────────────────────────────
            result = dict(_FALLBACK)
            result["fingerprint_used"] = fingerprint
            return result

        # ── Step 6: Attach fingerprint_used and return ────────────────────────
        parsed["fingerprint_used"] = fingerprint
        return parsed
