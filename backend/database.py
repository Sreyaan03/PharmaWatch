"""
database.py — Production MySQL Database Adapter for PharmaWatch
Provides thread-safe access to drug events, Reddit posts, boxed warnings,
GNN predictions, openFDA caching, and disproportionality signals.
"""

import json
import hashlib
import logging
from db.mysql import query, query_one, execute, ping

log = logging.getLogger("pharmawatch.database")


def init_db():
    """
    Verifies MySQL connectivity on application startup.
    Detailed DDL migrations are handled idempotently via backend/db/migrate.py.
    """
    try:
        status = ping()
        log.info(f"[DB] Connected to MySQL database '{status['database']}' ({status['table_count']} tables present).")
    except Exception as e:
        log.warning(f"[DB] MySQL connection check: {e}. If MySQL is not running yet, start the container or configure .env.")


def insert_drug_event(drug, event, source="manual", confidence=1.0, raw_text=None):
    """Insert a raw drug-adverse event pair."""
    raw_hash = hashlib.md5(raw_text.encode("utf-8")).hexdigest() if raw_text else None
    sql = """
        INSERT INTO drug_events (drug, event, source, confidence, raw_hash, raw_text, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, NOW())
        ON DUPLICATE KEY UPDATE confidence = VALUES(confidence)
    """
    try:
        execute(sql, (drug.strip().lower(), event.strip().lower(), source, float(confidence), raw_hash, raw_text))
    except Exception as e:
        log.error(f"[DB] insert_drug_event failed: {e}")


def get_drug_event_counts(drug, limit=10):
    """Return top adverse events and occurrence counts for a drug."""
    sql = """
        SELECT event, COUNT(*) AS count
        FROM drug_events
        WHERE drug = %s
        GROUP BY event
        ORDER BY count DESC
        LIMIT %s
    """
    try:
        return query(sql, (drug.strip().lower(), int(limit)))
    except Exception as e:
        log.error(f"[DB] get_drug_event_counts failed: {e}")
        return []


def get_prr_data(drug, event):
    """
    Compute 2x2 contingency table counts from drug_events:
      a  = drug + event
      b  = drug only
      c  = event only
      d  = all other reports
    """
    d_clean = drug.strip().lower()
    e_clean = event.strip().lower()
    try:
        row_a = query_one("SELECT COUNT(*) AS cnt FROM drug_events WHERE drug = %s AND event = %s", (d_clean, e_clean))
        row_ab = query_one("SELECT COUNT(*) AS cnt FROM drug_events WHERE drug = %s", (d_clean,))
        row_ac = query_one("SELECT COUNT(*) AS cnt FROM drug_events WHERE event = %s", (e_clean,))
        row_tot = query_one("SELECT COUNT(*) AS cnt FROM drug_events")

        a = int(row_a["cnt"]) if row_a else 0
        ab = int(row_ab["cnt"]) if row_ab else 0
        ac = int(row_ac["cnt"]) if row_ac else 0
        total = int(row_tot["cnt"]) if row_tot else 0

        return {
            "a": a,
            "b": max(0, ab - a),
            "c": max(0, ac - a),
            "d": max(0, total - ab - ac + a)
        }
    except Exception as e:
        log.error(f"[DB] get_prr_data failed: {e}")
        return {"a": 0, "b": 0, "c": 0, "d": 0}


# ── Reddit Mining Helpers ───────────────────────────────────────────────────

def insert_reddit_post(post_id, subreddit, title, body, score):
    """Insert a Reddit post if not already processed."""
    sql = """
        INSERT IGNORE INTO reddit_posts (id, subreddit, title, body, score, processed, created_at)
        VALUES (%s, %s, %s, %s, %s, 0, NOW())
    """
    try:
        execute(sql, (post_id, subreddit, title, body, int(score or 0)))
    except Exception as e:
        log.error(f"[DB] insert_reddit_post failed: {e}")


def get_unprocessed_posts(limit=50):
    """Fetch unprocessed Reddit posts for NER extraction."""
    sql = "SELECT * FROM reddit_posts WHERE processed = 0 LIMIT %s"
    try:
        return query(sql, (int(limit),))
    except Exception as e:
        log.error(f"[DB] get_unprocessed_posts failed: {e}")
        return []


def mark_post_processed(post_id):
    """Mark a Reddit post as processed."""
    sql = "UPDATE reddit_posts SET processed = 1 WHERE id = %s"
    try:
        execute(sql, (post_id,))
    except Exception as e:
        log.error(f"[DB] mark_post_processed failed: {e}")


