"""Deterministic scores to verify plumbing only; no semantic scoring claim."""
import time
import pandas as pd


def score_fixture(dataset):
    rows = []
    for row in dataset:
        if not row["answer"] or not row["contexts"]:
            raise ValueError("Fixture requires nonempty RAG answer and retrieved context")
        # Make scoring progress observable in the short offline integration run.
        time.sleep(.02)
        rows.append({"answer_correctness": .99, "faithfulness": .99,
                     "context_recall": .99, "context_precision": .99})
    return pd.DataFrame(rows)
