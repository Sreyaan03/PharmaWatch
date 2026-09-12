# PharmaWatch — Big Data Upgrade Guide

6 phases remaining. Each phase builds on the previous one.

---

## PHASE 1: Reddit API Credentials (10 min — manual)

The scraper code is written but needs your Reddit credentials.

1. Go to **https://www.reddit.com/prefs/apps** → register for API access first if prompted
2. Create a **script** type app named `PharmaWatch`
3. Edit `backend/reddit_scraper.py` Lines 9–11:

```python
REDDIT_CLIENT_ID = "paste_your_client_id"
REDDIT_CLIENT_SECRET = "paste_your_secret"
REDDIT_USER_AGENT = "PharmaWatch:v1.0 (by u/your_username)"
```

### Test:
```bash
curl -X POST http://127.0.0.1:5000/api/reddit/scrape
curl -X POST http://127.0.0.1:5000/api/reddit/process
curl http://127.0.0.1:5000/api/local-stats
```

---

## PHASE 2: Real LSTM Model (2–3 hours)

**Problem**: The current `/api/lstm` endpoint uses a rolling average pretending to be LSTM. No neural network runs.

**Solution**: Train a real PyTorch LSTM on openFDA historical data.

### Step 2.1 — Install dependencies

```bash
pip install scikit-learn
```
(PyTorch and numpy are already installed)

### Step 2.2 — Create NEW file: `backend/lstm_model.py`

```python
"""
lstm_model.py — Real LSTM model for adverse event forecasting
Trains on openFDA historical daily report counts per drug.
"""
import torch
import torch.nn as nn
import numpy as np
import requests
import os
import json

MODEL_DIR = os.path.join(os.path.dirname(__file__), "trained_models")
os.makedirs(MODEL_DIR, exist_ok=True)

# ── LSTM Architecture ────────────────────────────────────────────
class ADEForecaster(nn.Module):
    def __init__(self, input_size=1, hidden_size=64, num_layers=2, output_size=1):
        super().__init__()
        self.hidden_size = hidden_size
        self.num_layers = num_layers
        self.lstm = nn.LSTM(input_size, hidden_size, num_layers, batch_first=True, dropout=0.2)
        self.fc = nn.Linear(hidden_size, output_size)

    def forward(self, x):
        h0 = torch.zeros(self.num_layers, x.size(0), self.hidden_size)
        c0 = torch.zeros(self.num_layers, x.size(0), self.hidden_size)
        out, _ = self.lstm(x, (h0, c0))
        out = self.fc(out[:, -1, :])
        return out


# ── Data fetching ────────────────────────────────────────────────
def fetch_fda_timeseries(drug, limit=365):
    """Fetch daily adverse event counts from openFDA for a drug."""
    url = f'https://api.fda.gov/drug/event.json?search=patient.drug.medicinalproduct:"{drug}"&count=receivedate&limit={limit}'
    res = requests.get(url).json()
    results = res.get("results", [])
    counts = [r["count"] for r in results]
    return counts


# ── Sequence creation ────────────────────────────────────────────
def create_sequences(data, seq_length=30):
    """Create sliding window sequences for LSTM training."""
    xs, ys = [], []
    for i in range(len(data) - seq_length):
        xs.append(data[i:i + seq_length])
        ys.append(data[i + seq_length])
    return np.array(xs), np.array(ys)


# ── Training ─────────────────────────────────────────────────────
def train_model(drug, epochs=100, seq_length=30, lr=0.001):
    """Train LSTM on real openFDA data for a specific drug."""
    raw = fetch_fda_timeseries(drug, limit=365)
    if len(raw) < seq_length + 10:
        return None, "Not enough data points from openFDA"

    # Normalize
    data = np.array(raw, dtype=np.float32)
    data_min, data_max = data.min(), data.max()
    data_range = data_max - data_min if data_max != data_min else 1.0
    normalized = (data - data_min) / data_range

    X, y = create_sequences(normalized, seq_length)
    X_tensor = torch.FloatTensor(X).unsqueeze(-1)  # [batch, seq, 1]
    y_tensor = torch.FloatTensor(y).unsqueeze(-1)

    # Train/test split (80/20 chronological)
    split = int(len(X_tensor) * 0.8)
    X_train, X_test = X_tensor[:split], X_tensor[split:]
    y_train, y_test = y_tensor[:split], y_tensor[split:]

    model = ADEForecaster()
    criterion = nn.MSELoss()
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)

    model.train()
    for epoch in range(epochs):
        output = model(X_train)
        loss = criterion(output, y_train)
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()
        if (epoch + 1) % 20 == 0:
            print(f"  [LSTM] {drug} epoch {epoch+1}/{epochs}, loss={loss.item():.6f}")

    # Save model + metadata
    save_path = os.path.join(MODEL_DIR, f"lstm_{drug.lower()}.pt")
    meta_path = os.path.join(MODEL_DIR, f"lstm_{drug.lower()}_meta.json")
    torch.save(model.state_dict(), save_path)
    with open(meta_path, "w") as f:
        json.dump({"drug": drug, "min": float(data_min), "max": float(data_max),
                    "seq_length": seq_length, "data_points": len(raw)}, f)

    print(f"  [LSTM] Model saved to {save_path}")
    return model, None


# ── Inference ────────────────────────────────────────────────────
def predict(drug, seq_length=30):
    """Load trained model and predict baseline vs actual."""
    save_path = os.path.join(MODEL_DIR, f"lstm_{drug.lower()}.pt")
    meta_path = os.path.join(MODEL_DIR, f"lstm_{drug.lower()}_meta.json")

    if not os.path.exists(save_path):
        return None, "Model not trained yet — call /api/lstm/train?drug=X first"

    with open(meta_path) as f:
        meta = json.load(f)

    raw = fetch_fda_timeseries(drug, limit=365)
    if len(raw) < seq_length:
        return None, "Not enough recent data"

    data = np.array(raw, dtype=np.float32)
    data_min, data_max = meta["min"], meta["max"]
    data_range = data_max - data_min if data_max != data_min else 1.0
    normalized = (data - data_min) / data_range

    model = ADEForecaster()
    model.load_state_dict(torch.load(save_path, weights_only=True))
    model.eval()

    # Run prediction on sliding windows
    baseline = []
    with torch.no_grad():
        for i in range(seq_length, len(normalized)):
            seq = torch.FloatTensor(normalized[i-seq_length:i]).unsqueeze(0).unsqueeze(-1)
            pred = model(seq).item()
            baseline.append(round(pred * data_range + data_min, 1))

    actual = raw[seq_length:]
    labels = [f"Day {i+1}" for i in range(len(actual))]

    return {"drug": drug, "labels": labels, "baseline": baseline, "actual": actual,
            "trained_on": meta["data_points"]}, None
```

