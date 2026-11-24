# Trove web app / worker image

# --- 1. Build the React UI (frontend/ -> trove_agent/web) ---
FROM node:22.11.0-alpine AS ui
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build && npm test

# --- 2. Python runtime ---
FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TROVE_MODEL_DIR=/app/.models \
    TROVE_VAULT_DIR=/data/vault

# pg_dump for backups; no compilers needed (binary wheels only)
RUN apt-get update \
 && apt-get install -y --no-install-recommends postgresql-client ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 10001 trove \
 && mkdir -p /app /data/vault /data/import \
 && chown -R trove:trove /app /data

WORKDIR /app
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY --chown=trove:trove trove_agent ./trove_agent
COPY --from=ui --chown=trove:trove /build/trove_agent/web ./trove_agent/web
USER trove

# Bake the local encoder + RAG index into the image (no download at runtime)
RUN TROVE_ENCODER=fastembed python -m trove_agent --db /tmp/build.db warm-models && rm -f /tmp/build.db*

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=4).status==200 else 1)"

CMD ["python", "-m", "trove_agent", "serve"]
