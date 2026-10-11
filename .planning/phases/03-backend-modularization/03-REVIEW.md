---
status: clean
phase: "03"
phase_name: "backend-modularization"
depth: standard
files_reviewed: 31
critical: 0
warning: 0
info: 1
total: 1
reviewed_at: "2026-10-11T14:38:00+05:30"
fixed_at: "2026-10-11T14:52:30+05:30"
branch: feat/mysql-production
commit: d0682c2
---

# Code Review Report: Phase 03 — Production Hardening & MySQL Migration

## Executive Summary
A comprehensive standard-depth code review was conducted on the 31 files modified on branch `feat/mysql-production` (commit `e3f0ec4`). The review evaluated changes spanning the MySQL 8.0 migration, connection pooling, real global FAERS disproportionality scoring, Caddy reverse-proxy TLS edge, Docker Compose production topology, and GitHub Actions CI/CD pipelines.

The architecture is robust, type-safe, and passes 100% of unit and integration tests (13/13 passed). Zero critical security vulnerabilities (such as SQL injection or credential leaks) were detected. Three actionable warnings and three optimization suggestions were identified and cataloged below.

---

## Scope of Review
Total files evaluated: **31**
- **Infrastructure & Deployment (6)**: `.dockerignore`, `docker/Caddyfile`, `docker/Dockerfile`, `docker/Dockerfile.kafka`, `docker/docker-compose.prod.yml`, `docker/docker-compose.yml`
- **CI/CD Pipeline (1)**: `.github/workflows/deploy.yml`
- **Database Layer & Migrations (5)**: `backend/database.py`, `backend/db/__init__.py`, `backend/db/migrate.py`, `backend/db/mysql.py`, `backend/db/schema.sql`
- **Analytics & Scoring Engines (3)**: `backend/faers/disproportionality.py`, `scripts/kafka_consumer.py`, `scripts/recalculate_signals.py`
- **API Blueprints & Backend Core (8)**: `backend/app.py`, `backend/helpers/external_apis.py`, `backend/routes/dashboard.py`, `backend/routes/demographics.py`, `backend/routes/general.py`, `backend/routes/interactions.py`, `backend/routes/ml.py`, `backend/routes/pipeline.py`, `backend/routes/signals.py`, `backend/routes/temporal.py`
- **Tests & Quality Assurance (2)**: `backend/tests/test_mysql_foundation.py`, `backend/tests/test_mysql_routes.py`
- **Frontend Integration (3)**: `src/_interactions_new.js`, `src/app.js`, `src/biobert.js`
- **Dependencies (1)**: `backend/requirements.txt`

---

## Pillar Assessments

