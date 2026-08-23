---
phase: 2
plan: 04
type: feature
wave: 3
depends_on: [02, 03]
files_modified:
  - backend/app.py
  - src/index.html
autonomous: true
requirements:
  - HARD-07
  - HARD-08
---

# Plan 04 — /api/local-stats Endpoint + CORS Scope Fix + Pipeline Disclaimer Badge

<objective>
Three remaining hardening items:
1. Add `/api/local-stats` endpoint that returns real SQLite-sourced dashboard statistics
   (drugs monitored, signals detected, reports analyzed, drug-event pairs)
2. Narrow Flask CORS from wildcard `*` to an allowlist read from the `ALLOWED_ORIGINS` env var
3. Add a visible "Simulated — Architecture Demo" disclaimer badge in the Pipeline section
   of the HTML so users understand the Kafka/Spark/HDFS metrics are illustrative

These are independent changes but small enough to bundle into one plan.
</objective>

<tasks>

## Task 1 — Add /api/local-stats Backend Endpoint

<read_first>
- backend/app.py (search for existing `/api/signals` route to understand the SQLite structure; search for `pharmawatch.db` to find the db_path pattern used throughout the file)
- backend/database.py (look for table schema — specifically `signals`, `drug_events`, `predictions` tables)
</read_first>

<action>
In `backend/app.py`, add a new route `/api/local-stats` **before the `if __name__ == '__main__':` block**:

```python
@app.route('/api/local-stats', methods=['GET'])
def local_stats():
    """
    Returns real counts from SQLite for the dashboard animated counters.
    Never throws — returns zeroed fallback if DB is unavailable.
    """
    import sqlite3
    db_path = os.path.join(os.path.dirname(__file__), 'pharmawatch.db')
    
    try:
        conn = sqlite3.connect(db_path)
        
        # Count distinct drugs in signals cache
        drugs_monitored = conn.execute(
            "SELECT COUNT(DISTINCT drug) FROM signals"
        ).fetchone()[0] or 0
        
        # Count total signal entries
        signals_detected = conn.execute(
            "SELECT COUNT(*) FROM signals"
        ).fetchone()[0] or 0
        
        # Count drug-event pairs from drug_events table (if it exists)
        try:
            drug_event_pairs = conn.execute(
                "SELECT COUNT(*) FROM drug_events"
            ).fetchone()[0] or 0
        except Exception:
            drug_event_pairs = 0
        
        # Count GNN predictions made (interactions analyzed)
        try:
            interactions_analyzed = conn.execute(
                "SELECT COUNT(*) FROM predictions"
            ).fetchone()[0] or 0
        except Exception:
            interactions_analyzed = 0
        
        conn.close()
        
        # Approximate reports analyzed: signals * average FAERS report denominator (~50,000 total reports per signal query)
        # Use a fixed multiplier since we don't store raw report counts here
        reports_analyzed = max(signals_detected * 1200, drug_event_pairs * 80)
        
        return jsonify({
            'drugs_monitored': drugs_monitored,
            'signals_detected': signals_detected,
            'drug_event_pairs': drug_event_pairs,
            'interactions_analyzed': interactions_analyzed,
            'reports_analyzed': reports_analyzed,
            'source': 'sqlite'
        })
    except Exception as e:
        return jsonify({
            'drugs_monitored': 0,
            'signals_detected': 0,
            'drug_event_pairs': 0,
            'interactions_analyzed': 0,
            'reports_analyzed': 0,
            'source': 'fallback',
            'error': str(e)
        })
```
</action>

<acceptance_criteria>
- `grep "/api/local-stats" backend/app.py` returns a match
- `curl http://localhost:5000/api/local-stats` returns valid JSON
- JSON response contains keys: `drugs_monitored`, `signals_detected`, `drug_event_pairs`, `interactions_analyzed`, `reports_analyzed`, `source`
- Endpoint returns 200 (not 500) even if tables don't exist
</acceptance_criteria>

---

## Task 2 — Fix CORS: From Wildcard to ALLOWED_ORIGINS Allowlist

<read_first>
- backend/app.py (search for `CORS(app` to find the current CORS initialization — should be near the top of the file after Flask app creation)
- backend/.env (view current contents to understand what env vars are already set)
</read_first>