# ── Boxed Warning Violations ─────────────────────────────────────────────────

def insert_violation(drug, adverse_event, faers_count_post_warning,
                     faers_count_pre_warning, percentage_of_total,
                     warning_effective_date=None, warning_text_snippet=None,
                     detection_method="post_warning_faers"):
    """Upsert a post-warning violation record into MySQL."""
    sql = """
        INSERT INTO boxed_warning_violations
          (drug, adverse_event, faers_count_post_warning, faers_count_pre_warning,
           percentage_of_total, warning_effective_date, warning_text_snippet, detection_method, queried_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
        ON DUPLICATE KEY UPDATE
          faers_count_post_warning = VALUES(faers_count_post_warning),
          faers_count_pre_warning  = VALUES(faers_count_pre_warning),
          percentage_of_total      = VALUES(percentage_of_total),
          warning_effective_date   = VALUES(warning_effective_date),
          warning_text_snippet     = VALUES(warning_text_snippet),
          detection_method         = VALUES(detection_method),
          queried_at               = NOW()
    """
    try:
        execute(sql, (
            drug.strip().lower(), adverse_event.strip().lower(),
            int(faers_count_post_warning or 0), int(faers_count_pre_warning or 0),
            float(percentage_of_total or 0.0), warning_effective_date,
            warning_text_snippet, detection_method
        ))
    except Exception as e:
        log.error(f"[DB] insert_violation failed: {e}")


def get_violations(drug):
    """Return all stored violations for a drug, highest violation count first."""
    sql = """
        SELECT drug, adverse_event, faers_count_post_warning, faers_count_pre_warning,
               percentage_of_total, warning_effective_date, warning_text_snippet,
               detection_method, queried_at
        FROM boxed_warning_violations
        WHERE drug = %s
        ORDER BY faers_count_post_warning DESC
    """
    try:
        return query(sql, (drug.strip().lower(),))
    except Exception as e:
        log.error(f"[DB] get_violations failed: {e}")
        return []


def get_all_violations(limit=200):
    """Return the most recent violations across all drugs."""
    sql = """
        SELECT drug, adverse_event, faers_count_post_warning, faers_count_pre_warning,
               percentage_of_total, warning_effective_date, queried_at
        FROM boxed_warning_violations
        ORDER BY queried_at DESC, faers_count_post_warning DESC
        LIMIT %s
    """
    try:
        return query(sql, (int(limit),))
    except Exception as e:
        log.error(f"[DB] get_all_violations failed: {e}")
        return []


# ── Interaction Predictions (GNN Cache) ──────────────────────────────────────

def _normalize_pair(drug_a, drug_b):
    """Normalize drug pair alphabetically so (A, B) == (B, A)."""
    a, b = drug_a.strip().lower(), drug_b.strip().lower()
    return (a, b) if a <= b else (b, a)


def init_interaction_predictions_table():
    """Retained for backward compatibility. Schema managed via migrate.py."""
    pass


def upsert_prediction(drug_a, drug_b, is_harmful, confidence,
                      smiles_a=None, smiles_b=None,
                      cid_a=None, cid_b=None,
                      top_side_effect=None, all_side_effects=None,
                      source='gnn'):
    """Upsert GNN prediction results for a normalized drug pair."""
    da, db = _normalize_pair(drug_a, drug_b)
    if drug_a.strip().lower() != da:
        smiles_a, smiles_b = smiles_b, smiles_a
        cid_a, cid_b = cid_b, cid_a

    if isinstance(all_side_effects, list):
        all_side_effects = json.dumps(all_side_effects)

    sql = """
        INSERT INTO interaction_predictions
          (drug_a, drug_b, smiles_a, smiles_b, cid_a, cid_b,
           is_harmful, confidence, top_side_effect, all_side_effects, source, queried_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
        ON DUPLICATE KEY UPDATE
          smiles_a = VALUES(smiles_a),
          smiles_b = VALUES(smiles_b),
          cid_a = VALUES(cid_a),
          cid_b = VALUES(cid_b),
          is_harmful = VALUES(is_harmful),
          confidence = VALUES(confidence),
          top_side_effect = VALUES(top_side_effect),
          all_side_effects = VALUES(all_side_effects),
          source = VALUES(source),
          queried_at = NOW()
    """
    try:
        execute(sql, (
            da, db, smiles_a, smiles_b, str(cid_a) if cid_a else None,
            str(cid_b) if cid_b else None, int(bool(is_harmful)),
            float(confidence or 0.0), top_side_effect, all_side_effects, source
        ))
    except Exception as e:
        log.error(f"[DB] upsert_prediction failed: {e}")


