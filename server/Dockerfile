# syntax=docker/dockerfile:1
#
# FDPneo Index server image: uv build into a self-contained venv, copied into a
# slim non-root runtime. `fdpindex serve` applies migrations, then serves.

FROM python:3.15-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

RUN pip install --no-cache-dir uv
WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

FROM python:3.15-slim AS runtime

RUN groupadd --system fdpindex \
    && useradd --system --gid fdpindex --home-dir /app --no-create-home fdpindex
WORKDIR /app
COPY --from=builder --chown=fdpindex:fdpindex /app /app
# The SPARQL store (a cache derived from PostgreSQL); mount a volume here.
RUN mkdir -p /app/data/sparql && chown -R fdpindex:fdpindex /app/data

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

USER fdpindex
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=10 \
  CMD python -c "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3).status==200 else 1)"

CMD ["fdpindex", "serve", "--host", "0.0.0.0", "--port", "8000"]