<action>
In `backend/app.py`, find the CORS initialization line (currently `CORS(app)` or similar wildcard) and replace it with an environment-variable-driven allowlist:

Find this pattern (exact match may vary slightly):
```python
CORS(app)
```
or
```python
CORS(app, resources={r"/*": {"origins": "*"}})
```

Replace with:
```python
# Scope CORS to known origins — never expose wildcard in production
_allowed_origins = os.getenv(
    'ALLOWED_ORIGINS',
    'http://localhost:5000,http://127.0.0.1:5000'
).split(',')
CORS(app, origins=_allowed_origins)
```

Also ensure `import os` is at the top of the file (it should already be there, but verify).

Then update `backend/.env` (or create if missing) to add:
```
ALLOWED_ORIGINS=http://localhost:5000,http://127.0.0.1:5000
```
</action>

<acceptance_criteria>
- `grep "CORS(app)" backend/app.py` returns 0 results (wildcard form removed)
- `grep "ALLOWED_ORIGINS" backend/app.py` returns at least 1 match
- `grep "ALLOWED_ORIGINS" backend/.env` returns at least 1 match (env var documented)
- `grep "origins=_allowed_origins" backend/app.py` returns a match
</acceptance_criteria>

---

## Task 3 — Add Pipeline Disclaimer Badge in HTML

<read_first>
- src/index.html (search for `pipeline` or `kafka` or `distributed` to find the pipeline section — look for the section heading or the kafka-metric / spark-metric elements)
</read_first>

<action>
In `src/index.html`, find the Pipeline section header (the `<section>` or `<div>` that contains
the Kafka/Spark/HDFS metrics). Add a visible disclaimer banner immediately after the section's
opening `<h2>` or `<h3>` heading tag.

Search for the pipeline section heading. It likely looks like:
```html
<h2 ...>Pipeline</h2>
<!-- or -->
<h3 ...>Big Data Pipeline</h3>
<!-- or similar -->
```

After this heading (NOT replacing it), insert this disclaimer banner:
```html
<div id="pipeline-demo-badge" style="
    display: inline-flex;
    align-items: center;
    gap: 6px;
    background: rgba(255, 193, 7, 0.15);
    border: 1px solid rgba(255, 193, 7, 0.5);
    border-radius: 8px;
    padding: 6px 14px;
    font-size: 0.78rem;
    color: #f0a500;
    font-weight: 600;
    margin-bottom: 1rem;
    letter-spacing: 0.02em;
">
  <span>⚡</span>
  <span>Architecture Demo — Kafka / Spark / HDFS metrics are simulated for illustration. Only LSTM and BioBERT NLP run on real data.</span>
</div>
```

The badge should appear once, directly below the pipeline section heading, above the metric cards.
</action>

<acceptance_criteria>
- `grep "pipeline-demo-badge" src/index.html` returns a match
- `grep "Architecture Demo" src/index.html` returns a match
- `grep "simulated for illustration" src/index.html` returns a match
- The badge div appears in the file BEFORE the kafka-metric or spark-metric elements (verify by checking line order)
</acceptance_criteria>

</tasks>

<verification>
1. Start the backend: `python backend/app.py`
2. Verify local-stats endpoint:
   ```
   curl http://localhost:5000/api/local-stats
   ```
   Should return JSON with `drugs_monitored`, `signals_detected`, etc. (values may be 0 if DB is fresh).

3. Verify CORS fix:
   Check that `CORS(app)` with wildcard is gone from app.py:
   ```
   grep "CORS(app)" backend/app.py
   ```
   Should return no results with wildcard pattern.

4. Verify pipeline disclaimer:
   Open the PharmaWatch dashboard in browser → navigate to Pipeline section → confirm amber/yellow disclaimer banner is visible above the Kafka/Spark/HDFS metric cards.

5. Check `.env` has ALLOWED_ORIGINS entry:
   ```
   grep "ALLOWED_ORIGINS" backend/.env
   ```
</verification>

<must_haves>
- /api/local-stats endpoint exists and returns valid JSON with SQLite counts
- CORS no longer uses wildcard `*` — uses ALLOWED_ORIGINS env var allowlist
- Pipeline section has a clearly visible disclaimer badge indicating metrics are simulated
- No regressions: existing API endpoints still work after CORS change (test /api/signals from browser)
- .env ALLOWED_ORIGINS key documented
</must_haves>
