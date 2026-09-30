# Python services (api / engine / worker) share one image; the compose command picks the process.
FROM python:3.13-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY apps/api/pyproject.toml apps/api/
COPY apps/engine/pyproject.toml apps/engine/
COPY apps/worker/pyproject.toml apps/worker/
COPY packages/py-core/pyproject.toml packages/py-core/
COPY packages/py-brokers/pyproject.toml packages/py-brokers/
COPY packages/py-marketdata/pyproject.toml packages/py-marketdata/
COPY packages/py-db/pyproject.toml packages/py-db/alembic.ini packages/py-db/
COPY apps/api/src apps/api/src
COPY apps/engine/src apps/engine/src
COPY apps/worker/src apps/worker/src
COPY packages/py-core/src packages/py-core/src
COPY packages/py-brokers/src packages/py-brokers/src
COPY packages/py-marketdata/src packages/py-marketdata/src
COPY packages/py-db/src packages/py-db/src
RUN uv sync --frozen --all-packages --no-dev
RUN useradd --system --uid 10001 app
USER app
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
CMD ["uvicorn", "ae_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
