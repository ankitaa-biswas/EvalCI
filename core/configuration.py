"""Capture the dataset and actual evaluation settings before dispatch."""
from collections import Counter
from importlib.metadata import version
import json
import os
from pathlib import Path

from core.reports import METRICS, Provenance, digest, metric_value


def load_evaluation(question_set_path, commit_sha, rag_endpoint):
    if not rag_endpoint:
        raise ValueError("rag_endpoint is required: supply the HTTP(S) RAG query URL in POST /evaluate/.")
    path = Path(question_set_path)
    questions = json.loads(path.read_text())
    if not isinstance(questions, list) or not questions:
        raise ValueError("Question set must be a nonempty JSON list")
    for q in questions:
        if any(not isinstance(q.get(k), str) or not q[k].strip() for k in ("id", "category", "question", "ground_truth")):
            raise ValueError("Each question requires nonempty id, category, question and ground_truth strings")
    if len({q["id"] for q in questions}) != len(questions):
        raise ValueError("Duplicate question IDs")
    definitions = json.loads(path.with_name("categories.json").read_text())
    categories = definitions["categories"]
    if len({c["id"] for c in categories}) != len(categories):
        raise ValueError("Duplicate category definitions")
    actual = {c: sorted(q["id"] for q in questions if q["category"] == c) for c in {q["category"] for q in questions}}
    declared = {c["id"]: sorted(c["question_ids"]) for c in categories}
    if actual != declared:
        raise ValueError("Dataset category membership differs from categories.json")
    mode = os.getenv("EVAL_SCORING_MODE", "ragas")
    if mode not in {"ragas", "fixture"}:
        raise ValueError("EVAL_SCORING_MODE must be ragas or fixture")
    batch_size = int(os.getenv("EVAL_SCORING_BATCH_SIZE", "5"))
    timeout = float(os.getenv("RAG_REQUEST_TIMEOUT_SECONDS", "30"))
    if not 1 <= batch_size <= 50 or not 0 < timeout <= 120:
        raise ValueError("Scoring batch size must be 1..50 and request timeout must be >0 and <=120 seconds")
    judge = "fixture-not-a-model" if mode == "fixture" else os.getenv("EVAL_JUDGE_MODEL")
    embedding = "fixture-not-an-embedding" if mode == "fixture" else os.getenv("EVAL_EMBEDDING_MODEL")
    if not judge or not embedding:
        raise ValueError("Set EVAL_JUDGE_MODEL and EVAL_EMBEDDING_MODEL explicitly on both API and worker (OpenAI-compatible Ragas scoring).")
    # A provider URL may include credentials; persist only a digest of it.
    config = {"contract_version": 2, "scoring_mode": mode, "scoring_batch_size": batch_size,
        "scoring_timeout_seconds": 180, "scoring_max_retries": 2, "ragas_version": version("ragas"), "judge_model": judge,
        "embedding_model": embedding, "temperature": 0, "metrics": list(METRICS),
        "provider_base_url_sha256": digest(provider_base_url()),
        "scoring_dependencies": {name: version(name) for name in ("langchain-openai", "openai", "datasets")},
        "category_counts": dict(sorted(Counter(q["category"] for q in questions).items())),
        "category_thresholds": {c["id"]: c["pass_threshold"] for c in categories},
        "global_thresholds": definitions["global_pass_threshold"],
        "overall_correctness_threshold": metric_value(float(os.getenv("PASS_THRESHOLD", ".70")), "PASS_THRESHOLD"),
        "severity_thresholds": {"LOW": .02, "MEDIUM": .05, "HIGH": .15},
        "request_timeout_seconds": timeout}
    for thresholds in [config["global_thresholds"], *config["category_thresholds"].values()]:
        for name, value in thresholds.items():
            if name not in {*METRICS, "grounding"}:
                raise ValueError(f"Unknown quality metric: {name}")
            metric_value(value, name)
    provenance = Provenance(dataset_sha256=digest(questions), configuration_sha256=digest(config),
        configuration=config, commit_sha=commit_sha, rag_endpoint=rag_endpoint)
    return questions, provenance


def provider_base_url():
    return os.getenv("OPENAI_API_BASE") or os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1"
