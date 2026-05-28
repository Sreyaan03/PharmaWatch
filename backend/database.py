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
