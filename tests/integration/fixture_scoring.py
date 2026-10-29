"""Synthetic contract scoring: first-passage copy, not semantic/LLM accuracy.

A target must copy its first retrieved passage in fixture mode. Copying another
retrieved passage lowers synthetic correctness by exactly .15 (HIGH boundary)
while grounding remains high. This detects real target-code changes in CI.
"""
import time
import pandas as pd


def score_fixture(dataset):
    rows = []
    for row in dataset:
        if not row["answer"] or not row["contexts"]:
            raise ValueError("Fixture requires nonempty RAG answer and retrieved context")
        # Make scoring progress observable in the short offline integration run.
        time.sleep(.02)
        rows.append({"answer_correctness": .99 if row["answer"] == row["contexts"][0] else .84,
                     "faithfulness": .99 if row["answer"] in row["contexts"] else .60,
                     "context_recall": .99, "context_precision": .99})
    return pd.DataFrame(rows)
