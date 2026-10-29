"""Synchronous RAG/Ragas evaluation with explicit per-question accounting."""
from dataclasses import dataclass
import statistics

import httpx

from core.reports import Completion, DIRECTIONS, METRICS, metric_value

RAGAS_NAMES = {"correctness": "answer_correctness", "groundedness": "faithfulness",
               "context_recall": "context_recall", "context_precision": "context_precision"}
FLAT_NAMES = {"groundedness": "grounding"}


@dataclass
class EvaluationBatch:
    scores: list[dict]
    completion: Completion
    errors: list[dict]


class RAGEvaluator:
    def __init__(self, timeout=30., configuration=None):
        self._timeout = timeout
        self.configuration = configuration or {}

    def evaluate(self, questions, rag_endpoint):
        if not rag_endpoint:
            raise ValueError("Set rag_endpoint to the HTTP(S) RAG query URL in the evaluation request.")
        if not questions or len({q["id"] for q in questions}) != len(questions):
            raise ValueError("Question set must be nonempty with unique IDs")
        successful, outputs, errors = [], [], []
        attempted = 0
        with httpx.Client(timeout=self._timeout) as client:
            for q in questions:
                attempted += 1
                try:
                    output = self._call_rag_endpoint(client, rag_endpoint, q)
                    successful.append(q)
                    outputs.append(output)
                except Exception as exc:
                    errors.append({"question_id": q["id"], "stage": "rag_request", "error": str(exc)})
        scores = []
        if successful:
            try:
                dataset = self._build_ragas_dataset(successful, outputs)
                frame = self._run_ragas(dataset)
                if len(frame) != len(successful):
                    raise ValueError("Scorer returned a different number of rows than requested")
            except Exception as exc:
                errors.extend({"question_id": q["id"], "stage": "scoring", "error": str(exc)} for q in successful)
            else:
                for idx, (q, output) in enumerate(zip(successful, outputs)):
                    try:
                        scores.append(score_to_dict(q, output, frame.iloc[idx].to_dict()))
                    except Exception as exc:
                        errors.append({"question_id": q["id"], "stage": "scoring", "error": str(exc)})
        return EvaluationBatch(scores, Completion(expected=len(questions), attempted=attempted,
            successfully_scored=len(scores), failed=attempted-len(scores)), errors)

    @staticmethod
    def _call_rag_endpoint(client, rag_endpoint, question):
        response = client.post(rag_endpoint, json={"query": question["question"]})
        response.raise_for_status()
        payload = response.json()
        answer = payload["answer"]
        contexts = payload.get("retrieved_chunks", payload.get("contexts"))
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError("RAG response requires a nonempty answer string")
        if not isinstance(contexts, list) or any(not isinstance(c, str) for c in contexts):
            raise ValueError("RAG response requires retrieved_chunks (or contexts) as a list of strings")
        return {"answer": answer, "retrieved_chunks": contexts}

    @staticmethod
    def _build_ragas_dataset(questions, outputs):
        from datasets import Dataset
        return Dataset.from_dict({"question": [q["question"] for q in questions],
            "ground_truth": [q["ground_truth"] for q in questions],
            "answer": [r["answer"] for r in outputs], "contexts": [r["retrieved_chunks"] for r in outputs]})

    def _run_ragas(self, dataset):
        from core.configuration import provider_base_url
        from langchain_openai import ChatOpenAI, OpenAIEmbeddings
        from ragas import evaluate
        from ragas.metrics import answer_correctness, faithfulness, context_recall, context_precision
        config = self.configuration
        if not config.get("judge_model") or not config.get("embedding_model"):
            raise ValueError("Set EVAL_JUDGE_MODEL and EVAL_EMBEDDING_MODEL on the API and worker.")
        result = evaluate(dataset, metrics=[answer_correctness, faithfulness, context_recall, context_precision],
            llm=ChatOpenAI(model=config["judge_model"], temperature=0, base_url=provider_base_url()),
            embeddings=OpenAIEmbeddings(model=config["embedding_model"], base_url=provider_base_url()), raise_exceptions=True)
        return result.to_pandas()


def score_to_dict(q, rag_r, ragas_row):
    metrics = {FLAT_NAMES.get(m, m): metric_value(ragas_row.get(r), r) for m, r in RAGAS_NAMES.items()}
    return {"question_id": q["id"], "category": q["category"], "question": q["question"],
        "answer": rag_r["answer"], "contexts": rag_r["retrieved_chunks"], "ground_truth": q["ground_truth"],
        **metrics, "hallucination_risk": 1. - metrics["grounding"], "from_cache": False}


def aggregate_by_category(scores, category_thresholds=None):
    groups = {}
    for score in scores:
        groups.setdefault(score["category"], []).append(score)
    result = {}
    for cat, rows in groups.items():
        means = {m: statistics.mean(metric_value(s[FLAT_NAMES.get(m, m)], m) for s in rows) for m in METRICS}
        thresholds = (category_thresholds or {}).get(cat, {"correctness": .7, "grounding": .75})
        passed = thresholds_pass(means, thresholds)
        result[cat] = {"question_count": len(rows), "passed": passed,
                       **{"avg_" + FLAT_NAMES.get(m, m): v for m, v in means.items()}}
    return result


def thresholds_pass(metrics, thresholds):
    for name, threshold in thresholds.items():
        m = "groundedness" if name == "grounding" else name
        if m not in DIRECTIONS:
            raise ValueError(f"Unknown quality threshold metric: {name}")
        threshold = metric_value(threshold, name)
        if round((metric_value(metrics[m], m) - threshold) * DIRECTIONS[m], 12) < 0:
            return False
    return True


def category_snapshots(data):
    return {cat: {"question_count": row["question_count"],
        **{m: row["avg_" + FLAT_NAMES.get(m, m)] for m in METRICS}} for cat, row in data.items()}
