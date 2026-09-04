"""
core/clusterer.py
─────────────────
Semantic failure clustering for EvalCI.

Groups failed questions into meaningful clusters using sentence embeddings
(``all-MiniLM-L6-v2``) and KMeans so the dashboard can present failure patterns
by *topic* rather than just by raw category.

Dependencies: ``sentence-transformers``, ``scikit-learn``.
"""

from __future__ import annotations

import logging
from collections import Counter

logger = logging.getLogger(__name__)

#: Maximum number of KMeans clusters produced, regardless of dataset size.
_MAX_CLUSTERS: int = 8

#: Minimum number of failed questions before semantic clustering is attempted.
#: Below this threshold every question becomes its own single-item cluster.
_MIN_FOR_CLUSTERING: int = 3


class FailureClusterer:
    """Cluster failed evaluation questions by semantic similarity.

    Uses ``sentence-transformers`` to embed question text and ``scikit-learn``
    KMeans to group semantically similar questions together.  Each resulting
    cluster is labelled with the most common question category it contains.

    Typical usage::

        clusterer = FailureClusterer()
        clusters  = clusterer.cluster(failed_questions)
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def cluster(self, failed_questions: list[dict]) -> list[dict]:
        """Cluster failed questions into semantically coherent groups.

        Steps
        ─────
        1. If fewer than 3 questions, bypass clustering and return one cluster
           per question, labelled with its own category.
        2. Embed all question strings with ``all-MiniLM-L6-v2``.
        3. Determine the number of KMeans clusters:
           ``k = max(2, len(questions) // 5)``, capped at 8.
        4. Run KMeans and assign each question to a cluster.
        5. Label each cluster with the most frequent category in it.
        6. Return a list of cluster dicts ordered by cluster ID.

        Args:
            failed_questions: List of dicts, each with:
                - ``question_id``     (str) — unique question identifier.
                - ``question``        (str) — the question text to embed.
                - ``category``        (str) — semantic category label.
                - ``dominant_failure``(str) — retriever / generator / prompt / kb.

        Returns:
            List of cluster dicts, each with:
            - ``cluster_id``      (int)       — zero-based cluster index.
            - ``cluster_label``   (str)       — most common category + " Questions".
            - ``question_ids``    (list[str]) — IDs of questions in this cluster.
            - ``dominant_failure``(str)       — most common failure type in cluster.
            - ``size``            (int)       — number of questions in cluster.
        """
        if not failed_questions:
            logger.debug("cluster: received empty question list — returning [].")
            return []

        # ── Step 1: Small-dataset fast path ───────────────────────────────────
        if len(failed_questions) < _MIN_FOR_CLUSTERING:
            logger.debug(
                "cluster: only %d questions — returning trivial per-question clusters.",
                len(failed_questions),
            )
            return self._trivial_clusters(failed_questions)

        # ── Step 2: Embed question strings ────────────────────────────────────
        embeddings = self._embed([q["question"] for q in failed_questions])

        # ── Step 3: Determine k and run KMeans ────────────────────────────────
        k = min(_MAX_CLUSTERS, max(2, len(failed_questions) // 5))
        labels = self._kmeans(embeddings, k)

        # ── Steps 4 & 5: Build cluster dicts ──────────────────────────────────
        return self._build_clusters(failed_questions, labels)

    # ------------------------------------------------------------------
    # Step 1 — Trivial clusters (small dataset)
    # ------------------------------------------------------------------

    @staticmethod
    def _trivial_clusters(questions: list[dict]) -> list[dict]:
        """Return one cluster per question when the list is too small for KMeans.

        Each cluster inherits the category and dominant_failure of its single
        question, so downstream code can treat them identically to real clusters.

        Args:
            questions: The (small) list of failed question dicts.

        Returns:
            One-item cluster list per question.
        """
        return [
            {
                "cluster_id":       idx,
                "cluster_label":    f"{q['category'].title()} Questions",
                "question_ids":     [q["question_id"]],
                "dominant_failure": q["dominant_failure"],
                "size":             1,
            }
            for idx, q in enumerate(questions)
        ]

    # ------------------------------------------------------------------
    # Step 2 — Sentence embedding
    # ------------------------------------------------------------------

    @staticmethod
    def _embed(texts: list[str]):
        """Encode a list of strings into dense sentence embeddings.

        Uses the ``all-MiniLM-L6-v2`` model which produces 384-dimensional
        embeddings in a fraction of a second on CPU for small datasets (< 200
        sentences).

        Args:
            texts: List of question strings to embed.

        Returns:
            NumPy array of shape ``(len(texts), 384)``.
        """
        from sentence_transformers import SentenceTransformer  # lazy import

        model = SentenceTransformer("all-MiniLM-L6-v2")
        return model.encode(texts, show_progress_bar=False)

    # ------------------------------------------------------------------
    # Step 3 — KMeans clustering
    # ------------------------------------------------------------------

    @staticmethod
    def _kmeans(embeddings, k: int) -> list[int]:
        """Run KMeans on the embeddings and return per-sample cluster labels.

        Args:
            embeddings: NumPy array of shape ``(n_samples, embedding_dim)``.
            k:          Number of clusters.

        Returns:
            List of integer labels, one per sample, in the same order as input.
        """
        from sklearn.cluster import KMeans  # lazy import

        km = KMeans(n_clusters=k, random_state=42, n_init="auto")
        km.fit(embeddings)
        return km.labels_.tolist()

    # ------------------------------------------------------------------
    # Steps 4 & 5 — Build cluster dicts
    # ------------------------------------------------------------------

    @staticmethod
    def _build_clusters(questions: list[dict], labels: list[int]) -> list[dict]:
        """Aggregate questions by cluster label and build the output dicts.

        For each cluster:
        - ``cluster_label`` is the most common category + " Questions".
        - ``dominant_failure`` is the most common failure attribution.
        - ``size`` is the count of questions in the cluster.

        Clusters with zero members (possible if KMeans k > actual distinct
        groups) are omitted from the output.

        Args:
            questions: Full list of failed question dicts.
            labels:    Per-question integer cluster assignments from KMeans.

        Returns:
            List of cluster dicts sorted by cluster_id ascending.
        """
        # Group question dicts by cluster label
        cluster_buckets: dict[int, list[dict]] = {}
        for q, label in zip(questions, labels):
            cluster_buckets.setdefault(label, []).append(q)

        clusters: list[dict] = []
        for cluster_id, members in sorted(cluster_buckets.items()):
            category_counts = Counter(m["category"] for m in members)
            failure_counts  = Counter(m["dominant_failure"] for m in members)

            most_common_category = category_counts.most_common(1)[0][0]
            most_common_failure  = failure_counts.most_common(1)[0][0]

            clusters.append(
                {
                    "cluster_id":       cluster_id,
                    "cluster_label":    f"{most_common_category.title()} Questions",
                    "question_ids":     [m["question_id"] for m in members],
                    "dominant_failure": most_common_failure,
                    "size":             len(members),
                }
            )

        return clusters
