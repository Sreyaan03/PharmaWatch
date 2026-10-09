# 🚀 PharmaWatch — Complete System Startup & Operations Guide

> **Official Deployment & Developer Operations Manual**  
> *Covers Full-Stack Architecture, Docker Infrastructure, Apache Kafka Pipelines, Apache HBase Thrift Storage, and Flask Backend.*

---

## 📑 Table of Contents
1. [System Architecture Overview](#1-system-architecture-overview)
2. [Component & Port Allocation Matrix](#2-component--port-allocation-matrix)
3. [Prerequisites & Initial Setup](#3-prerequisites--initial-setup)
4. [Startup Flow: Recommended Fast / Hybrid Mode](#4-startup-flow-recommended-fast--hybrid-mode)
5. [Startup Flow: Full Docker Compose Mode](#5-startup-flow-full-docker-compose-mode)
6. [Accessing Applications & Dashboards](#6-accessing-applications--dashboards)
7. [Stopping & Restarting Services](#7-stopping--restarting-services)
8. [Troubleshooting & Battle-Tested Fixes](#8-troubleshooting--battle-tested-fixes)

---

## 1. System Architecture Overview

PharmaWatch is a distributed pharmacovigilance and drug-interaction intelligence platform integrating real-time stream processing, distributed column-family storage, and neural graph prediction.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              FRONTEND SPA                                   │
│            Vanilla JS (ES6+) • CSS3 Glassmorphism • Chart.js                │
│                         (Served via Port 5000)                              │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │ REST API calls / WebSockets
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         FLASK BACKEND ENGINE                                │
│       App Server (Port 5000) • DuckDB FAERS Analytics • PyTorch Models      │
└──────────────┬───────────────────────┬───────────────────────┬──────────────┘
               │                       │                       │
               ▼                       ▼                       ▼
┌──────────────────────────┐  ┌──────────────────┐  ┌────────────────────────┐
│   APACHE HBASE STORAGE   │  │   APACHE KAFKA   │  │    SHARED SQLITE DB    │
│   Docker: hbase-server   │  │  Streaming Bus   │  │    pharmawatch.db      │
│   Thrift RPC: Port 9090  │  │  Host: Port 9092 │  │    Persistent Alerts   │
│   Stores TWOSIDES graph  │  │  UI:   Port 8086 │  │    & Consumer State    │
└──────────────────────────┘  └────────▲────▲────┘  └───────────▲────────────┘
                                       │    │                   │
                     ┌─────────────────┴─┐  └─────────────┐     │
                     │                   │                │     │
            ┌────────┴─────────┐  ┌──────┴─────────┐      │     │
            │  KAFKA PRODUCER  │  │ KAFKA CONSUMER ├──────┴─────┘
            │  scripts/        │  │ scripts/       │
            │  kafka_producer  │  │ kafka_consumer │
            │  Polls FDA RSS,  │  │ Scores risks,  │
            │  VigiBase, Recalls  Persists alerts  │
            └──────────────────┘  └────────────────┘
```

---

## 2. Component & Port Allocation Matrix

To prevent port conflicts when running both **HBase** and **Confluent Kafka** on the same machine, the infrastructure uses custom remapped host ports:

| Service / Container | Container Port | Host Port | Purpose | Potential Conflict & Resolution |
|---|---|---|---|---|
| **PharmaWatch App** | `5000` | `5000` | Main Web UI & Flask API | Standard Flask dev port |
| **Apache ZooKeeper** | `2182` | `2182` | Kafka coordination | ⚠️ Port `2181` is held by HBase; ZK is remapped to `2182` |
| **Apache Kafka Broker** | `29092` / `9092` | `9092` | Event broker | Accessible via `localhost:9092` |
| **Kafka UI (Provectus)**| `8080` | `8086` | Topic & consumer dashboard | ⚠️ Port `8085` & `8080` are held by HBase; UI is remapped to `8086` |
| **Apache HBase Thrift** | `9090` | `9090` | Low-latency big-data RPC | Used by `backend/helpers/external_apis.py` |
| **HBase Embedded Web** | `8080`, `8085` | `8080`, `8085` | HBase Master info & status | Internal HBase metrics |

---

## 3. Prerequisites & Initial Setup

### 3.1 Software Requirements
- **OS**: Windows 10/11 (with WSL2 installed) or macOS / Linux
- **Docker Desktop**: Version 4.25+ with WSL2 engine enabled
- **Python**: Python 3.10 or 3.11 64-bit
- **PowerShell** (Windows) or **Bash** (macOS/Linux)

### 3.2 One-Time Repository Setup

1. **Activate Virtual Environment**:
   *Windows (PowerShell)*:
   ```powershell
   # If script execution is restricted:
   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass

   # Activate the venv:
   .\venv\Scripts\Activate.ps1
   ```
   *Linux / macOS*:
   ```bash
   source venv/bin/activate
   ```

2. **Verify Python Packages**:
   Ensure all critical dependencies are installed in your active environment:
   ```bash
   pip install -r backend/requirements.txt
   pip install feedparser kafka-python thrift duckdb
   ```

3. **Verify Environment Variables (`backend/.env`)**:
   Ensure `backend/.env` exists. If missing, create it:
   ```env
   FDA_API_KEY=your_openfda_api_key_here
   ALLOWED_ORIGINS=http://localhost:5000,http://127.0.0.1:5000
   KAFKA_BOOTSTRAP_SERVERS=localhost:9092
   FLASK_ENV=development
   ```

---

## 4. Startup Flow: Recommended Fast / Hybrid Mode

> [!TIP]
> **Why Hybrid Mode?**  
> Running the Kafka broker, ZooKeeper, Kafka-UI, and HBase inside Docker containers while executing Flask, the producer, and the consumer directly in Python provides **instant hot-reloading**, **zero build wait times**, and direct terminal debugging.

### Step 1: Ensure Docker Desktop is Running
Verify Docker is active by running:
```powershell
docker ps
```
*(If Docker Desktop stalls or fails to respond, see Section 8 for the quick reset sequence).*

---

### Step 2: Start Apache HBase Server
If your `hbase-server` container is already created, start it:
```powershell
docker start hbase-server
```
Verify it is running:
```powershell
docker ps --filter "name=hbase-server"
```
*(Thrift API will now be listening on `localhost:9090`)*.

---

### Step 3: Launch Kafka Infrastructure (ZooKeeper + Broker + UI)
From the **root folder of PharmaWatch**, launch the messaging cluster using the `--no-build` flag:

```powershell
docker compose -f docker/docker-compose.yml up zookeeper kafka kafka-ui --no-build -d
```

> [!IMPORTANT]
> **Always include `--no-build`!**  
> Skipping `--no-build` triggers full container builds for Python ML images, causing unnecessary 15-30 minute compilation stalls.

**Wait ~45 seconds** for Kafka broker healthchecks to stabilize. Check container status:
```powershell
docker compose -f docker/docker-compose.yml ps
```
You should see:
- `zookeeper` — `healthy`
- `kafka` — `healthy`
- `kafka-ui` — `running` / `healthy`

---

### Step 4: Start Flask Backend Server (Terminal 1)
Open **Terminal 1**, activate venv, and run:

```powershell
# Navigate to backend
cd backend

# Activate venv
..\venv\Scripts\Activate.ps1

# Launch Flask application
python app.py
```
*Output will indicate:*
```
 * Running on http://127.0.0.1:5000
 * DuckDB connected.
 * HBase Thrift connection pool initialized.
```

---

### Step 5: Start Kafka Producer (Terminal 2)
Open **Terminal 2** from the **repository root**, activate venv, and run:

```powershell
# Activate venv
.\venv\Scripts\Activate.ps1

# Run the live stream producer
python scripts/kafka_producer.py
```
*The producer will poll FAERS RSS feeds, drug recall databases, and simulate real-time pharmacovigilance telemetry, streaming events directly to `pharmawatch-raw-signals` topic.*

---

### Step 6: Start Kafka Consumer (Terminal 3)
Open **Terminal 3** from the **repository root**, activate venv, and run:

```powershell
# Activate venv
.\venv\Scripts\Activate.ps1

# Run the live risk scoring consumer
python scripts/kafka_consumer.py
```
*The consumer ingests incoming signals, executes disproportionality scoring, and persists safety alerts to `backend/pharmawatch.db`.*

---

## 5. Startup Flow: Full Docker Compose Mode

If deploying into an isolated staging or production environment where the entire stack (including Python services) runs in containers:

```powershell
# Run from repository root
docker compose -f docker/docker-compose.yml up --build -d
```

To view streaming logs across all containers:
```powershell
docker compose -f docker/docker-compose.yml logs -f
```

To view logs for a specific service:
```powershell
docker compose -f docker/docker-compose.yml logs -f pharmawatch
docker compose -f docker/docker-compose.yml logs -f kafka-producer
```

---

## 6. Accessing Applications & Dashboards

Once all services are started, the following web interfaces are available:

| Application | URL | Description |
|---|---|---|
| **PharmaWatch Dashboard** | [http://localhost:5000](http://localhost:5000) | Primary web UI: Signals, Polypharmacy, Risk Analyzer, 3D Graph Explorer |
| **Kafka Cluster UI** | [http://localhost:8086](http://localhost:8086) | Web dashboard: inspect topics, broker partitions, live messages, and consumer groups |
| **Backend Health Check** | [http://localhost:5000/api/health](http://localhost:5000/api/health) | JSON status endpoint confirming DB, DuckDB, and model statuses |

---

## 7. Stopping & Restarting Services

### 7.1 Stop Python Processes
In Terminals 1, 2, and 3, press:
```
Ctrl + C
```

### 7.2 Stop Docker Containers
```powershell
# Stop Kafka, ZooKeeper, and Kafka UI
docker compose -f docker/docker-compose.yml stop

# Stop HBase container
docker stop hbase-server
```

### 7.3 Complete Tear Down (Keeps Volumes)
```powershell
docker compose -f docker/docker-compose.yml down
```

---

## 8. Troubleshooting & Battle-Tested Fixes

### 1. `ModuleNotFoundError: No module named 'feedparser'`
- **Cause**: Python was invoked with the global interpreter rather than the virtual environment.
- **Fix**: Run `.\venv\Scripts\Activate.ps1` before executing scripts, or explicitly run:
  ```powershell
  .\venv\Scripts\python.exe scripts/kafka_producer.py
  ```

### 2. `ImportError: cannot import name 'NoBrokersAvailable' from 'kafka.errors'`
- **Cause**: `kafka-python` version 3.x restructured its exception taxonomy.
- **Fix**: Use `from kafka.errors import KafkaError` (already resolved in `scripts/kafka_producer.py` and `scripts/kafka_consumer.py`).

### 3. Docker Desktop App Freezes / Wont Open
- **Cause**: WSL2 backend socket deadlock on Windows.
- **Fix**: Execute this clean reset in PowerShell as Administrator:
  ```powershell
  taskkill /F /IM "Docker Desktop.exe" /T
  wsl --shutdown
  Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"
  ```
  Wait 30 seconds for the whale icon in the system tray to stabilize.

### 4. `Bind for 0.0.0.0:2181 failed: port is already allocated`
- **Cause**: Apache HBase embeds an internal ZooKeeper instance on port `2181`.
- **Fix**: Verify `docker/docker-compose.yml` has ZooKeeper mapped to host port `2182` (configured by default).

### 5. `Bind for 0.0.0.0:8085 failed: port is already allocated`
- **Cause**: `hbase-server` uses port `8085` for its secondary info dashboard.
- **Fix**: Verify `docker/docker-compose.yml` maps `kafka-ui` to host port `8086` (`8086:8080`).

### 6. HBase Thrift `TTransportException` / Stale Sockets
- **Cause**: HBase Thrift closes idle client connections after 60 seconds of inactivity.
- **Resolution**: Both `backend/helpers/external_apis.py` and `backend/routes/interactions.py` feature automated reconnect logic that transparently retries socket calls on `TTransportException` before failing gracefully.

### 7. Kafka UI Displays "Offline" or Error 502
- **Cause**: Kafka broker is still executing startup logs or cluster metadata initialization.
- **Fix**: Wait 30 seconds for `kafka` container health check to flip to `healthy`. Kafka UI will automatically connect once the broker is ready.
