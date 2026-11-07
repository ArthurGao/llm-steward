# syntax=docker/dockerfile:1
FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.10.8 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# Dependencies first, so source edits do not invalidate this layer.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --extra native --no-install-project
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --extra native --no-editable

FROM python:3.12-slim
# A real home: libraries such as huggingface_hub write under $HOME and fail without one.
RUN useradd --system --create-home --uid 10001 steward && mkdir /data && chown steward /data
COPY --from=build /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1 \
    STEWARD_MODEL_CACHE_DIR=/data/models HF_HOME=/data/.cache/huggingface
USER steward
WORKDIR /data
EXPOSE 8600
HEALTHCHECK --interval=10s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8600/health', timeout=4)"]
CMD ["sh", "-c", "steward migrate && exec steward serve --host 0.0.0.0 --port 8600"]
