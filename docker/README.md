# 🐳 PharmaWatch — Docker Setup

Everything in this folder handles containerised deployment of PharmaWatch.
No source code lives here — all application code stays in `src/` and `backend/`.

---

## Files

| File | Purpose |
|---|---|
| `Dockerfile` | Builds the PharmaWatch web image (Python 3.11-slim + CPU PyTorch), runs as a non-root user, bakes in **no data** |
| `Dockerfile.kafka` | Lightweight image for the Kafka producer & consumer |
| `compose.yml` | Single stack definition; pick what runs with `--profile` |
| `docker-compose.scale.yml` | Horizontal scaling (kept separate on purpose) |
| `.dockerignore` | Excludes venv, logs, raw data, secrets from the build context |

---

## Profiles

One file, three profiles — combine them as needed:

| Profile | Services | When |
|---|---|---|
| `core` | web app, MySQL, Caddy (TLS edge) | always |
| `kafka` | ZooKeeper, Kafka, Kafka UI, producer, consumer | to run the live ingestion stream |
| `demo` | HBase (TWOSIDES store) | optional teaching demo |

---

## Quick Start (run from the **repo root**)

```bash
# App + database + TLS edge only
docker compose -f docker/compose.yml --profile core up -d --build

# Add the live Kafka ingestion stream
docker compose -f docker/compose.yml --profile core --profile kafka up -d --build

# Everything, including the HBase demo
docker compose -f docker/compose.yml --profile core --profile kafka --profile demo up -d --build
```

Visit **http://localhost:5000** directly, or **https://${DOMAIN_NAME}** through Caddy, once the
health check passes (~60 s on first boot because ML models load). The Kafka UI is at
**http://localhost:8086**.

### Plain Docker (web only)
```bash
docker build -f docker/Dockerfile -t pharmawatch .
docker run -p 5000:5000 --env-file backend/.env \
  -v "$PWD/data":/app/data:ro \
  -v "$PWD/backend/trained_models":/app/backend/trained_models:ro \
  pharmawatch
```

---

## Data & models (mounted, never baked in)

The image contains no FAERS database, DDI datasets or trained models. They are mounted read-only
from the host at runtime, so the image is portable and safe to publish:

- `DATA_DIR` (default `../data`) — `pharmawatch_faers.duckdb`, `twosides.parquet`, `drugbank_ddi.tsv`
- `MODELS_DIR` (default `../backend/trained_models`) — deployed DDI model + manifest

Run `setup.sh` / `setup.ps1` once after cloning to create `data/` and fetch the FAERS release.

---

## Environment Variables

Copy `.env.example` to `backend/.env` and fill in what you need (generated from
`backend/core/config.py`). The app validates its configuration at startup and reports **every**
missing required value at once. Key settings for deployment:

```env
DOMAIN_NAME=pharmawatch.example.edu     # Caddy serves this and requests a TLS cert for it
AUTH_REQUIRED=true                      # then FLASK_SECRET_KEY becomes mandatory
FLASK_SECRET_KEY=<64 hex chars>
ALLOWED_ORIGINS=https://pharmawatch.example.edu
```

---

## Notes

- **First build takes ~10–15 minutes** — PyTorch + transformers are large. Subsequent builds are fast (cached layers).
- The raw `data/twosides.csv` (677 MB) is **excluded** from the build context; only the pre-processed `twosides.parquet` is mounted.
- Trained models are mounted read-only, so you can retrain without rebuilding the image.
- MySQL and Kafka ports are **not published** to the host in a profile deployment — Caddy is the only public edge.
- To deploy to a cloud VM, CI builds and pushes the images to GHCR; the `deploy` job pulls and runs `docker compose -f docker/compose.yml --profile core --profile kafka up -d`.
