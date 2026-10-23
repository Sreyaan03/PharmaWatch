# PharmaWatch — Deployment Guide

How to install, run, back up, upgrade and host PharmaWatch. For the teaching side (roles,
exercises, feedback, data-protection rules) see [`PILOT_GUIDE.md`](PILOT_GUIDE.md).

---

## 1. Requirements

- **Docker Desktop** (or Docker Engine + Compose v2), running.
- ~8 GB free disk for the image and the FAERS database; ~4 GB RAM for the app.
- Outbound HTTPS to the FDA (`fis.fda.gov`, `api.fda.gov`) and, optionally, RxNav/NCBI.
- For the no-Docker development path: Python 3.11.

No dataset is shipped in the image. FAERS is downloaded from the FDA and built locally the first
time you run the setup script.

---

## 2. First install

From the repository root:

```bash
# Windows (PowerShell)
./setup.ps1
# macOS / Linux
chmod +x setup.sh && ./setup.sh
```

The script:
1. Checks Docker is running.
2. Creates `backend/.env` from `.env.example` (generated from `backend/core/config.py`).
3. Downloads the most recent FAERS quarters from the FDA and builds
   `data/pharmawatch_faers.duckdb` (`FAERS_QUARTERS=8 ./setup.sh` for fewer/faster).
4. Builds the image and starts the **core** profile (app + MySQL + Caddy).

Open **http://localhost:5000**.

### No-Docker development

```bash
pip install -r backend/requirements.txt
cd backend && python -m faers.downloader --latest 8 && python -m faers.parser --all && python -m faers.loader
python app.py          # http://127.0.0.1:5000
```

---

## 3. Configuration (`.env`)

Every setting lives in `backend/core/config.py` and is documented in `.env.example`
(`python -m core.config` regenerates it). The app **validates configuration at startup and reports
every missing required value at once.** Key settings:

| Setting | Meaning |
|---|---|
| `DATA_DIR` | Large data (FAERS DB, caches). Empty = `<repo>/data`. Point several hosts at the same loaded data by setting this. |
| `MYSQL_*` | Database host, port, user, password, name, pool sizes. |
| `AUTH_REQUIRED` | `true` in a pilot — then `FLASK_SECRET_KEY` becomes **required**. |
| `FLASK_SECRET_KEY` | Signs the session cookie. Generate 64 hex chars. |
| `ADMIN_API_KEY` | Key for admin endpoints (`X-Admin-Key` header). Empty disables them. |
| `SESSION_COOKIE_SECURE` | `true` behind TLS in production. |
| `DOMAIN_NAME` | Public hostname Caddy serves and requests a TLS certificate for. |
| `ALLOWED_ORIGINS` | CORS origins (include your public URL). |
| `FDA_API_KEY`, `NCBI_API_KEY` | Higher openFDA / NCBI rate limits (optional). |
| `ANTHROPIC_API_KEY`, `RX_EXTRACTOR` | Optional cloud prescription extractor (`cloud` requires the key). |
| `FAERS_AUTO_REFRESH` | Load a newly announced FAERS quarter without an admin. |
| Reddit / HBase settings | Optional exploratory and demo components. |

---

## 4. Compose profiles

One file, `docker/compose.yml`, with three profiles (combine as needed):

| Profile | Services | When |
|---|---|---|
| `core` | web app, MySQL, Caddy | always |
| `kafka` | ZooKeeper, Kafka, Kafka UI, producer, consumer | to run the live ingestion stream |
| `demo` | HBase | optional teaching demo |

```bash
docker compose -f docker/compose.yml --profile core up -d --build
docker compose -f docker/compose.yml --profile core --profile kafka up -d --build
```

MySQL and Kafka ports are **not** published to the host — Caddy is the only public edge.
Horizontal scale-out (SeaweedFS + Spark cluster) is a separate file,
`docker/docker-compose.scale.yml`, run on its own when you need it.

---

## 5. Backups

Two things hold state:

- **MySQL** (accounts, audit log, feedback, jobs, caches):
  ```bash
  docker exec pharmawatch-mysql mysqldump -u root -p"$MYSQL_ROOT_PASSWORD" pharmawatch > backup_$(date +%F).sql
  ```
  The data lives in the named volume `pharmawatch_mysql_data`.
- **`DATA_DIR`** (the FAERS DuckDB, DDI datasets, caches): back up the folder. It can be rebuilt
  from the FDA with the setup script, but a copy saves the download.

Trained models are in `backend/trained_models/` (version-controlled manifest + weights) and are
mounted read-only.

---

## 6. Upgrade

```bash
git pull
docker compose -f docker/compose.yml --profile core build
docker compose -f docker/compose.yml --profile core up -d
cd backend && python -m db.migrate     # applies any new schema (idempotent)
```

Schema migrations are additive and idempotent. To load a new FAERS quarter:
`cd backend && python -m faers.downloader --latest 1 && python -m faers.parser --all && python -m faers.loader`.

---

## 7. Hosting on a server

Moving to another host needs **only environment changes**, no code change:

1. Install Docker on the server; clone the repo to `/opt/pharmawatch`.
2. Copy the loaded `DATA_DIR` (or run the setup script to rebuild it) and set `DATA_DIR`.
3. In `.env`: set `DOMAIN_NAME` to your hostname, `AUTH_REQUIRED=true`, a strong `FLASK_SECRET_KEY`,
   `SESSION_COOKIE_SECURE=true`, `ALLOWED_ORIGINS=https://<your-domain>`, and strong MySQL passwords.
4. Point DNS at the server (Caddy obtains the TLS certificate automatically for `DOMAIN_NAME`).
5. `docker compose -f docker/compose.yml --profile core --profile kafka up -d --build`.

CI builds and pushes the images to GHCR on `main`; the deploy job (gated on a server secret) pulls
and restarts the stack. See `.github/workflows/deploy.yml`.

> **Before any public hosting:** rotate the FDA and NCBI API keys that were committed earlier and
> move them to the host's `.env` only. This is a standing release checkpoint.

---

## 8. Health and troubleshooting

- `GET /api/health` reports the database, the loaded FAERS release, the deployed DDI model and the
  job-worker configuration.
- `GET /health/live` (process up) and `GET /health/ready` (503 until every required dependency is
  reachable) are the orchestrator probes.
- First boot takes ~60 s while the ML models load; the image build takes ~10–15 min (PyTorch).
