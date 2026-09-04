This directory contains sample documents used as the knowledge base for the EvalCI RAG evaluation target.

PURPOSE
-------
When EvalCI runs an evaluation, it queries the RAG system with questions from
tests/test_suite.json. The RAG system retrieves information from these sample
documents to generate answers, which are then scored by Ragas.

HOW TO USE
----------
1. Add .txt or .md files to this directory containing domain knowledge.
2. Run: python -m rag_target.ingest
   This will chunk, embed, and load the documents into ChromaDB.
3. Trigger an evaluation: POST /evaluate

DOCUMENT GUIDELINES
-------------------
- Cover all question categories in tests/categories.json
- Use clear, factual prose (avoid vague or ambiguous language)
- Documents do not need to be exhaustive — partial coverage tests
  the retriever's context_recall under realistic conditions
- Add new .txt files here to expand the knowledge base

FILES TO ADD (Cycle 2)
----------------------
- rag_concepts.txt         : Definitions and explanations of RAG terminology
- python_ecosystem.txt     : Python, Celery, FastAPI, SQLAlchemy documentation
- vector_databases.txt     : ChromaDB, Pinecone, Weaviate comparisons
- evalci_architecture.txt  : EvalCI system design documentation
- troubleshooting_guide.txt: Common RAG failure modes and diagnostics
