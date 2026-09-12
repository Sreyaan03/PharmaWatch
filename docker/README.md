# 🐳 PharmaWatch — Docker Setup

Everything in this folder handles containerised deployment of PharmaWatch.
No source code lives here — all application code stays in `src/` and `backend/`.

---

## Files

| File | Purpose |
|---|---|
| `Dockerfile` | Builds the PharmaWatch image (Python 3.11-slim + CPU PyTorch) |
| `docker-compose.yml` | One-command full-stack launcher |
| `.dockerignore` | Excludes venv, logs, raw data from build context |

---

## Quick Start (run from the **repo root**)

### Option A — Docker Compose (recommended)
```bash
docker compose -f docker/docker-compose.yml up --build
```
Visit **http://localhost:5000** once the health-check passes (~60 s on first boot because ML models load).

### Option B — Plain Docker
```bash
# 1. Build the image
docker build -f docker/Dockerfile -t pharmawatch .

# 2. Run with your env file
docker run -p 5000:5000 --env-file backend/.env pharmawatch
```

---

## Environment Variables

Copy `backend/.env` and adjust as needed before running:

```env
FDA_API_KEY=your_key_here
ALLOWED_ORIGINS=http://localhost:5000
```

To add a public URL (e.g. ngrok):
```env
ALLOWED_ORIGINS=http://localhost:5000,https://abc123.ngrok.io
```

---

## Notes for Students

- **First build takes ~10–15 minutes** — PyTorch + transformers are large.
- **Subsequent builds are fast** — Docker caches the dependency layer.
- The `data/twosides.csv` (677 MB) is **excluded** from the image; only the pre-processed `twosides.parquet` is copied.
- Trained LSTM models are mounted as a read-only volume, so you can retrain without rebuilding.
- To deploy to a cloud VM (e.g. Google Cloud free tier, AWS EC2):
  1. Push the image to Docker Hub: `docker push yourusername/pharmawatch`
  2. Pull and run on the VM: `docker run -p 80:5000 yourusername/pharmawatch`