### Step 2.3 — Update `/api/lstm` in `backend/app.py`

**Add import after line 15:**
```python
from lstm_model import train_model as lstm_train, predict as lstm_predict
```

**Replace the entire `/api/lstm` route with:**
```python
@app.route("/api/lstm/train", methods=["POST"])
def train_lstm():
    """Train LSTM on real openFDA data for a drug."""
    drug = request.args.get('drug', 'Metformin')
    model, error = lstm_train(drug, epochs=100)
    if error:
        return jsonify({"error": error}), 400
    return jsonify({"status": "ok", "drug": drug, "message": "Model trained and saved"})

@app.route("/api/lstm", methods=["GET"])
def get_lstm_forecast():
    """Fetch LSTM prediction using trained model."""
    drug = request.args.get('drug', 'Metformin')
    try:
        result, error = lstm_predict(drug)
        if error:
            return jsonify({"error": error}), 400
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
```

### Step 2.4 — Test
```bash
# Train a model (takes ~30 seconds)
curl -X POST "http://127.0.0.1:5000/api/lstm/train?drug=Metformin"

# Get real LSTM predictions
curl "http://127.0.0.1:5000/api/lstm?drug=Metformin"
```

---

## PHASE 3: Install Docker + HBase (1–2 hours)

HBase requires Hadoop ecosystem. Docker is the cleanest way on Windows.

### Step 3.1 — Install Docker Desktop

1. Download from **https://www.docker.com/products/docker-desktop/**
2. Install and restart your PC
3. Open Docker Desktop, ensure it says "Docker is running"

### Step 3.2 — Start HBase container

```bash
docker run -d --name hbase-pharmawatch ^
  -p 9090:9090 ^
  -p 16010:16010 ^
  -v hbase-data:/hbase-data ^
  harisekhon/hbase
```

