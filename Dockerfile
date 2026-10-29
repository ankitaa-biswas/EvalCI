FROM python:3.11.14-slim-bookworm@sha256:65a93d69fa75478d554f4ad27c85c1e69fa184956261b4301ebaf6dbb0a3543d
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    ANONYMIZED_TELEMETRY=FALSE HOME=/home/evalci
COPY --from=ghcr.io/astral-sh/uv:0.13.6@sha256:af22a52f5eafef3d78e551599d405c02df61919d18028f0cea8eda93bc9986d0 /uv /bin/uv
WORKDIR /app
# Chroma's hnswlib dependency may need to compile for the selected architecture.
RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.lock ./
RUN --mount=type=cache,target=/uv-cache uv pip install --system --cache-dir /uv-cache --require-hashes --only-binary pyarrow -v -r requirements.lock \
    && uv pip check --system \
    && python -c "from datasets import Dataset; from ragas import evaluate; Dataset.from_dict({'question': ['dependency smoke check']})"
RUN useradd --create-home --uid 10001 evalci && mkdir -p /app/logs && chown -R evalci:evalci /home/evalci /app/logs
COPY --chown=evalci:evalci api ./api
COPY --chown=evalci:evalci core ./core
COPY --chown=evalci:evalci db ./db
ARG RAG_SOURCE_DIR=rag_target
COPY --chown=evalci:evalci ${RAG_SOURCE_DIR} ./rag_target
COPY --chown=evalci:evalci dashboard ./dashboard
COPY --chown=evalci:evalci tests ./tests
COPY --chown=evalci:evalci scripts ./scripts
COPY --chown=evalci:evalci alembic.ini ./
ENV PYTHONPATH=/app
RUN python -c "import core.judge, core.agent, rag_target.query"
ARG REVISION_SHA=unversioned
ARG HARNESS_REVISION_SHA=
ARG TARGET_SOURCE_SHA256=
RUN python -m scripts.image_identity --revision "$REVISION_SHA" --harness-revision "$HARNESS_REVISION_SHA" --expected-target-sha "$TARGET_SOURCE_SHA256" --write /app/build-info.json
LABEL org.opencontainers.image.revision=$REVISION_SHA
USER evalci
EXPOSE 8080
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8080"]