def get_prediction(drug_a, drug_b):
    """Retrieve cached GNN prediction for a drug pair (order-insensitive)."""
    da, db = _normalize_pair(drug_a, drug_b)
    sql = "SELECT * FROM interaction_predictions WHERE drug_a = %s AND drug_b = %s"
    try:
        row = query_one(sql, (da, db))
        if row and row.get("all_side_effects"):
            try:
                row["all_side_effects"] = json.loads(row["all_side_effects"])
            except Exception:
                row["all_side_effects"] = []
        return row
    except Exception as e:
        log.error(f"[DB] get_prediction failed: {e}")
        return None


def get_recent_predictions(limit=20):
    """Return recently queried GNN interaction predictions."""
    sql = """
        SELECT drug_a, drug_b, is_harmful, confidence, top_side_effect, source, queried_at
        FROM interaction_predictions
        ORDER BY queried_at DESC
        LIMIT %s
    """
    try:
        return query(sql, (int(limit),))
    except Exception as e:
        log.error(f"[DB] get_recent_predictions failed: {e}")
        return []


# ── Disproportionality Signals Cache Helpers ─────────────────────────────────

def upsert_signal(drug, event, metrics, source="faers"):
    """Upsert computed disproportionality metrics into signals_cache."""
    sql = """
        INSERT INTO signals_cache (
            drug, event, a, b, c, d, prr, prr_ci_low, prr_ci_high, ror, ror_ci_low, ror_ci_high,
            chi2, bcpnn_ic, ic025, n_reports, signal_strength, source, computed_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
        ON DUPLICATE KEY UPDATE
            a = VALUES(a), b = VALUES(b), c = VALUES(c), d = VALUES(d),
            prr = VALUES(prr), prr_ci_low = VALUES(prr_ci_low), prr_ci_high = VALUES(prr_ci_high),
            ror = VALUES(ror), ror_ci_low = VALUES(ror_ci_low), ror_ci_high = VALUES(ror_ci_high),
            chi2 = VALUES(chi2), bcpnn_ic = VALUES(bcpnn_ic), ic025 = VALUES(ic025),
            n_reports = VALUES(n_reports), signal_strength = VALUES(signal_strength),
            source = VALUES(source), computed_at = NOW()
    """
    try:
        execute(sql, (
            drug.strip().lower(), event.strip().lower(),
            int(metrics.get("a", 0)), int(metrics.get("b", 0)),
            int(metrics.get("c", 0)), int(metrics.get("d", 0)),
            float(metrics["prr"]) if metrics.get("prr") is not None else None,
            float(metrics["prr_ci_low"]) if metrics.get("prr_ci_low") is not None else None,
            float(metrics["prr_ci_high"]) if metrics.get("prr_ci_high") is not None else None,
            float(metrics["ror"]) if metrics.get("ror") is not None else None,
            float(metrics["ror_ci_low"]) if metrics.get("ror_ci_low") is not None else None,
            float(metrics["ror_ci_high"]) if metrics.get("ror_ci_high") is not None else None,
            float(metrics["chi2"]) if metrics.get("chi2") is not None else None,
            float(metrics["bcpnn_ic"]) if metrics.get("bcpnn_ic") is not None else None,
            float(metrics["ic025"]) if metrics.get("ic025") is not None else None,
            int(metrics.get("n_reports", 0)),
            metrics.get("signal_strength", "None"),
            source
        ))
    except Exception as e:
        log.error(f"[DB] upsert_signal failed: {e}")


def get_cached_signals(limit=100):
    """Retrieve top cached disproportionality signals sorted by PRR."""
    sql = "SELECT * FROM signals_cache ORDER BY prr DESC LIMIT %s"
    try:
        return query(sql, (int(limit),))
    except Exception as e:
        log.error(f"[DB] get_cached_signals failed: {e}")
        return []


def get_signal(drug, event):
    """Retrieve cached signal for a specific drug and event pair."""
    sql = "SELECT * FROM signals_cache WHERE drug = %s AND event = %s LIMIT 1"
    try:
        return query_one(sql, (drug.strip().lower(), event.strip().lower()))
    except Exception as e:
        log.error(f"[DB] get_signal failed: {e}")
        return None
