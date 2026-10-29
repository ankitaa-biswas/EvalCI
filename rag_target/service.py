"""Container RAG service with corpus-versioned Chroma collections."""
import hashlib
import math
import os
import re
from pathlib import Path


class FixtureEmbeddings:
    """Local feature hashing, only for deterministic transport verification."""
    def embed_query(self, text):
        vector = [0.] * 128
        for token in re.findall(r"\w+", text.lower()):
            index = int.from_bytes(hashlib.sha256(token.encode()).digest()[:4], "big") % len(vector)
            vector[index] += 1
        norm = math.sqrt(sum(v*v for v in vector)) or 1
        return [v / norm for v in vector]

    def embed_documents(self, texts):
        return [self.embed_query(t) for t in texts]


class RAGService:
    def __init__(self):
        self.mode = os.getenv("RAG_MODE", "live")
        if self.mode not in {"live", "fixture"}:
            raise ValueError("RAG_MODE must be live or fixture")
        if self.mode == "live" and not all(os.getenv(k) for k in ("GOOGLE_API_KEY", "GEMINI_GENERATION_MODEL", "GEMINI_EMBEDDING_MODEL")):
            raise ValueError("Live target requires GOOGLE_API_KEY, GEMINI_GENERATION_MODEL and GEMINI_EMBEDDING_MODEL")
        self.top_k = int(os.getenv("RAG_TOP_K", "5"))
        if not 1 <= self.top_k <= 20:
            raise ValueError("RAG_TOP_K must be 1..20")
        self.collection = None
        self.client = None
        self.embedder = None

    def initialize(self):
        from rag_target.ingest import get_chroma_client, load_sample_documents, chunk_documents
        from core.reports import digest
        self.client = get_chroma_client()
        self.client.heartbeat()
        raw = load_sample_documents(os.getenv("RAG_DOCS_DIR", str(Path(__file__).parent / "sample_docs")))
        chunks = chunk_documents(raw)
        if not chunks:
            raise ValueError("RAG documents directory contains no usable documents")
        identity = digest({"documents": raw, "chunks": chunks, "mode": self.mode,
                           "embedding_model": os.getenv("GEMINI_EMBEDDING_MODEL") if self.mode == "live" else "fixture-hash-128-v1"})[:16]
        name = os.getenv("CHROMA_COLLECTION", "evalci_docs") + "_" + identity
        self.collection = self.client.get_or_create_collection(name=name, metadata={"hnsw:space": "cosine"}, embedding_function=None)
        if self.mode == "fixture":
            self.embedder = FixtureEmbeddings()
        else:
            from langchain_google_genai import GoogleGenerativeAIEmbeddings
            model = os.environ["GEMINI_EMBEDDING_MODEL"]
            self.embedder = GoogleGenerativeAIEmbeddings(model=model if model.startswith("models/") else "models/" + model)
        # Corpus/model/chunk changes select a new collection; existing indexes
        # are preserved. Stable restarts do not make paid embedding calls.
        if self.collection.count() != len(chunks):
            for offset in range(0, len(chunks), 100):
                batch = chunks[offset:offset+100]
                texts = [c["text"] for c in batch]
                self.collection.upsert(ids=[c["id"] for c in batch], documents=texts,
                    embeddings=self.embedder.embed_documents(texts), metadatas=[{"source": c["source"]} for c in batch])

    def retrieve(self, query):
        embedding = self.embedder.embed_query(query)
        result = self.collection.query(query_embeddings=[embedding], n_results=min(self.top_k, self.collection.count()), include=["documents"])
        return [doc for doc in result["documents"][0] if doc]

    async def query(self, query):
        import asyncio
        contexts = await asyncio.to_thread(self.retrieve, query)
        if self.mode == "fixture":
            await asyncio.sleep(.02)
            answer = contexts[0] if contexts else "I don't know."
        else:
            from rag_target.query import generate_answer
            answer = await generate_answer(query, contexts)
        return {"answer": answer, "retrieved_chunks": contexts, "mode": self.mode}

    def ready(self):
        self.client.heartbeat()
        return self.collection.count() > 0
