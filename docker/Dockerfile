# ─────────────────────────────────────────────────────────────────────────────
# PharmaWatch — Dockerfile
# Builds a production container serving the Flask API backend via Gunicorn
# and static assets from /src.
# ─────────────────────────────────────────────────────────────────────────────

# ── Stage 1: Base Image ──────────────────────────────────────────────────────
FROM python:3.11-slim

# ── System Dependencies ───────────────────────────────────────────────────────
# libgomp1  → required by PyTorch (OpenMP runtime)
# libglib2.0-0, libsm6, libxext6, libxrender-dev → required by EasyOCR / cv2
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgomp1 \
        libglib2.0-0 \
        libsm6 \
        libxext6 \
        libxrender-dev \
        curl \
    && rm -rf /var/lib/apt/lists/*

# ── Working Directory ─────────────────────────────────────────────────────────
WORKDIR /app

# ── Python Dependencies ───────────────────────────────────────────────────────
COPY backend/requirements.txt /app/requirements.txt

# Install CPU-only PyTorch first (saves ~1 GB vs default CUDA build). torchvision is pinned
# with it: easyocr asks for an unpinned torchvision, and the newest one would otherwise
# replace this torch with the CUDA build and download the NVIDIA libraries.
# then install the rest of the project requirements.
RUN pip install --no-cache-dir \
        "torch==2.2.0+cpu" "torchvision==0.17.0+cpu" --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r /app/requirements.txt

# EasyOCR downloads its detection and recognition models on first use. Fetch them at build time,
# so a new worker does not spend a minute downloading before its first prescription.
RUN python -c "import easyocr; easyocr.Reader(['en'], gpu=False, verbose=False)"

# ── Application Source ────────────────────────────────────────────────────────
# Copy backend Python application
COPY backend/ /app/backend/

# Copy frontend static files (served by Caddy or Flask send_from_directory)
COPY src/ /app/src/

# No data is baked into the image. The FAERS database, the DDI datasets and the trained models are
# mounted as volumes at runtime (see compose.yml and DATA_DIR). This keeps the image portable and
# free of any dataset, so it can be published and moved between hosts safely.

# ── Environment Defaults ──────────────────────────────────────────────────────
ENV FLASK_ENV=production \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app:/app/backend \
    DATA_DIR=/app/data

# ── Non-root user ─────────────────────────────────────────────────────────────
# Run as an unprivileged user; create the data/cache dirs it may write to.
RUN useradd --create-home --uid 10001 pharma \
    && mkdir -p /app/data /app/backend/http_cache \
    && chown -R pharma:pharma /app
USER pharma

# ── Expose Port ───────────────────────────────────────────────────────────────
EXPOSE 5000

# ── Entrypoint ────────────────────────────────────────────────────────────────
WORKDIR /app/backend
# Gunicorn with 1 worker and 8 threads to prevent OOM with large PyTorch models
CMD ["gunicorn", "--workers=1", "--threads=8", "--bind=0.0.0.0:5000", "--timeout=120", "--access-logfile=-", "--error-logfile=-", "app:app"]
