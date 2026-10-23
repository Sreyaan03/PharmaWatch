# 11-02 Summary: Security hardening, portable deployment, CI jobs

Closes the audit security findings and makes the deployment configuration-driven, keeping the
visual style untouched (backend/CI only). Commits: 1908e2b (security pt1), f1dba96 (deployment +
CI), plus the parameter-validation sweep in this part.

## Task 1 — Security fixes (done; one checkpoint intentionally skipped)
- Admin key accepted from the `X-Admin-Key` header only, compared with `hmac.compare_digest`; the
  query-parameter path is gone (`?admin_key=` → 401).
- `core/errors.py`: one error handler returns a generic message + `request_id` and logs the detail;
  no `str(e)` reaches a client. Fixed a remaining leak in `services/boxed_warnings.py`
  (`get_all_violations_route` returned `str(e)`; now logs and returns a generic message).
- `MAX_CONTENT_LENGTH` set globally (oversized body → 413 before a handler); per-endpoint rate
  limits on login, upload, NER-mine and feedback; default limit lowered via config.
- **Integer query parameters validated and bounded** with a shared `core/http_params.bounded_int`
  (never raises, clamps to a range, falls back to a clamped default). Applied to the Phase-10 /
  newer routes: `surveillance/alerts` and `replay` limits, `interactions/uncharted` min_reports,
  `interactions/recent` limit, `boxed-warning/...all` limits. openFDA queries escape user-supplied
  names (`services/signals._fda_term`; drug-profile/boxed-warning resolve through the same clients).
- Model loading uses `weights_only=True` (torch) safe loaders.
- Dropped the unused `fda_cache` table (schema + migration retired list).
- **Checkpoint skipped by owner decision:** rotating the committed FDA/NCBI keys and purging them
  from git history (`git filter-repo`). The keys remain in history until the owner rotates them;
  this is noted for the pilot-release step. Everything else in Task 1 is complete.

## Task 2 — Portable deployment (done)
- `docker/Dockerfile`: no dataset baked in (the three `COPY data/...` lines removed), runs as a
  non-root `pharma` user, `DATA_DIR=/app/data`. Data and models are host bind mounts at runtime.
- One `docker/compose.yml` with profiles `core` (web + MySQL + Caddy), `kafka` (ZooKeeper, Kafka,
  Kafka UI, producer, consumer) and `demo` (HBase). `DATA_DIR` / `MODELS_DIR` drive the mounts;
  MySQL and Kafka ports are not published (Caddy is the edge). Replaces the old dev + prod compose
  files; `docker-compose.scale.yml` stays separate as the scale-out profile.
- `core/config.py`: `validate()` / `check_or_raise()` report **every** missing required value at
  once at startup (AUTH_REQUIRED → FLASK_SECRET_KEY; s3 → bucket/keys; kafka queue → brokers;
  cloud extractor → ANTHROPIC_API_KEY; plus enum checks). New `DOMAIN_NAME` setting. `.env.example`
  regenerated (46 settings).
- `services/health.py`: `/api/health` now also reports the loaded FAERS release, the deployed DDI
  model + version, and the job-worker configuration (each guarded so the probe never fails).
- `setup.ps1` / `setup.sh`: Google-Drive placeholders replaced with the real FAERS flow
  (`faers.downloader --latest N` → `faers.parser --all` → `faers.loader`); start the core profile.
- Verified: `docker compose -f docker/compose.yml --profile core --profile kafka --profile demo
  config` parses; `/api/health` returns the new fields.

## Task 3 — CI/CD (done)
- `.github/workflows/deploy.yml` split: `lint` (ruff); a `test` matrix over
  `unit` / `integration` / `statistics`; a `validation-smoke` job that uploads
  `reports/validation/` as an artifact. Images build on every PR **without** pushing (proving a
  clean-checkout build with no data), push only on main. **Deploy job unchanged** except the
  compose path/profiles.
- `tests/conftest.py` auto-tags every test into one CI category by file name — a true partition
  (167 unit + 225 integration + 165 statistics = 557), so the split never changes an outcome.

## Tests
- New: `test_security.py` (Task 1), `test_config_validation.py`, `test_deployment.py`,
  `test_http_params.py`. Full suite green locally.

## Not done / deferred
- Key rotation + git-history purge (checkpoint, owner-skipped for now) — must happen before the
  repo is shared publicly or hosted.
- Evidence-badge + feedback-control sweep across the remaining cards (carried from Phase 10).
