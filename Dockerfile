# ---- Frontend build stage ----
FROM node:20-alpine AS frontend-build
WORKDIR /frontend

COPY frontend/package*.json ./
RUN npm ci --no-audit --no-fund

COPY frontend/ .

# Same-origin deploy: the API is served from this same container, so leave the
# base URL empty and let axios make relative requests instead of an absolute one.
ARG VITE_API_BASE_URL=""
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL

RUN npm run build

# ---- Backend stage ----
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# libpq is required by psycopg[binary] at runtime on slim images
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY alembic.ini ./alembic.ini
COPY --from=frontend-build /frontend/dist ./frontend_dist

RUN useradd --create-home appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# /healthz answers without touching the database (so probing it never wakes a
# suspended Neon compute). start-period covers first boot: a cold DB wake plus
# running Alembic migrations before the app accepts requests.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '8000') + '/healthz', timeout=4)"]

# `exec` makes gunicorn the container's main process so `docker stop` delivers
# SIGTERM to it (graceful shutdown) instead of to a wrapper shell.
#  - default 2 workers: a good fit for 1 vCPU / 2 GB; override with WEB_CONCURRENCY
#  - --max-requests(+jitter) recycles workers periodically so slow memory growth
#    can't accumulate on a small instance
#  - --worker-tmp-dir /dev/shm keeps gunicorn's heartbeat file in RAM (the
#    standard fix for spurious worker timeouts on container filesystems)
#  - logs go to stdout/stderr so `docker compose logs` shows requests and errors
CMD ["sh", "-c", "exec gunicorn app.main:app -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:${PORT:-8000} --workers ${WEB_CONCURRENCY:-2} --timeout 120 --graceful-timeout 30 --keep-alive 5 --max-requests 1000 --max-requests-jitter 100 --worker-tmp-dir /dev/shm --access-logfile - --error-logfile -"]
