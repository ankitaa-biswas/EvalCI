# evalci/rag_target/ingest.py
# Load and index documents into ChromaDB for RAG evaluation.

import os
import uuid
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # Load GOOGLE_API_KEY and other vars from .env before anything else

import chromadb
from chromadb.config import Settings
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_google_genai import GoogleGenerativeAIEmbeddings

CHROMA_HOST = os.getenv("CHROMA_HOST", "localhost")
CHROMA_PORT = int(os.getenv("CHROMA_PORT", "8000"))
COLLECTION_NAME = os.getenv("CHROMA_COLLECTION", "evalci_docs")
SAMPLE_DOCS_DIR = os.path.join(os.path.dirname(__file__), "sample_docs")

CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "512"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "64"))


def get_chroma_client() -> chromadb.HttpClient:
    """Return a ChromaDB HTTP client connected to the configured host."""
    return chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)


def load_sample_documents(docs_dir: str = SAMPLE_DOCS_DIR) -> list[dict]:
    """
    Load all .txt and .md files from docs_dir.
    Returns list of dicts with id, text, source keys.
    """
    docs = []
    for path in Path(docs_dir).glob("**/*"):
        if path.suffix.lower() in (".txt", ".md") and path.name != "README.txt":
            text = path.read_text(encoding="utf-8").strip()
            if text:
                docs.append({
                    "id": str(path.relative_to(docs_dir)),
                    "text": text,
                    "source": path.name,
                })
    return docs


def chunk_documents(raw_docs: list[dict]) -> list[dict]:
    """
    Split raw documents into chunks using RecursiveCharacterTextSplitter.
    Each chunk inherits the source metadata and gets a unique id.
    """
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    chunks = []
    for doc in raw_docs:
        parts = splitter.split_text(doc["text"])
        for i, part in enumerate(parts):
            chunks.append({
                "id": f"{doc['id']}::chunk{i}",
                "text": part,
                "source": doc["source"],
            })
    return chunks


def embed_and_upsert(
    client: chromadb.HttpClient,
    chunks: list[dict],
    collection_name: str = COLLECTION_NAME,
) -> int:
    """
    Embed chunks via OpenAI and upsert into ChromaDB.
    Returns number of chunks upserted.
    """
    embedder = GoogleGenerativeAIEmbeddings(model="models/gemini-embedding-001")
    collection = client.get_or_create_collection(
        name=collection_name,
        metadata={"hnsw:space": "cosine"},
    )

    # Process in batches of 100 to respect API limits
    batch_size = 100
    total = 0
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i : i + batch_size]
        texts = [c["text"] for c in batch]
        ids = [c["id"] for c in batch]
        metadatas = [{"source": c["source"]} for c in batch]
        embeddings = embedder.embed_documents(texts)
        collection.upsert(
            ids=ids,
            embeddings=embeddings,
            documents=texts,
            metadatas=metadatas,
        )
        total += len(batch)
    return total


def run_ingestion(reset_collection: bool = False) -> None:
    """
    Full pipeline: load → chunk → embed → upsert.
    Entry point for: python -m rag_target.ingest
    """
    client = get_chroma_client()

    if reset_collection:
        try:
            client.delete_collection(COLLECTION_NAME)
            print(f"Deleted existing collection '{COLLECTION_NAME}'")
        except Exception:
            pass

    print(f"Loading documents from {SAMPLE_DOCS_DIR} ...")
    raw_docs = load_sample_documents()
    print(f"  Loaded {len(raw_docs)} files")

    print("Chunking ...")
    chunks = chunk_documents(raw_docs)
    print(f"  Created {len(chunks)} chunks")

    print("Embedding and upserting into ChromaDB ...")
    count = embed_and_upsert(client, chunks)
    print(f"  Upserted {count} chunks → collection '{COLLECTION_NAME}'")
    print("Ingestion complete.")


if __name__ == "__main__":
    import sys
    reset = "--reset" in sys.argv
    run_ingestion(reset_collection=reset)
