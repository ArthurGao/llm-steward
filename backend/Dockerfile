# syntax=docker/dockerfile:1
FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.10.8 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# git, for the engines pinned to a commit (lite-mem); the build stage only, the image has none.
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
# Dependencies first, so source edits do not invalidate this layer. --frozen installs the lock as is:
# --locked would re-check it, which needs the dev group's path source (../sdk/python), outside this
# build context. Development and CI run `uv sync --locked`, so the lock is checked there.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --extra native --extra mem0 --extra litemem --no-install-project
# The local embedding model (native and mem0), baked in: a first start needs no download from the
# Hugging Face Hub, which can stall for minutes. Must match Settings.embedding_model (a test pins it).
ARG EMBEDDING_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
RUN .venv/bin/python -c "import sys; from fastembed import TextEmbedding; TextEmbedding(sys.argv[1], cache_dir='/app/models')" "$EMBEDDING_MODEL"
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --extra native --extra mem0 --extra litemem --no-editable

FROM python:3.12-slim
# A real home: libraries such as huggingface_hub write under $HOME and fail without one.
RUN useradd --system --create-home --uid 10001 steward && mkdir /data && chown steward /data
COPY --from=build /app/.venv /app/.venv
COPY --from=build --chown=steward /app/models /app/models
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1 \
    STEWARD_MODEL_CACHE_DIR=/app/models HF_HOME=/data/.cache/huggingface \
    MEM0_TELEMETRY=false
USER steward
WORKDIR /data
EXPOSE 8600
HEALTHCHECK --interval=10s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8600/health', timeout=4)"]
CMD ["sh", "-c", "steward migrate && exec steward serve --host 0.0.0.0 --port 8600"]
