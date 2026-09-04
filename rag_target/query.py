# evalci/rag_target/query.py
# Query interface for the RAG target system.

import os
from typing import Any

import httpx
import chromadb
from langchain_google_genai import ChatGoogleGenAI, GoogleGenerativeAIEmbeddings
from langchain.schema import HumanMessage, SystemMessage

from rag_target.ingest import get_chroma_client, COLLECTION_NAME

OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gemini-1.5-flash")
TOP_K = int(os.getenv("RAG_TOP_K", "5"))

_RAG_SYSTEM_PROMPT = (
    "You are a precise question-answering assistant. "
    "Answer the question using ONLY the information provided in the context below. "
    "If the answer is not contained in the context, say exactly: 'I don't know.' "
    "Do not add information from your own knowledge. Be concise and factual.\n\n"
    "Context:\n{context}"
)


async def query_rag(
    question: str,
    rag_endpoint: str | None = None,
    top_k: int = TOP_K,
) -> dict:
    """
    Run end-to-end RAG inference for a single question.
    Returns {"answer": str, "contexts": list[str]}.
    """
    if rag_endpoint:
        return await query_external_rag(question, rag_endpoint)

    client = get_chroma_client()
    contexts = await retrieve_contexts(question, client, top_k)
    answer = await generate_answer(question, contexts)
    return {"answer": answer, "contexts": contexts}


async def retrieve_contexts(
    question: str,
    client: chromadb.HttpClient,
    top_k: int = TOP_K,
    collection_name: str = COLLECTION_NAME,
) -> list[str]:
    """
    Embed the query and retrieve the top-k document chunks from ChromaDB.
    """
    embedder = GoogleGenerativeAIEmbeddings(model="models/text-embedding-004")
    query_embedding = embedder.embed_query(question)

    collection = client.get_or_create_collection(
        name=collection_name,
        metadata={"hnsw:space": "cosine"},
    )
    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=min(top_k, collection.count() or 1),
        include=["documents"],
    )
    documents = results.get("documents", [[]])[0]
    return [doc for doc in documents if doc]


async def generate_answer(question: str, contexts: list[str]) -> str:
    """
    Generate an answer grounded in the retrieved contexts using an LLM.
    """
    if not contexts:
        return "I don't know."

    context_text = "\n\n---\n\n".join(contexts)
    system_content = _RAG_SYSTEM_PROMPT.format(context=context_text)

    llm = ChatGoogleGenAI(model=OPENAI_MODEL, temperature=0.0)
    messages = [
        SystemMessage(content=system_content),
        HumanMessage(content=question),
    ]
    response = await llm.ainvoke(messages)
    return response.content.strip()


async def query_external_rag(question: str, rag_endpoint: str) -> dict:
    """
    Call an external RAG service endpoint.
    Expects POST with {"question": str} → {"answer": str, "contexts": list[str]}.
    """
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            str(rag_endpoint),
            json={"question": question},
        )
        response.raise_for_status()
        data = response.json()
        return {
            "answer": data.get("answer", ""),
            "contexts": data.get("contexts", []),
        }
