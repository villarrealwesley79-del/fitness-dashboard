# Dockerfile — Fitness Dashboard SaaS
# Multi-stage build: keeps image lean (~150MB)

FROM python:3.11-slim AS base

WORKDIR /app

# Install system deps (SQLite headers for potential future use)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code; secret exclusion relies on .dockerignore.
COPY . .

# ── Runtime config ──────────────────────────────────────────
# Fly.io injects PORT env var; default 8080
# SECRET_KEY must be injected at runtime via env/secret manager, never baked in.
ENV PORT=8080 \
    FLASK_DEBUG=0 \
    DATA_DIR=/data

# Expose for Fly.io health checks and HTTP service
EXPOSE 8080

# Use gunicorn for production (already in requirements.txt)
# --workers 1 avoids divergent per-process globals (FIT-256). This masks
# rather than eliminates the underlying TOCTOU race: `save_current_workout_plan`
# still does a blind INSERT ... ON CONFLICT DO UPDATE with no version/optimistic
# lock, so a future move back to >1 worker (or a multi-instance/Fly.io
# multi-machine deploy) would reopen the same cross-process clobber risk.
# --threads 1 is a no-op here: gunicorn's default sync worker class already
# serves one request per worker with no thread concurrency to disable; it
# only matters if the worker class is changed to `gthread`. Kept for
# explicitness, not because it changes behavior.
# --timeout 120 allows slow Oura sync calls
CMD ["sh", "-c", "gunicorn app:app \
     --bind 0.0.0.0:${PORT} \
     --workers 1 \
     --threads 1 \
     --timeout 120 \
     --access-logfile - \
     --access-logformat '%(h)s %(l)s %(u)s %(t)s \"%(m)s %(U)s %(H)s\" %(s)s %(b)s \"%(f)s\" \"%(a)s\"' \
     --error-logfile -"]
