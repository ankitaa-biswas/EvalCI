"""
core/judge.py
─────────────
Multi-LLM consensus judge for EvalCI evaluation runs.

This module provides ``MultiLLMJudge``, which calls three LLM providers
(OpenAI GPT-4o-mini, Anthropic Claude Haiku, Google Gemini Flash) **in
parallel** to grade a RAG answer, then aggregates their scores into a single
consensus verdict.

Why three judges?
    A single LLM judge can be biased or inconsistent.  Running three independent
    judges and measuring their agreement gives a calibrated *confidence* signal:
    high agreement means the score is reliable; low agreement flags borderline
    answers that warrant human review.

Environment variables required:
    - ``OPENAI_API_KEY``    — OpenAI API key
    - ``ANTHROPIC_API_KEY`` — Anthropic API key
    - ``GEMINI_API_KEY``    — Google Generative AI API key

Dependencies: ``openai``, ``anthropic``, ``google-generativeai``,
plus stdlib ``asyncio``, ``json``, ``os``, ``statistics``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import statistics
from typing import Any

import anthropic
import google.generativeai as genai
import openai

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Shared grading prompt
# ─────────────────────────────────────────────────────────────────────────────

#: Template sent to every judge.  Placeholders are filled at call time.
#: The LLM is instructed to return *only* a JSON object — no markdown fences,
#: no preamble — so that ``json.loads`` can parse the response directly.
GRADING_PROMPT_TEMPLATE: str = (
    "Given the question, context, and answer, rate the answer's correctness "
    "from 0.0 to 1.0.\n\n"
    "Question:\n{question}\n\n"
    "Context:\n{context}\n\n"
    "Answer:\n{answer}\n\n"
    "Ground truth:\n{ground_truth}\n\n"
    'Return only a JSON object: {{"score": float, "reasoning": string}}'
)


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────


def _build_prompt(
    question: str,
    answer: str,
    context: list[str],
    ground_truth: str,
) -> str:
    """Render the grading prompt with the provided inputs.

    Args:
        question:     The user's question.
        answer:       The RAG system's generated answer to grade.
        context:      List of retrieved passage strings used to generate the answer.
        ground_truth: The reference correct answer.

    Returns:
        Fully rendered prompt string ready to be sent to any LLM judge.
    """
    context_block = "\n---\n".join(context) if context else "(no context provided)"
    return GRADING_PROMPT_TEMPLATE.format(
        question=question,
        context=context_block,
        answer=answer,
        ground_truth=ground_truth,
    )


def _parse_judge_response(raw: str) -> tuple[float | None, str | None]:
    """Parse a judge's raw text response into a (score, reasoning) pair.

    The LLM is instructed to return only ``{"score": ..., "reasoning": ...}``.
    This function strips any accidental markdown fences before parsing so that
    minor formatting violations don't count as failures.

    Args:
        raw: The raw string returned by the LLM.

    Returns:
        ``(score, reasoning)`` if parsing succeeds, ``(None, None)`` otherwise.
    """
    # Strip markdown code fences (```json ... ``` or ``` ... ```)
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        # Remove opening fence (``` or ```json) and closing ```
        text = "\n".join(
            line for line in lines
            if not line.strip().startswith("```")
        ).strip()

    try:
        payload: dict[str, Any] = json.loads(text)
        score = float(payload["score"])
        # Clamp to [0.0, 1.0] in case the LLM goes out of range
        score = max(0.0, min(1.0, score))
        reasoning: str = str(payload.get("reasoning", ""))
        return score, reasoning
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        logger.warning("Judge response parse error: %s | raw=%r", exc, raw[:200])
        return None, None


def _compute_agreement(valid_scores: list[float]) -> float:
    """Compute an agreement coefficient from a list of valid judge scores.

    Formula::

        agreement = 1 - (std_dev / mean)   # clipped to [0.0, 1.0]

    This measures how tightly the judges agree relative to their average.
    A lower relative spread → higher agreement.

    Special cases:
    - Exactly 1 valid score → ``0.5`` (neither confident nor disagreeing).
    - Mean is 0.0 (all judges scored 0) → ``1.0`` (perfect agreement at zero).

    Args:
        valid_scores: Non-empty list of score floats from judges that succeeded.

    Returns:
        Agreement coefficient in ``[0.0, 1.0]``.
    """
    if len(valid_scores) == 1:
        return 0.5

    mean = statistics.mean(valid_scores)
    std_dev = statistics.stdev(valid_scores)  # sample std dev (N-1)

    if mean == 0.0:
        # All judges returned 0.0 — they perfectly agree on a zero score
        return 1.0

    raw = 1.0 - (std_dev / mean)
    return max(0.0, min(1.0, raw))


def _compute_confidence(agreement: float) -> str:
    """Map an agreement score to a human-readable confidence label.

    Thresholds::

        agreement > 0.8  → "high"
        agreement > 0.6  → "medium"
        else             → "low"

    Args:
        agreement: Agreement coefficient in ``[0.0, 1.0]``.

    Returns:
        One of ``"high"``, ``"medium"``, or ``"low"``.
    """
    if agreement > 0.8:
        return "high"
    if agreement > 0.6:
        return "medium"
    return "low"


# ─────────────────────────────────────────────────────────────────────────────
# Public class
# ─────────────────────────────────────────────────────────────────────────────


class MultiLLMJudge:
    """Consensus LLM judge that aggregates verdicts from three AI providers.

    All three providers are called **concurrently** via ``asyncio.gather`` to
    minimise wall-clock latency.  Individual failures are caught and recorded
    as ``None`` rather than crashing the entire judgment.

    Usage::

        judge = MultiLLMJudge()
        result = judge.judge(question, answer, context, ground_truth)

    The returned dict has the following structure::

        {
            "mean_score":        float,          # average of valid scores
            "agreement":         float,          # 0.0 – 1.0 consensus coefficient
            "confidence":        str,            # "high" | "medium" | "low"
            "individual_scores": {
                "gpt":    float | None,
                "claude": float | None,
                "gemini": float | None,
            },
            "reasonings": {
                "gpt":    str | None,
                "claude": str | None,
                "gemini": str | None,
            },
        }
    """

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    def __init__(self) -> None:
        """Initialise API clients from environment variables.

        Clients are created once at construction time so they can reuse
        connection pools across multiple ``judge()`` calls.

        Raises:
            KeyError: If any of ``OPENAI_API_KEY``, ``ANTHROPIC_API_KEY``, or
                ``GEMINI_API_KEY`` are missing from the environment.
        """
        self._openai_client = openai.AsyncOpenAI(
            api_key=os.environ["OPENAI_API_KEY"]
        )
        self._anthropic_client = anthropic.AsyncAnthropic(
            api_key=os.environ["ANTHROPIC_API_KEY"]
        )
        # Gemini uses a module-level configure call; store the key for reuse.
        self._gemini_api_key: str = os.environ["GEMINI_API_KEY"]
        genai.configure(api_key=self._gemini_api_key)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def judge(
        self,
        question: str,
        answer: str,
        context: list[str],
        ground_truth: str,
    ) -> dict:
        """Run all three LLM judges and return a consensus verdict.

        This is a **synchronous** entry point that internally runs an async
        event loop so callers (Celery workers, CLI scripts) do not need to
        manage ``asyncio`` themselves.

        Args:
            question:     The question the RAG system was asked.
            answer:       The RAG system's generated answer to evaluate.
            context:      List of retrieved text chunks that the RAG system used
                          when generating the answer.
            ground_truth: The reference correct answer for comparison.

        Returns:
            A result dict with keys ``mean_score``, ``agreement``,
            ``confidence``, ``individual_scores``, and ``reasonings``.
            See class docstring for the full schema.
        """
        return asyncio.run(
            self._async_judge(question, answer, context, ground_truth)
        )

    # ------------------------------------------------------------------
    # Async orchestration
    # ------------------------------------------------------------------

    async def _async_judge(
        self,
        question: str,
        answer: str,
        context: list[str],
        ground_truth: str,
    ) -> dict:
        """Async implementation: call all three judges concurrently then aggregate.

        Step 1 — Render the shared prompt.
        Step 2 — Fire all three judge coroutines concurrently with
                 ``asyncio.gather(..., return_exceptions=True)``.
        Step 3 — Parse each judge response (failures → None).
        Step 4 — Compute consensus statistics.
        Step 5 — Return the structured result dict.

        Args:
            question, answer, context, ground_truth: Forwarded from ``judge``.

        Returns:
            Same shape as described in the class docstring.
        """
        # ── Step 1: Build the shared grading prompt ────────────────────────────
        prompt = _build_prompt(question, answer, context, ground_truth)

        # ── Step 2: Call all three judges in parallel ──────────────────────────
        # ``return_exceptions=True`` ensures one failure does not cancel the
        # other two coroutines.
        gpt_raw, claude_raw, gemini_raw = await asyncio.gather(
            self._call_gpt(prompt),
            self._call_claude(prompt),
            self._call_gemini(prompt),
            return_exceptions=True,
        )

        # ── Step 3: Parse responses; exceptions and bad JSON → None ───────────
        gpt_score,    gpt_reasoning    = self._safe_parse(gpt_raw,    "gpt")
        claude_score, claude_reasoning = self._safe_parse(claude_raw, "claude")
        gemini_score, gemini_reasoning = self._safe_parse(gemini_raw, "gemini")

        individual_scores = {
            "gpt":    gpt_score,
            "claude": claude_score,
            "gemini": gemini_score,
        }
        reasonings = {
            "gpt":    gpt_reasoning,
            "claude": claude_reasoning,
            "gemini": gemini_reasoning,
        }

        # ── Step 4: Aggregate valid scores ─────────────────────────────────────
        valid_scores = [s for s in individual_scores.values() if s is not None]

        if not valid_scores:
            # All three judges failed — return a zero-confidence fallback.
            logger.error("All three LLM judges failed for question: %r", question[:80])
            return {
                "mean_score":        0.0,
                "agreement":         0.0,
                "confidence":        "low",
                "individual_scores": individual_scores,
                "reasonings":        reasonings,
            }

        mean_score = statistics.mean(valid_scores)
        agreement  = _compute_agreement(valid_scores)
        confidence = _compute_confidence(agreement)

        # ── Step 5: Return the consensus verdict ───────────────────────────────
        return {
            "mean_score":        round(mean_score, 4),
            "agreement":         round(agreement,  4),
            "confidence":        confidence,
            "individual_scores": individual_scores,
            "reasonings":        reasonings,
        }

    # ------------------------------------------------------------------
    # Individual judge coroutines
    # ------------------------------------------------------------------

    async def _call_gpt(self, prompt: str) -> str:
        """Call OpenAI GPT-4o-mini and return the raw response text.

        Args:
            prompt: The fully rendered grading prompt.

        Returns:
            Raw string content of the model's first response message.

        Raises:
            openai.OpenAIError: On API errors (propagated to ``asyncio.gather``).
        """
        response = await self._openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,  # deterministic grading
            max_tokens=256,
            response_format={"type": "json_object"},
        )
        return response.choices[0].message.content or ""

    async def _call_claude(self, prompt: str) -> str:
        """Call Anthropic Claude Haiku and return the raw response text.

        Args:
            prompt: The fully rendered grading prompt.

        Returns:
            Raw string content of the first text block in Claude's response.

        Raises:
            anthropic.APIError: On API errors (propagated to ``asyncio.gather``).
        """
        message = await self._anthropic_client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        # Extract text from the first content block
        for block in message.content:
            if hasattr(block, "text"):
                return block.text
        return ""

    async def _call_gemini(self, prompt: str) -> str:
        """Call Google Gemini Flash and return the raw response text.

        The google-generativeai SDK is synchronous; we wrap the call in
        ``asyncio.to_thread`` so it doesn't block the event loop while the
        other two judges are running concurrently.

        Args:
            prompt: The fully rendered grading prompt.

        Returns:
            Raw string text from Gemini's response.

        Raises:
            Exception: Any google-generativeai error (propagated to ``asyncio.gather``).
        """
        def _sync_call() -> str:
            model = genai.GenerativeModel(
                model_name="gemini-1.5-flash",
                generation_config=genai.types.GenerationConfig(
                    temperature=0.0,
                    max_output_tokens=256,
                    response_mime_type="application/json",
                ),
            )
            response = model.generate_content(prompt)
            return response.text or ""

        return await asyncio.to_thread(_sync_call)

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _safe_parse(
        raw: str | BaseException,
        judge_name: str,
    ) -> tuple[float | None, str | None]:
        """Parse a judge result, converting any failure to ``(None, None)``.

        This method handles three cases:
        1. *raw* is an ``Exception`` raised inside the coroutine (network error,
           API rate limit, etc.) — log it and return ``(None, None)``.
        2. *raw* is a valid JSON string — delegate to ``_parse_judge_response``.
        3. *raw* is invalid JSON — ``_parse_judge_response`` returns
           ``(None, None)`` and logs a warning.

        Args:
            raw:        The coroutine return value (str) or exception object.
            judge_name: Human-readable judge label for log messages.

        Returns:
            ``(score, reasoning)`` or ``(None, None)`` on any failure.
        """
        if isinstance(raw, BaseException):
            logger.error(
                "Judge '%s' raised an exception: %s: %s",
                judge_name,
                type(raw).__name__,
                raw,
            )
            return None, None

        return _parse_judge_response(raw)