- Port `9090` = Thrift API (Python connects here)
- Port `16010` = HBase Web UI (http://localhost:16010)
- `-v hbase-data` = data persists across container restarts

### Step 3.3 — Start Thrift server inside container

```bash
docker exec -d hbase-pharmawatch hbase thrift start
```

Wait 10 seconds, then verify:
```bash
docker exec hbase-pharmawatch hbase shell <<< "status"
```

### Step 3.4 — Install HappyBase

```bash
pip install happybase
```

### Step 3.5 — Create NEW file: `backend/hbase_store.py`

```python
"""
hbase_store.py — HBase storage layer for PharmaWatch
Connects via Thrift to HBase running in Docker.
Tables: drug_events, reddit_posts
"""
import happybase
import json
import time
import uuid

HBASE_HOST = "localhost"
HBASE_PORT = 9090

def get_connection():
    return happybase.Connection(HBASE_HOST, port=HBASE_PORT)

def init_tables():
    """Create HBase tables if they don't exist."""
    conn = get_connection()
    existing = [t.decode() for t in conn.tables()]

    if b'drug_events' not in conn.tables():
        conn.create_table('drug_events', {
            'info': dict(max_versions=1),      # drug, event, source
            'meta': dict(max_versions=1),       # confidence, raw_text
            'ts': dict(max_versions=1),         # created_at
        })
        print("[HBase] Created table: drug_events")

    if b'reddit_posts' not in conn.tables():
        conn.create_table('reddit_posts', {
            'content': dict(max_versions=1),    # title, body, subreddit
            'meta': dict(max_versions=1),       # score, processed
        })
        print("[HBase] Created table: reddit_posts")

    conn.close()
    print("[HBase] Tables initialized")

def store_drug_event(drug, event, source="manual", confidence=1.0, raw_text=None):
    """Store a drug-event pair in HBase."""
    conn = get_connection()
    table = conn.table('drug_events')
    row_key = f"{drug.lower()}_{event.lower()}_{uuid.uuid4().hex[:8]}"
    table.put(row_key.encode(), {
        b'info:drug': drug.lower().encode(),
        b'info:event': event.lower().encode(),
        b'info:source': source.encode(),
        b'meta:confidence': str(confidence).encode(),
        b'meta:raw_text': (raw_text or "").encode()[:500],
        b'ts:created_at': str(time.time()).encode(),
    })
    conn.close()

def scan_drug_events(drug=None, limit=100):
    """Scan drug_events table, optionally filtering by drug."""
    conn = get_connection()
    table = conn.table('drug_events')
    results = []
    prefix = f"{drug.lower()}_".encode() if drug else None

    scanner = table.scan(row_prefix=prefix, limit=limit) if prefix else table.scan(limit=limit)
    for key, data in scanner:
        results.append({
            "row_key": key.decode(),
            "drug": data.get(b'info:drug', b'').decode(),
            "event": data.get(b'info:event', b'').decode(),
            "source": data.get(b'info:source', b'').decode(),
            "confidence": float(data.get(b'meta:confidence', b'0')),
        })
    conn.close()
    return results

def get_event_counts(drug, limit=10):
    """Count events for a drug from HBase."""
    events = scan_drug_events(drug, limit=1000)
    counts = {}
    for e in events:
        ev = e["event"]
        counts[ev] = counts.get(ev, 0) + 1
    sorted_events = sorted(counts.items(), key=lambda x: -x[1])[:limit]
    return [{"event": k, "count": v} for k, v in sorted_events]

def store_reddit_post(post_id, subreddit, title, body, score):
    """Store a Reddit post in HBase."""
    conn = get_connection()
    table = conn.table('reddit_posts')
    table.put(post_id.encode(), {
        b'content:subreddit': subreddit.encode(),
        b'content:title': title.encode(),
        b'content:body': body[:2000].encode(),
        b'meta:score': str(score).encode(),
        b'meta:processed': b'0',
    })
    conn.close()
```

### Step 3.6 — Add HBase routes to `backend/app.py`

Add these before the entry point:
```python
# ── HBase endpoints (optional — only if Docker + HBase running) ──
try:
    from hbase_store import init_tables as hbase_init, store_drug_event as hbase_store, scan_drug_events, get_event_counts
    hbase_init()
    HBASE_AVAILABLE = True
    print("[HBase] Connected successfully")
except Exception as e:
    HBASE_AVAILABLE = False
    print(f"[HBase] Not available: {e} — SQLite will be used")

@app.route("/api/hbase/status", methods=["GET"])
def hbase_status():
    return jsonify({"available": HBASE_AVAILABLE})

@app.route("/api/hbase/events", methods=["GET"])
def hbase_events():
    if not HBASE_AVAILABLE:
        return jsonify({"error": "HBase not running"}), 503
    drug = request.args.get("drug")
    events = scan_drug_events(drug, limit=50)
    return jsonify({"events": events, "count": len(events)})
```

### Step 3.7 — Test
```bash
# Check HBase is accessible
curl http://127.0.0.1:5000/api/hbase/status

# Query events
curl "http://127.0.0.1:5000/api/hbase/events?drug=metformin"

# HBase Web UI
# Open http://localhost:16010 in browser
```

---

## PHASE 4: Apache Spark Streaming Pipeline (2–3 hours)

PySpark processes data streams and writes to both SQLite and HBase.

### Step 4.1 — Install prerequisites

1. **Java JDK 11**: Download from https://adoptium.net/ → set `JAVA_HOME` env variable
2. **Hadoop WinUtils**: Download `winutils.exe` for Hadoop 3.x, place in `C:\hadoop\bin`, set `HADOOP_HOME=C:\hadoop`
3. **PySpark**:
```bash
pip install pyspark
```

### Step 4.2 — Create NEW file: `backend/spark_pipeline.py`

```python
"""
spark_pipeline.py — Spark Structured Streaming pipeline for PharmaWatch
Reads from openFDA, processes through NER, writes to SQLite + HBase.
"""
from pyspark.sql import SparkSession
from pyspark.sql.functions import col, udf, current_timestamp, lit
from pyspark.sql.types import StringType, ArrayType, StructType, StructField, FloatType
import requests
import json
import time

def create_spark_session():
    return SparkSession.builder \
        .appName("PharmaWatch-Pipeline") \
        .master("local[*]") \
        .config("spark.driver.memory", "2g") \
        .config("spark.sql.streaming.checkpointLocation", "./spark_checkpoints") \
        .getOrCreate()

def fetch_fda_batch(drug, limit=100):
    """Fetch a batch of adverse event reports from openFDA."""
    url = f'https://api.fda.gov/drug/event.json?search=patient.drug.medicinalproduct:"{drug}"&limit={limit}'
    try:
        res = requests.get(url, timeout=10).json()
        return res.get("results", [])
    except Exception as e:
        print(f"[Spark] FDA fetch error: {e}")
        return []

def extract_drug_event_pairs(reports):
    """Extract structured drug-event pairs from raw FDA reports."""
    pairs = []
    for report in reports:
        drugs = [d.get("medicinalproduct", "").upper()
                 for d in report.get("patient", {}).get("drug", [])]
        events = [r.get("reactionmeddrapt", "").lower()
                  for r in report.get("patient", {}).get("reaction", [])]
        for drug in drugs:
            for event in events:
                if drug and event:
                    pairs.append({"drug": drug.lower(), "event": event,
                                  "source": "openfda", "confidence": 1.0})
    return pairs

def run_batch_pipeline(drugs, limit_per_drug=100):
    """Run a batch processing pipeline for multiple drugs."""
    spark = create_spark_session()
    all_pairs = []

    for drug in drugs:
        print(f"[Spark] Processing {drug}...")
        reports = fetch_fda_batch(drug, limit=limit_per_drug)
        pairs = extract_drug_event_pairs(reports)
        all_pairs.extend(pairs)
        print(f"[Spark] {drug}: {len(pairs)} drug-event pairs extracted")

    if all_pairs:
        df = spark.createDataFrame(all_pairs)
        # Aggregate: count occurrences of each drug-event pair
        summary = df.groupBy("drug", "event", "source").count() \
                     .orderBy(col("count").desc())
        print("[Spark] === Top Drug-Event Pairs ===")
        summary.show(20, truncate=False)

        # Write to Parquet (data lake format)
        output_path = "./data_lake/drug_events"
        summary.write.mode("append").parquet(output_path)
        print(f"[Spark] Written to {output_path}")

    spark.stop()
    return len(all_pairs)

def run_streaming_simulation():
    """Simulate streaming by polling openFDA at intervals."""
    MONITORED_DRUGS = ["Metformin", "Warfarin", "Aspirin", "Ibuprofen",
                       "Sertraline", "Gabapentin", "Atorvastatin", "Lisinopril"]

    print("[Spark] Starting streaming pipeline simulation...")
    print(f"[Spark] Monitoring {len(MONITORED_DRUGS)} drugs")

    while True:
        total = run_batch_pipeline(MONITORED_DRUGS, limit_per_drug=50)
        print(f"[Spark] Batch complete: {total} pairs. Sleeping 5 minutes...")
        time.sleep(300)

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--stream":
        run_streaming_simulation()
    else:
        drugs = ["Metformin", "Warfarin", "Aspirin", "Ibuprofen"]
        run_batch_pipeline(drugs)
```

### Step 4.3 — Add Spark route to `backend/app.py`

```python
@app.route("/api/spark/run", methods=["POST"])
def trigger_spark():
    """Trigger a Spark batch processing run."""
    from spark_pipeline import run_batch_pipeline
    drugs = request.json.get("drugs", ["Metformin", "Warfarin", "Aspirin"])
    limit = request.json.get("limit", 50)
    count = run_batch_pipeline(drugs, limit_per_drug=limit)
    return jsonify({"status": "ok", "pairs_processed": count})
```

### Step 4.4 — Test
```bash
# One-time batch run
python backend/spark_pipeline.py

# Continuous streaming mode
python backend/spark_pipeline.py --stream

# Via API
curl -X POST http://127.0.0.1:5000/api/spark/run -H "Content-Type: application/json" -d "{\"drugs\": [\"Metformin\", \"Warfarin\"]}"
```

---

## PHASE 5: Secure the Pipeline (1 hour)

### Step 5.1 — Create NEW file: `backend/.env`

```env
# Flask
FLASK_SECRET_KEY=generate-a-random-string-here

# Reddit (fill in your values)
REDDIT_CLIENT_ID=your_client_id
REDDIT_CLIENT_SECRET=your_client_secret
REDDIT_USERNAME=your_username

# HBase
HBASE_HOST=localhost
HBASE_PORT=9090

# openFDA (optional — increases rate limit)
FDA_API_KEY=your_key_from_https://open.fda.gov/apis/authentication/
```

### Step 5.2 — Install python-dotenv

```bash
pip install python-dotenv
```

### Step 5.3 — Update `backend/app.py` top-level imports

Add at line 7 (before Flask import):
```python
from dotenv import load_dotenv
load_dotenv()
```

Then replace any hardcoded values with:
```python
import os
# In reddit_scraper.py:
REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID", "")
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET", "")
```

### Step 5.4 — Add `.env` to `.gitignore`

```bash
echo .env >> .gitignore
echo pharmawatch.db >> .gitignore
echo trained_models/ >> .gitignore
echo data_lake/ >> .gitignore
echo spark_checkpoints/ >> .gitignore
```

### Step 5.5 — Add API rate limiting

```bash
pip install flask-limiter
```

Add to `backend/app.py`:
```python
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

limiter = Limiter(get_remote_address, app=app, default_limits=["200 per hour"])
```

---

## PHASE 6: Wire HBase into Spark Output (30 min)

### Step 6.1 — Update `backend/spark_pipeline.py`

Add after the Parquet write in `run_batch_pipeline()`:
```python
        # Also write to HBase if available
        try:
            from hbase_store import store_drug_event as hbase_store
            for pair in all_pairs:
                hbase_store(pair["drug"], pair["event"],
                           source=pair["source"], confidence=pair["confidence"])
            print(f"[Spark] Also written {len(all_pairs)} pairs to HBase")
        except Exception as e:
            print(f"[Spark] HBase write skipped: {e}")
```

### Step 6.2 — Update `backend/spark_pipeline.py`

Also write to SQLite for the frontend:
```python
        # Write to SQLite
        try:
            from database import insert_drug_event
            for pair in all_pairs:
                insert_drug_event(pair["drug"], pair["event"],
                                 source=pair["source"], confidence=pair["confidence"])
            print(f"[Spark] Also written {len(all_pairs)} pairs to SQLite")
        except Exception as e:
            print(f"[Spark] SQLite write skipped: {e}")
---

## PHASE 7: Real Drug Interaction Severity via DDInter (1–2 hours)

**Problem**: The current `/api/graph` shows co-prescription frequency (how often two drugs appear in the same report), NOT actual interaction danger. Metformin + Insulin shows as "critical" just because diabetics take both — not because it's dangerous.

**Solution**: Use the **DDInter** database (302,516 curated drug-drug interactions with clinical severity levels: Major / Moderate / Minor).

- **Source**: https://ddinter2.scbdd.com
- **License**: CC BY-NC-SA 4.0 (free for research/academic use)
- **Data**: CSV download with drug pairs + severity + mechanism + management

### Step 7.1 — Download DDInter data

1. Go to **https://ddinter2.scbdd.com/download/**
2. Download the DDI dataset (CSV format)
3. Place it in `backend/data/ddinter_interactions.csv`

The CSV should have columns like: `Drug_A`, `Drug_B`, `Level` (Major/Moderate/Minor), `Description`

### Step 7.2 — Create NEW file: `backend/ddi_lookup.py`

```python
"""
ddi_lookup.py — Drug-Drug Interaction severity lookup from DDInter database
Loads the DDInter CSV into a SQLite table for fast lookups.
"""
import sqlite3
import csv
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "pharmawatch.db")
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