### 1. Security & Authentication
- **SQL Injection**: **CLEAN**. All database interactions across `backend/db/mysql.py`, `backend/database.py`, `scripts/kafka_consumer.py`, and `scripts/recalculate_signals.py` exclusively use parameterized tuples (`%s`). Zero f-strings or manual SQL string concats.
- **Credential Hygiene**: **CLEAN**. Sensitive database credentials and API keys are read via `dotenv` from `.env` or system environment variables. None are hardcoded.
- **Edge Security**: **CLEAN**. `docker/Caddyfile` sets HSTS (`max-age=31536000; includeSubDomains; preload`), `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, and strips the `Server` header.
- **Port Isolation**: **CLEAN**. `docker/docker-compose.prod.yml` isolates MySQL (`3306`), Kafka (`29092`), and Zookeeper (`2181`) inside the internal `pharmawatch-net` bridge network with zero public host port bindings.
- **Finding**: Admin endpoint auth policy differs between blueprints (see `WR-01`).

### 2. Logic & Correctness
- **Global FAERS Denominator**: **VERIFIED**. Corrected single-cohort $c=0$ inflation in disproportionality scoring by integrating the true global FAERS background denominator ($N = 20,692,690$) and `faers_background_events` persistent cache.
- **Type Conversions**: **VERIFIED**. `clean_row()` in `backend/db/mysql.py` recursively transforms PyMySQL `Decimal` types to `int`/`float`, preventing Flask JSON serialization crashes.
- **Network Graph Integrity**: **VERIFIED**. `get_signal_network()` in `backend/routes/signals.py` strictly verifies node referential integrity before emitting links to D3.

### 3. Concurrency & Performance
- **Connection Pooling**: **VERIFIED**. `DBUtils.PooledDB` manages thread-safe connections with active borrow ping (`ping=7`), auto-reconnect, and context-managed commit/rollback.
- **Network Caching**: **VERIFIED**. In-memory lookup tables reduce signal network latency from ~4.3s to 0.006s and drug resolution from 7.6s to 0.010s.
- **Resource Cleanup**: Attention required on HBase Thrift socket handling during network timeouts (see `WR-02`).

### 4. Deployment & Operations
- **CI/CD Pipeline**: Multi-job workflow (`test` and `build-and-push`) validates linting (`ruff`), runs migrations against a live MySQL service container, and executes pytest.
- **Docker Multi-Stage Build**: Uses slim Python 3.11 with torch-cpu to keep images lightweight.

---

## Detailed Findings

### WR-01: Admin Endpoints Inconsistent Auth Model
- **File**: `backend/routes/signals.py:330-349`
- **Severity**: Warning
- **Category**: Security
- **Description**: 
  In `backend/routes/signals.py`, `/api/reddit/scrape` and `/api/reddit/process` check:
  ```python
  admin_key = os.getenv("ADMIN_API_KEY", "").strip()
  if admin_key:
      provided = request.headers.get("X-Admin-Key") or request.args.get("admin_key")
      if not provided or provided.strip() != admin_key:
          return jsonify({"error": "Unauthorized: invalid or missing admin key"}), 401
  ```
  If `ADMIN_API_KEY` is not set or empty, this condition evaluates to false and fails open (allowing unauthenticated requests to trigger scraping and NER processing). In contrast, `backend/routes/ml.py:30-32` fails closed (`if not admin_key: return 403`).
- **Remediation**:
  Align `backend/routes/signals.py` with `backend/routes/ml.py` so that when `ADMIN_API_KEY` is not configured, admin endpoints return `403 Admin endpoints disabled. Configure ADMIN_API_KEY in .env`.

### WR-02: HBase Thrift Connection Leak on Exception
- **File**: `backend/routes/interactions.py:171, 328, 437`
- **Severity**: Warning
- **Category**: Resource Management / Reliability
- **Description**:
  In `_do_scan()`, `predict_interaction()`, and `get_twosides_graph()`, `conn.close()` is placed at the bottom of the try block without a `finally` clause:
  ```python
  conn = _hbase_connect()
  table = conn.table('interactions')
  for key, data in rows_iter:
      ...
  conn.close()
  ```
  If `table.scan()` or `table.row()` raises a `TTransportException` or socket timeout mid-iteration, `conn.close()` is skipped and caught by the outer `except Exception: pass`, leaking an active TCP socket until garbage collection.
- **Remediation**:
  Wrap all HBase connection usages in `try ... finally: conn.close()` or provide a context manager in `external_apis.py`.

### WR-03: Hardcoded Host and Port for HBase Connection
- **File**: `backend/helpers/external_apis.py:54`
- **Severity**: Warning
- **Category**: Configuration & Portability
- **Description**:
  In `backend/helpers/external_apis.py`, `_open_fresh()` connects with hardcoded values:
  ```python
  c = happybase.Connection('127.0.0.1', port=9090, timeout=3000)
  ```
  If HBase runs inside a Docker container network under service name `hbase-server`, the backend cannot connect without code modification.
- **Remediation**:
  Read host and port from environment variables:
  ```python
  host = os.getenv("HBASE_HOST", "127.0.0.1")
  port = int(os.getenv("HBASE_PORT", 9090))
  c = happybase.Connection(host, port=port, timeout=3000)
  ```

### IN-01: Redundant Local Imports and Variable Scoping in `disproportionality.py`
- **File**: `backend/faers/disproportionality.py:59-71`
- **Severity**: Info
- **Category**: Code Quality
- **Description**:
  In `get_global_event_count()`, `from db.mysql import query_one, execute` is imported inside a `try` block and later referenced with `if execute:`. If the first `query_one` succeeds and returns early, `execute` is never used. Pre-declaring `execute = None` before the `try` block or importing at module level clarifies variable lifecycle.

### IN-02: Missing LRU/TTL Eviction on `_SIGNAL_NETWORK_CACHE`
- **File**: `backend/routes/signals.py:250, 326`
- **Severity**: Info
- **Category**: Performance
- **Description**:
  `_SIGNAL_NETWORK_CACHE` is capped at 250 items with `if len(_SIGNAL_NETWORK_CACHE) < 250:`. While this prevents unbounded memory growth, once the dictionary reaches 250 elements, newly queried pairs cannot be cached. Replacing with an `OrderedDict` or simple TTL eviction policy ensures recent queries benefit from caching.

### IN-03: Production Container Memory Limits
- **File**: `docker/docker-compose.prod.yml`
- **Severity**: Info
- **Category**: Operations
- **Description**:
  For deployments on budget cloud VMs (1 GB to 2 GB RAM), Kafka and MySQL can consume significant memory during high ingestion bursts. Defining resource constraints (e.g. `mem_limit: 768m` for Kafka, `512m` for MySQL) prevents the Linux OOM-killer from abruptly terminating containers.

---

## Action Plan & Recommendations
1. **Immediate Auto-Fixes**:
   - Align `signals.py` admin auth to fail closed.
   - Wrap HBase connections in `try ... finally: conn.close()`.
   - Add `HBASE_HOST` / `HBASE_PORT` environment variables to `external_apis.py`.
2. **Next Steps**:
   - Run `/gsd-code-review-fix 03` or execute the recommended fixes.
   - Once review is signed off, proceed to the Cloud VM deployment step when ready.
