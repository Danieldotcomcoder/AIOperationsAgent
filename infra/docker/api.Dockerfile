# syntax=docker/dockerfile:1.7
# One image, several entrypoints (ADR 0006). Built from the repo root.
#
#   docker build -f infra/docker/api.Dockerfile --target runtime -t opspilot-api:<tag> .
#
# Installs strictly from apps/api/uv.lock. A lockfile the pinned uv cannot read is a
# build failure, never a silent re-resolve: the image must contain exactly what was
# tested. uv 0.8.24 (pinned by digest) reads the lockfile; verified 2026-10-03 with
# `uv export --frozen`. Bump it together with the uv that writes the lockfile.

ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.8@sha256:1d31be550ff927957472b2a491dc3de1ea9b5c2d319a9cea5b6a48021e2990a6

FROM ${UV_IMAGE} AS uv

FROM python:3.12-slim AS base
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/app/apps/api/.venv/bin:$PATH"
WORKDIR /app
# No system packages: psycopg[binary] bundles libpq, every dependency ships wheels,
# and health checks use Python. Smaller image, and no apt mirror in the build path.
COPY --from=uv /uv /usr/local/bin/uv

# --- dependency layer: changes only when the lockfile changes -----------------
FROM base AS deps
COPY apps/api/pyproject.toml apps/api/uv.lock /app/apps/api/
WORKDIR /app/apps/api
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# --- development ---------------------------------------------------------------
FROM deps AS dev
WORKDIR /app/apps/api
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --extra dev
COPY apps/api /app/apps/api
ENV PYTHONPATH=/app/apps/api/src
EXPOSE 8000
CMD ["uvicorn", "opspilot.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# --- production ----------------------------------------------------------------
FROM deps AS runtime
COPY apps/api /app/apps/api
WORKDIR /app/apps/api
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev
ENV PYTHONPATH=/app/apps/api/src
RUN useradd --system --uid 10001 --home /app opspilot && chown -R opspilot /app
USER opspilot
EXPOSE 8000
# Health checks live in compose, per role: workers and beat serve no HTTP.
# Workers and beat override this with their own celery command.
CMD ["uvicorn", "opspilot.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--proxy-headers", "--forwarded-allow-ips", "*"]