def init_ddi_table():
    """Create DDI lookup table if it doesn't exist."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ddi_interactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            drug_a TEXT NOT NULL,
            drug_b TEXT NOT NULL,
            severity TEXT DEFAULT 'unknown',
            description TEXT,
            management TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ddi_a ON ddi_interactions(drug_a)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ddi_b ON ddi_interactions(drug_b)")
    conn.commit()
    conn.close()

def load_ddinter_csv(csv_filename="ddinter_interactions.csv"):
    """
    Import DDInter CSV into the ddi_interactions table.
    Run this ONCE after downloading the CSV.
    Adjust column names below to match the actual CSV headers.
    """
    csv_path = os.path.join(DATA_DIR, csv_filename)
    if not os.path.exists(csv_path):
        print(f"[DDI] CSV not found at {csv_path}")
        print(f"[DDI] Download from https://ddinter2.scbdd.com/download/")
        return 0

    conn = sqlite3.connect(DB_PATH)
    # Clear old data before reimporting
    conn.execute("DELETE FROM ddi_interactions")

    count = 0
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        # Print headers so you can verify column names
        print(f"[DDI] CSV headers: {reader.fieldnames}")

        for row in reader:
            # Adjust these keys to match your actual CSV column headers
            drug_a = row.get("Drug_A", row.get("drug_a", "")).strip().upper()
            drug_b = row.get("Drug_B", row.get("drug_b", "")).strip().upper()
            severity = row.get("Level", row.get("severity", "unknown")).strip().lower()
            description = row.get("Description", row.get("description", "")).strip()
            management = row.get("Management", row.get("management", "")).strip()

            if drug_a and drug_b:
                conn.execute(
                    "INSERT INTO ddi_interactions (drug_a, drug_b, severity, description, management) VALUES (?,?,?,?,?)",
                    (drug_a, drug_b, severity, description, management)
                )
                count += 1

    conn.commit()
    conn.close()
    print(f"[DDI] Loaded {count} interactions from {csv_path}")
    return count

def lookup_interaction(drug_a, drug_b):
    """
    Look up the interaction between two drugs.
    Checks both directions (A→B and B→A).
    Returns: {"severity": "major/moderate/minor", "description": "...", "management": "..."} or None
    """
    conn = sqlite3.connect(DB_PATH)
    a = drug_a.strip().upper()
    b = drug_b.strip().upper()

    row = conn.execute(
        "SELECT severity, description, management FROM ddi_interactions WHERE (drug_a=? AND drug_b=?) OR (drug_a=? AND drug_b=?) LIMIT 1",
        (a, b, b, a)
    ).fetchone()
    conn.close()

    if row:
        return {"severity": row[0], "description": row[1], "management": row[2]}
    return None

def get_all_interactions_for_drug(drug, limit=50):
    """Get all known interactions for a specific drug."""
    conn = sqlite3.connect(DB_PATH)
    d = drug.strip().upper()
    rows = conn.execute(
        "SELECT drug_a, drug_b, severity, description FROM ddi_interactions WHERE drug_a=? OR drug_b=? LIMIT ?",
        (d, d, limit)
    ).fetchall()
    conn.close()

    results = []
    for r in rows:
        other_drug = r[1] if r[0] == d else r[0]
        results.append({
            "drug": other_drug,
            "severity": r[2],
            "description": r[3]
        })
    return results

# Auto-init table on import
init_ddi_table()

if __name__ == "__main__":
    print("=== DDInter Loader ===")
    count = load_ddinter_csv()
    if count > 0:
        # Test lookup
        result = lookup_interaction("Metformin", "Warfarin")
        print(f"Metformin + Warfarin: {result}")
```

### Step 7.3 — Update `/api/graph` in `backend/app.py`

**Add import:**
```python
from ddi_lookup import lookup_interaction, get_all_interactions_for_drug
```

**Update the `/api/graph` route** to use real DDI severity instead of count-based thresholds:

```python
@app.route("/api/graph", methods=["GET"])
def get_interaction_graph():
    """
    Fetches co-prescribed drugs from openFDA, then cross-references
    each pair against the DDInter database for REAL clinical severity.
    """
    target_drug = request.args.get('drug', 'Metformin').upper()
    FDA_BASE = "https://api.fda.gov/drug/event.json"

    try:
        url = f'{FDA_BASE}?search=patient.drug.medicinalproduct:"{target_drug}"&count=patient.drug.medicinalproduct.exact&limit=15'
        response = http_requests.get(url)
        fda_data = response.json()

        nodes = [{"id": target_drug, "group": 1}]
        links = []

        if "results" in fda_data:
            for item in fda_data["results"]:
                co_drug = item["term"].upper()
                if co_drug == target_drug:
                    continue
                count = item["count"]

                # Look up REAL interaction severity from DDInter
                ddi = lookup_interaction(target_drug, co_drug)
                if ddi:
                    severity = ddi["severity"]
                    if severity == "major":
                        group = 2; risk = "critical"
                    elif severity == "moderate":
                        group = 3; risk = "moderate"
                    else:
                        group = 4; risk = "low"
                    description = ddi.get("description", "")
                else:
                    # No DDI record found — mark as unknown
                    group = 4; risk = "unknown"
                    description = "No interaction data in DDInter"

                value = max(1, count / 500)
                nodes.append({"id": co_drug, "group": group, "risk": risk,
                              "description": description})
                links.append({
                    "source": target_drug, "target": co_drug,
                    "value": value, "risk": risk
                })

        return jsonify({"nodes": nodes, "links": links})

    except Exception as e:
        return jsonify({"error": str(e)}), 500
```

### Step 7.4 — Load the DDInter data (one-time)

```bash
# After downloading the CSV to backend/data/ddinter_interactions.csv:
python backend/ddi_lookup.py
```

### Step 7.5 — Test

```bash
# Graph now shows real severity
curl "http://127.0.0.1:5000/api/graph?drug=Metformin"

# Expected: nodes with risk = "critical"/"moderate"/"low" based on
# actual DDInter clinical data, NOT just co-prescription frequency
```

---

## Architecture After All Phases

```
openFDA API ──┐
Reddit PRAW ──┼──► Spark Pipeline ──┬──► HBase (data lake)
Manual NER ───┘                     ├──► SQLite (frontend queries)
                                    ├──► Parquet files (historical archive)
                                    └──► LSTM Model (trained on accumulated data)

DDInter CSV ──► SQLite (ddi_interactions table) ──► /api/graph severity lookup
```

## Dependencies to Install

```bash
pip install praw happybase pyspark python-dotenv flask-limiter scikit-learn
```

## Prerequisites

| Tool | Download | Required For |
|------|----------|-------------|
| Docker Desktop | https://docker.com/products/docker-desktop/ | HBase |
| Java JDK 11 | https://adoptium.net/ | PySpark |
| Hadoop WinUtils | https://github.com/cdarlint/winutils | PySpark on Windows |
| DDInter CSV | https://ddinter2.scbdd.com/download/ | Real DDI severity |

## Files Summary

| File | Action | What |
|------|--------|------|
| `backend/lstm_model.py` | **NEW** | Real PyTorch LSTM — train + predict |
| `backend/hbase_store.py` | **NEW** | HBase storage layer via Thrift |
| `backend/spark_pipeline.py` | **NEW** | Spark batch + streaming pipeline |
| `backend/ddi_lookup.py` | **NEW** | DDInter drug interaction severity lookup |
| `backend/data/ddinter_interactions.csv` | **DOWNLOAD** | DDInter interaction database |
| `backend/.env` | **NEW** | Secrets (Reddit, FDA key, Flask key) |
| `backend/app.py` | **MODIFY** | New routes + real DDI severity in /api/graph |
| `backend/reddit_scraper.py` | **MODIFY** | Read credentials from env vars |

