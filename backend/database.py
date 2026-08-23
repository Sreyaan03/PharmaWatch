"""
database.py — SQLite storage for PharmaWatch
Stores drug-event pairs, Reddit posts, and boxed warning violations.

A 'boxed_warning_violation' = a FAERS adverse event report received AFTER the
FDA boxed warning effective_time for that drug+event pair.  Each row represents
evidence that prescriptions/use continued despite the black box flag.
"""
import sqlite3
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "pharmawatch.db")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS drug_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            drug TEXT NOT NULL,
            event TEXT NOT NULL,
            source TEXT DEFAULT 'unknown',
            confidence REAL DEFAULT 1.0,
            raw_text TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_drug ON drug_events(drug);
        CREATE INDEX IF NOT EXISTS idx_event ON drug_events(event);
        CREATE INDEX IF NOT EXISTS idx_source ON drug_events(source);

        CREATE TABLE IF NOT EXISTS reddit_posts (
            id TEXT PRIMARY KEY,
            subreddit TEXT,
            title TEXT,
            body TEXT,
            score INTEGER,
            processed INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        -- Boxed Warning Violations:
        -- Each row = post-warning FAERS reports for a warned adverse event.
        -- faers_count_post_warning: number of FAERS reports for this event
        --   received AFTER the warning effective_time (= "violations").
        -- faers_count_pre_warning: reports BEFORE the warning (baseline).
        CREATE TABLE IF NOT EXISTS boxed_warning_violations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            drug TEXT NOT NULL,
            adverse_event TEXT NOT NULL,
            faers_count_post_warning INTEGER NOT NULL DEFAULT 0,
            faers_count_pre_warning  INTEGER NOT NULL DEFAULT 0,
            percentage_of_total REAL  NOT NULL DEFAULT 0,
            warning_effective_date TEXT,
            warning_text_snippet TEXT,
            detection_method TEXT DEFAULT 'post_warning_faers',
            queried_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_viol_drug
            ON boxed_warning_violations(drug);
        CREATE INDEX IF NOT EXISTS idx_viol_event
            ON boxed_warning_violations(adverse_event);

        -- openFDA Cache Table:
        CREATE TABLE IF NOT EXISTS fda_cache (
            url TEXT PRIMARY KEY,
            response_json TEXT NOT NULL,
            cached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        -- signals_cache Table:
        CREATE TABLE IF NOT EXISTS signals_cache (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            drug         TEXT NOT NULL,
            event        TEXT NOT NULL,
            a            INTEGER,  -- co-reports (drug+event)
            b            INTEGER,  -- drug only
            c            INTEGER,  -- event only
            d            INTEGER,  -- neither
            prr          REAL,
            ror          REAL,
            bcpnn_ic     REAL,
            n_reports    INTEGER,
            severity     TEXT,
            source       TEXT DEFAULT 'faers',
            computed_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_signal_pair ON signals_cache(drug, event);
    """)
    conn.commit()
    conn.close()
    print(f"[DB] Initialized at {DB_PATH}")

def insert_drug_event(drug, event, source="manual", confidence=1.0, raw_text=None):
    conn = get_db()
    conn.execute(
        "INSERT INTO drug_events (drug, event, source, confidence, raw_text) VALUES (?,?,?,?,?)",
        (drug.lower(), event.lower(), source, confidence, raw_text)
    )
    conn.commit()
    conn.close()

def get_drug_event_counts(drug, limit=10):
    conn = get_db()
    rows = conn.execute("""
        SELECT event, COUNT(*) as count
        FROM drug_events WHERE drug = ?
        GROUP BY event ORDER BY count DESC LIMIT ?
    """, (drug.lower(), limit)).fetchall()
    conn.close()
    return [{"event": r["event"], "count": r["count"]} for r in rows]

def get_prr_data(drug, event):
    conn = get_db()
    a = conn.execute("SELECT COUNT(*) FROM drug_events WHERE drug=? AND event=?",
                     (drug.lower(), event.lower())).fetchone()[0]
    ab = conn.execute("SELECT COUNT(*) FROM drug_events WHERE drug=?",
                      (drug.lower(),)).fetchone()[0]
    ac = conn.execute("SELECT COUNT(*) FROM drug_events WHERE event=?",
                      (event.lower(),)).fetchone()[0]
    total = conn.execute("SELECT COUNT(*) FROM drug_events").fetchone()[0]
    conn.close()
    return {"a": a, "b": ab - a, "c": ac - a, "d": total - ab - ac + a}

def insert_reddit_post(post_id, subreddit, title, body, score):
    conn = get_db()
    conn.execute(
        "INSERT OR IGNORE INTO reddit_posts (id, subreddit, title, body, score) VALUES (?,?,?,?,?)",
        (post_id, subreddit, title, body, score)
    )
    conn.commit()
    conn.close()

def get_unprocessed_posts(limit=50):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM reddit_posts WHERE processed=0 LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return rows

def mark_post_processed(post_id):
    conn = get_db()
    conn.execute("UPDATE reddit_posts SET processed=1 WHERE id=?", (post_id,))
    conn.commit()
    conn.close()


# ── Boxed Warning Violations ─────────────────────────────────────────────────

def insert_violation(drug, adverse_event, faers_count_post_warning,
                     faers_count_pre_warning, percentage_of_total,
                     warning_effective_date=None, warning_text_snippet=None,
                     detection_method="post_warning_faers"):
    """
    Upsert a violation record.  If the drug+event pair already exists,
    update the counts (newer query wins — keeps data fresh).
    """
    conn = get_db()
    existing = conn.execute(
        "SELECT id FROM boxed_warning_violations WHERE drug=? AND adverse_event=?",
        (drug.lower(), adverse_event.lower())
    ).fetchone()

    if existing:
        conn.execute("""
            UPDATE boxed_warning_violations
               SET faers_count_post_warning = ?,
                   faers_count_pre_warning  = ?,
                   percentage_of_total      = ?,
                   warning_effective_date   = ?,
                   warning_text_snippet     = ?,
                   detection_method         = ?,
                   queried_at               = CURRENT_TIMESTAMP
             WHERE id = ?
        """, (faers_count_post_warning, faers_count_pre_warning,
               percentage_of_total, warning_effective_date,
               warning_text_snippet, detection_method, existing["id"]))
    else:
        conn.execute("""
            INSERT INTO boxed_warning_violations
              (drug, adverse_event, faers_count_post_warning,
               faers_count_pre_warning, percentage_of_total,
               warning_effective_date, warning_text_snippet, detection_method)
            VALUES (?,?,?,?,?,?,?,?)
        """, (drug.lower(), adverse_event.lower(),
               faers_count_post_warning, faers_count_pre_warning,
               percentage_of_total, warning_effective_date,
               warning_text_snippet, detection_method))
    conn.commit()
    conn.close()


def get_violations(drug):
    """Return all stored violations for a specific drug (most recent first)."""
    conn = get_db()
    rows = conn.execute("""
        SELECT drug, adverse_event, faers_count_post_warning,
               faers_count_pre_warning, percentage_of_total,
               warning_effective_date, warning_text_snippet,
               detection_method, queried_at
          FROM boxed_warning_violations
         WHERE drug = ?
         ORDER BY faers_count_post_warning DESC
    """, (drug.lower(),)).fetchall()
    conn.close()
    return [
        {
            "drug":                    r["drug"],
            "adverse_event":           r["adverse_event"],
            "faers_count_post_warning":r["faers_count_post_warning"],
            "faers_count_pre_warning": r["faers_count_pre_warning"],
            "percentage_of_total":     r["percentage_of_total"],
            "warning_effective_date":  r["warning_effective_date"],
            "warning_text_snippet":    r["warning_text_snippet"],
            "detection_method":        r["detection_method"],
            "queried_at":              r["queried_at"],
        }
        for r in rows
    ]


def get_all_violations(limit=200):
    """Return the most recent violations across all drugs."""
    conn = get_db()
    rows = conn.execute("""
        SELECT drug, adverse_event, faers_count_post_warning,
               faers_count_pre_warning, percentage_of_total,
               warning_effective_date, queried_at
          FROM boxed_warning_violations
         ORDER BY queried_at DESC, faers_count_post_warning DESC
         LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [
        {
            "drug":                    r["drug"],
            "adverse_event":           r["adverse_event"],
            "faers_count_post_warning":r["faers_count_post_warning"],
            "faers_count_pre_warning": r["faers_count_pre_warning"],
            "percentage_of_total":     r["percentage_of_total"],
            "warning_effective_date":  r["warning_effective_date"],
            "queried_at":              r["queried_at"],
        }
        for r in rows
    ]


init_db()


# ── Interaction Predictions ───────────────────────────────────────────────────

def init_interaction_predictions_table():
    """
    Creates the interaction_predictions table for caching GNN results locally.
    Pairs are stored alphabetically (drug_a <= drug_b) so (A,B) == (B,A).
    """
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS interaction_predictions (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            drug_a           TEXT NOT NULL,
            drug_b           TEXT NOT NULL,
            smiles_a         TEXT,
            smiles_b         TEXT,
            cid_a            TEXT,
            cid_b            TEXT,
            is_harmful       INTEGER NOT NULL DEFAULT 0,
            confidence       REAL    NOT NULL DEFAULT 0.0,
            top_side_effect  TEXT,
            all_side_effects TEXT,
            source           TEXT    NOT NULL DEFAULT 'gnn',
            queried_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_pred_pair
            ON interaction_predictions(drug_a, drug_b);
        CREATE INDEX IF NOT EXISTS idx_pred_drug_a
            ON interaction_predictions(drug_a);
        CREATE INDEX IF NOT EXISTS idx_pred_drug_b
            ON interaction_predictions(drug_b);
    """)
    conn.commit()
    conn.close()
    print("[DB] interaction_predictions table ready")


def _normalize_pair(drug_a, drug_b):
    """Return (a, b) sorted alphabetically so pair order is always consistent."""
    a, b = drug_a.strip().lower(), drug_b.strip().lower()
    return (a, b) if a <= b else (b, a)


def upsert_prediction(drug_a, drug_b, is_harmful, confidence,
                      smiles_a=None, smiles_b=None,
                      cid_a=None, cid_b=None,
                      top_side_effect=None, all_side_effects=None,
                      source='gnn'):
    """
    Insert or update a GNN prediction for a drug pair.
    Pair is stored alphabetically so (Warfarin, Aspirin) == (Aspirin, Warfarin).
    all_side_effects should be a list or JSON string.
    """
    import json
    da, db = _normalize_pair(drug_a, drug_b)
    # Keep smiles/cid aligned with the normalized order
    if drug_a.strip().lower() != da:
        smiles_a, smiles_b = smiles_b, smiles_a
        cid_a, cid_b = cid_b, cid_a

    if isinstance(all_side_effects, list):
        all_side_effects = json.dumps(all_side_effects)

    conn = get_db()
    existing = conn.execute(
        "SELECT id FROM interaction_predictions WHERE drug_a=? AND drug_b=?",
        (da, db)
    ).fetchone()

    if existing:
        conn.execute("""
            UPDATE interaction_predictions
               SET smiles_a=?, smiles_b=?, cid_a=?, cid_b=?,
                   is_harmful=?, confidence=?, top_side_effect=?,
                   all_side_effects=?, source=?,
                   queried_at=CURRENT_TIMESTAMP
             WHERE id=?
        """, (smiles_a, smiles_b, cid_a, cid_b,
              int(is_harmful), float(confidence),
              top_side_effect, all_side_effects, source,
              existing["id"]))
    else:
        conn.execute("""
            INSERT INTO interaction_predictions
              (drug_a, drug_b, smiles_a, smiles_b, cid_a, cid_b,
               is_harmful, confidence, top_side_effect, all_side_effects, source)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (da, db, smiles_a, smiles_b, cid_a, cid_b,
              int(is_harmful), float(confidence),
              top_side_effect, all_side_effects, source))
    conn.commit()
    conn.close()


def get_prediction(drug_a, drug_b):
    """
    Fetch a cached prediction for a drug pair (order-insensitive).
    Returns a dict or None if not cached.
    """
    import json
    da, db = _normalize_pair(drug_a, drug_b)
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM interaction_predictions WHERE drug_a=? AND drug_b=?",
        (da, db)
    ).fetchone()
    conn.close()
    if not row:
        return None
    result = dict(row)
    if result.get("all_side_effects"):
        try:
            result["all_side_effects"] = json.loads(result["all_side_effects"])
        except Exception:
            result["all_side_effects"] = []
    return result


def get_recent_predictions(limit=20):
    """Return the most recently queried predictions across all drug pairs."""
    conn = get_db()
    rows = conn.execute("""
        SELECT drug_a, drug_b, is_harmful, confidence,
               top_side_effect, source, queried_at
          FROM interaction_predictions
         ORDER BY queried_at DESC
         LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# Initialise the new table on import
init_interaction_predictions_table()


# ── openFDA Cache Helpers ─────────────────────────────────────────────────────

def get_cached_fda_response(url):
    """Retrieve cached JSON response for a URL if it is less than 7 days old."""
    import json
    from datetime import datetime
    conn = get_db()
    row = conn.execute("""
        SELECT response_json, cached_at 
          FROM fda_cache 
         WHERE url = ?
    """, (url,)).fetchone()
    conn.close()
    if row:
        try:
            cached_time = datetime.strptime(row["cached_at"], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            try:
                cached_time = datetime.fromisoformat(row["cached_at"].rstrip("Z"))
            except ValueError:
                return None
        delta = datetime.utcnow() - cached_time
        if delta.days < 7:
            return json.loads(row["response_json"])
    return None

def cache_fda_response(url, response_json):
    """Upsert response_json into fda_cache table."""
    import json
    conn = get_db()
    response_str = json.dumps(response_json)
    conn.execute("""
        INSERT OR REPLACE INTO fda_cache (url, response_json, cached_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
    """, (url, response_str))
    conn.commit()
    conn.close()


# ── signals_cache Helpers ─────────────────────────────────────────────────────

def upsert_signal(drug, event, a, b, c, d, prr, ror, bcpnn_ic, n_reports, severity, source='faers'):
    """Upsert a computed signal into signals_cache table."""
    conn = get_db()
    # Try inserting. If unique index conflicts, update the values.
    # Note: SQLite supports ON CONFLICT from version 3.24.0
    conn.execute("""
        INSERT INTO signals_cache (drug, event, a, b, c, d, prr, ror, bcpnn_ic, n_reports, severity, source, computed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(drug, event) DO UPDATE SET
            a = excluded.a,
            b = excluded.b,
            c = excluded.c,
            d = excluded.d,
            prr = excluded.prr,
            ror = excluded.ror,
            bcpnn_ic = excluded.bcpnn_ic,
            n_reports = excluded.n_reports,
            severity = excluded.severity,
            source = excluded.source,
            computed_at = CURRENT_TIMESTAMP
    """, (drug.lower(), event.lower(), a, b, c, d, prr, ror, bcpnn_ic, n_reports, severity, source))
    conn.commit()
    conn.close()


def get_cached_signals(limit=100):
    """Fetch all cached signals from signals_cache."""
    conn = get_db()
    rows = conn.execute("""
        SELECT drug, event, a, b, c, d, prr, ror, bcpnn_ic, n_reports, severity, source, computed_at
          FROM signals_cache
         ORDER BY prr DESC
         LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_signal(drug, event):
    """Fetch a single cached signal."""
    conn = get_db()
    row = conn.execute("""
        SELECT drug, event, a, b, c, d, prr, ror, bcpnn_ic, n_reports, severity, source, computed_at
          FROM signals_cache
         WHERE drug = ? AND event = ?
    """, (drug.lower(), event.lower())).fetchone()
    conn.close()
    return dict(row) if row else None

