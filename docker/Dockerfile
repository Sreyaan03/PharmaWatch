# ─────────────────────────────────────────────────────────────────────────────
# PharmaWatch — Dockerfile
# Builds a single container that serves BOTH the Flask backend (port 5000)
# and the static frontend from the /src directory.
#
# Usage (from the repo root):
#   docker build -f docker/Dockerfile -t pharmawatch .
#   docker run -p 5000:5000 --env-file backend/.env pharmawatch
# ─────────────────────────────────────────────────────────────────────────────

# ── Stage 1: base image ──────────────────────────────────────────────────────
# Use a slim Python 3.11 image to keep the final image as small as possible.
FROM python:3.11-slim

# ── System dependencies ───────────────────────────────────────────────────────
# libgomp1  → required by PyTorch (OpenMP runtime)
# libglib2.0-0, libsm6, libxext6, libxrender-dev → required by EasyOCR / cv2
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgomp1 \
        libglib2.0-0 \
        libsm6 \
        libxext6 \
        libxrender-dev \
    && rm -rf /var/lib/apt/lists/*

# ── Working directory ─────────────────────────────────────────────────────────
WORKDIR /app

# ── Python dependencies ───────────────────────────────────────────────────────
# Copy requirements first so Docker can cache this layer independently from
# the source code — rebuilds are much faster this way.
COPY backend/requirements.txt /app/requirements.txt

# Install CPU-only PyTorch first (saves ~1 GB vs the default CUDA build)
# then install the rest of the project requirements.
RUN pip install --no-cache-dir \
        "torch==2.2.0+cpu" --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r /app/requirements.txt

# ── Application source ────────────────────────────────────────────────────────
# Copy the backend Python code
COPY backend/ /app/backend/

# Copy the frontend static files (served by Flask via send_from_directory)
COPY src/ /app/src/

# Copy data files needed at runtime (SQLite DB, parquet files, DuckDB)
# NOTE: twosides.csv is 677 MB — only copy what the app actually loads.
COPY data/pharmawatch_faers.duckdb /app/data/pharmawatch_faers.duckdb
COPY data/twosides.parquet         /app/data/twosides.parquet
COPY data/drugbank_ddi.tsv         /app/data/drugbank_ddi.tsv

# ── Environment defaults ──────────────────────────────────────────────────────
# These can be overridden with --env or --env-file at runtime.
ENV FLASK_ENV=production \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# ── Expose port ───────────────────────────────────────────────────────────────
EXPOSE 5000

# ── Entrypoint ────────────────────────────────────────────────────────────────
# Run from the backend directory so relative imports work correctly.
WORKDIR /app/backend
CMD ["python", "app.py"]
