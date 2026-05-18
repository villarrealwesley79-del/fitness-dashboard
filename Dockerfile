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

# Copy application code
COPY . .

# ── Runtime config ──────────────────────────────────────────
# Fly.io injects PORT env var; default 8080
ENV PORT=8080 \
    FLASK_DEBUG=0 \
    DATA_DIR=/data

# Expose for Fly.io health checks and HTTP service
EXPOSE 8080

# Use gunicorn for production (already in requirements.txt)
# --workers 2 keeps memory under 256MB free tier limit
# --timeout 120 allows slow Oura sync calls
CMD ["sh", "-c", "gunicorn app:app \
     --bind 0.0.0.0:${PORT} \
     --workers 2 \
     --timeout 120 \
     --access-logfile - \
     --error-logfile -"]
