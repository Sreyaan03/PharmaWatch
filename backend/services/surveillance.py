# backend/services/surveillance.py
# The alert detector and the surveillance feed.
#
# Alerts are POSSIBLE signals for review, never findings. Kinds:
#   new_signal     a drug-event pair became a signal in the latest quarter (it was not one before)
#   not_in_label   a new signal whose effect is not found in the drug's stored FDA label text
#   label_change   the drug's FDA label gained a boxed warning or new adverse-effect text
#
# The detector reads the Spark results (lake/results/signals_by_quarter); it computes no
# statistics. A quarter can bring tens of thousands of newly-signalling pairs, so alerts are
# created for the strongest (by IC025), with not-in-label ones kept first; the true total is
# recorded and shown. Every write is an upsert on (kind, rxcui, pt, quarter), so replaying the
# `signals-updated` event changes nothing.

import json
import logging

from db.mysql import execute, query, query_one
from services import label_check

log = logging.getLogger("surveillance")

KINDS = ("new_signal", "not_in_label", "label_change")
STATUSES = ("new", "reviewed", "dismissed", "escalated")
MAX_ALERTS_PER_RUN = 300             # how many signal alerts to create per quarter
CANDIDATE_LIMIT = 2000               # strongest newly-signalling pairs to examine for label status
# Product-use, administrative and dosing-error terms: reported often, but they describe how a
# product was used or recorded, not an adverse reaction. Left out of surveillance alerts.
NON_CLINICAL_LIKE = ["product%", "%off label%", "%drug ineffective%", "%use issue%", "%use in unapproved%",
                     "%wrong technique%", "%schedule of product%", "%incorrect dose%", "%dose omission%",
                     "%underdose%", "%overdose%", "%medication error%", "%device%", "%no adverse event%",
                     "%therapeutic response decreased%", "%drug administration error%", "%expired product%",
                     "%poor quality product%", "%storage%", "%contamination%"]
POSSIBLE_SIGNAL = ("Possible signal: needs review. A disproportionality signal is a reporting "
                   "pattern, not proof that the drug causes the effect.")


def _results_glob():
    import os

    from core import storage
    from services.signal_history import RESULTS
    if storage.backend().name != "local":
        return None
    return os.path.join(storage.backend().root("lake"), *RESULTS.split("/"), "*", "*.parquet").replace("\\", "/")


def _ingredient_names(con, rxcuis):
    if not rxcuis:
        return {}
    import duckdb

    from faers.registry import database_path
    try:
        idc = duckdb.connect(database_path(), read_only=True)
        marks = ", ".join("?" for _ in rxcuis)
        rows = idc.execute(f"SELECT DISTINCT ingredient_rxcui, ingredient_name FROM drug_identity "
                           f"WHERE ingredient_rxcui IN ({marks})", list(rxcuis)).fetchall()
        idc.close()
        return {str(r[0]): r[1] for r in rows}
    except duckdb.Error:
        return {}


def _newly_signalling(glob, quarter, previous, limit):
    """The strongest pairs that are a signal in `quarter` but were not in `previous`."""
    import duckdb
    con = duckdb.connect()
    try:
        rows = con.execute(f"""
            SELECT cur.rxcui, cur.pt_lower, cur.signal_strength, cur.ic025, cur.a, cur.prr, cur.ror_ci_low
            FROM (SELECT * FROM read_parquet('{glob}', hive_partitioning = 1) WHERE quarter = ?) cur
            LEFT JOIN (SELECT rxcui, pt_lower, signal_strength AS prev
                       FROM read_parquet('{glob}', hive_partitioning = 1) WHERE quarter = ?) p
              USING (rxcui, pt_lower)
            WHERE cur.signal_strength <> 'none' AND (p.prev IS NULL OR p.prev = 'none')
              AND NOT (%s)
            ORDER BY cur.ic025 DESC""" % " OR ".join("cur.pt_lower LIKE ?" for _ in NON_CLINICAL_LIKE),
            [quarter, previous] + NON_CLINICAL_LIKE).fetchall()
        total = len(rows)
        top = rows[:limit]
        names = _ingredient_names(con, list({r[0] for r in top}))
    finally:
        con.close()
    pt_names = _pt_names({r[1] for r in top})
    pairs = [{"rxcui": str(r[0]), "pt_lower": r[1], "pt": pt_names.get(r[1], r[1]),
              "signal_strength": r[2], "ic025": r[3], "cases": int(r[4]), "prr": r[5], "ror_ci_low": r[6],
              "ingredient": names.get(str(r[0]), str(r[0]))}         # fall back to rxcui/term when identity is absent
             for r in top]
    return pairs, total


def _pt_names(pt_lowers):
    """{lower-case term: Preferred Term} from the FAERS identity dictionary (DuckDB)."""
    if not pt_lowers:
        return {}
    import duckdb

    from faers.registry import database_path
    try:
        con = duckdb.connect(database_path(), read_only=True)
        marks = ", ".join("?" for _ in pt_lowers)
        rows = con.execute(f"SELECT pt_lower, pt FROM pt_dictionary WHERE pt_lower IN ({marks})",
                           list(pt_lowers)).fetchall()
        con.close()
        return {r[0]: r[1] for r in rows}
    except duckdb.Error:
        return {}


def upsert_alert(kind, rxcui, ingredient, pt, quarter, ic025=None, cases=None, detail=None):
    """Create or refresh one alert, keeping its review status. Returns 1 if a row was inserted."""
    before = query_one("SELECT id FROM surveillance_alerts WHERE kind = %s AND rxcui = %s AND pt = %s "
                       "AND quarter = %s", (kind, rxcui, pt, quarter))
    execute(
        "INSERT INTO surveillance_alerts (kind, rxcui, ingredient, pt, quarter, ic025, cases, detail_json) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
        "ON DUPLICATE KEY UPDATE ingredient = VALUES(ingredient), ic025 = VALUES(ic025), "
        "cases = VALUES(cases), detail_json = VALUES(detail_json)",
        (kind, rxcui, ingredient, pt, quarter, ic025, cases, json.dumps(detail or {})))
    return 0 if before else 1


def detect_new_signals(quarter=None, cap=MAX_ALERTS_PER_RUN, candidate_limit=CANDIDATE_LIMIT):
    """Raise alerts for pairs that became a signal in the latest quarter. Returns a summary."""
    glob = _results_glob()
    if glob is None:
        return {"error": "Spark results are not in local storage", "created": 0}
    import duckdb
    con = duckdb.connect()
    quarters = [r[0] for r in con.execute(
        f"SELECT DISTINCT quarter FROM read_parquet('{glob}', hive_partitioning = 1) ORDER BY 1").fetchall()]
    con.close()
    if len(quarters) < 2:
        return {"error": "need at least two quarters of results", "created": 0}
    quarter = quarter or quarters[-1]
    previous = quarters[quarters.index(quarter) - 1]
    pairs, total = _newly_signalling(glob, quarter, previous, candidate_limit)

    created = {"new_signal": 0, "not_in_label": 0}
    not_in_label, new_signal = [], []
    for p in pairs:
        check = label_check.find(p["rxcui"], p["pt"])
        p["label_check"] = check
        (not_in_label if check["status"] == "not_found_in_label_text" else new_signal).append(p)

    # not-in-label first, then strongest new signals, capped so one quarter cannot flood the feed
    for p in (not_in_label + new_signal)[:cap]:
        kind = "not_in_label" if p["label_check"]["status"] == "not_found_in_label_text" else "new_signal"
        detail = {"prr": round(p["prr"], 4), "ic025": round(p["ic025"], 4), "cases": p["cases"],
                  "ror_ci_low": round(p["ror_ci_low"], 4), "class": p["signal_strength"],
                  "became_signal_in": quarter, "was_not_a_signal_in": previous,
                  "label": p["label_check"], "note": POSSIBLE_SIGNAL}
        upsert_alert(kind, p["rxcui"], p["ingredient"], p["pt"], quarter,
                     round(p["ic025"], 4), p["cases"], detail)
        created[kind] += 1
    summary = {"quarter": quarter, "previous": previous, "newly_signalling_total": total,
               "candidates_examined": len(pairs), "created": sum(created.values()), "by_kind": created,
               "capped": total > cap}
    log.info(f"[surveillance] {quarter}: {total} newly signalling, created {summary['created']} alerts {created}")
    return summary


def handle_label_change(change):
    """A `label-change` event: raise a label_change alert and link it to open signal alerts for the
    same drug whose term now appears in the added text. Returns a summary."""
    rxcui = change.get("rxcui")
    if not rxcui:
        return {"created": 0}
    changed = change.get("changed_sections") or {}
    added_text = " ".join(s for sents in changed.values() for s in sents).lower()
    name = change.get("generic_name") or rxcui
    quarter = change.get("effective_time", "")
    detail = {"generic_name": name, "effective_time": change.get("effective_time"),
              "previous_effective_time": change.get("previous_effective_time"),
              "changed_sections": {k: v for k, v in changed.items()}, "note": POSSIBLE_SIGNAL}
    created = upsert_alert("label_change", rxcui, name, "", quarter, detail=detail)

    # link: an open not-in-label / new-signal alert for this drug whose term is now in the added text
    linked = 0
    for a in query("SELECT id, pt FROM surveillance_alerts WHERE rxcui = %s AND kind IN "
                   "('not_in_label', 'new_signal') AND status = 'new'", (rxcui,)):
        if a["pt"] and label_check.check_sections({"adverse_reactions": added_text}, a["pt"])["status"] == "in_label_text":
            execute("UPDATE surveillance_alerts SET detail_json = JSON_SET(COALESCE(detail_json, '{}'), "
                    "'$.label_caught_up', %s) WHERE id = %s",
                    (json.dumps({"effective_time": change.get("effective_time")}), a["id"]))
            linked += 1
    return {"created": created, "linked_existing_alerts": linked}


# ── consumer entry points (called from ingest.consumer) ───────────────────────
def on_signals_updated(msg):
    quarter = (msg or {}).get("latest_quarter")
    return detect_new_signals(quarter)


def on_label_change(msg):
    return handle_label_change(msg or {})


# ── feed, review, watchlist (read by routes) ──────────────────────────────────
def list_alerts(kind=None, status=None, drug=None, quarter=None, limit=200):
    where, params = ["1 = 1"], []
    if kind in KINDS:
        where.append("kind = %s"); params.append(kind)
    if status in STATUSES:
        where.append("status = %s"); params.append(status)
    if quarter:
        where.append("quarter = %s"); params.append(quarter)
    if drug:
        from services.identity import IdentityService
        res = IdentityService().resolve_drug(drug, live=False)
        rxcuis = [str(i["rxcui"]) for i in res.get("ingredients", [])]
        if not rxcuis:
            return {"alerts": [], "count": 0, "note": POSSIBLE_SIGNAL}, 200
        where.append("rxcui IN (%s)" % ", ".join(["%s"] * len(rxcuis))); params += rxcuis
    sql = ("SELECT id, kind, rxcui, ingredient, pt, quarter, ic025, cases, status, detail_json, "
           "created_at, reviewed_by, reviewed_at FROM surveillance_alerts WHERE " + " AND ".join(where) +
           " ORDER BY (kind = 'label_change') DESC, (kind = 'not_in_label') DESC, ic025 DESC LIMIT %s")
    try:
        rows = query(sql, tuple(params + [int(limit)]))
    except Exception:
        return {"error": "Alert store unavailable (MySQL is not reachable).", "alerts": []}, 503
    alerts = [{**{k: r[k] for k in ("id", "kind", "rxcui", "ingredient", "pt", "quarter", "ic025",
                                    "cases", "status", "reviewed_by")},
               "detail": json.loads(r["detail_json"]) if r["detail_json"] else {},
               "created_at": str(r["created_at"]), "reviewed_at": str(r["reviewed_at"]) if r["reviewed_at"] else None}
              for r in rows]
    return {"alerts": alerts, "count": len(alerts), "note": POSSIBLE_SIGNAL}, 200


def set_status(alert_id, status, reviewer=None):
    if status not in STATUSES:
        return {"error": f"status must be one of {STATUSES}"}, 400
    try:
        n = execute("UPDATE surveillance_alerts SET status = %s, reviewed_by = %s, reviewed_at = NOW() "
                    "WHERE id = %s", (status, reviewer, int(alert_id)))
    except Exception:
        return {"error": "Alert store unavailable."}, 503
    if not n:
        return {"error": "No such alert."}, 404
    return {"id": int(alert_id), "status": status}, 200


def counts():
    try:
        rows = query("SELECT kind, status, COUNT(*) AS n FROM surveillance_alerts GROUP BY kind, status")
    except Exception:
        return {}
    out = {}
    for r in rows:
        out.setdefault(r["kind"], {})[r["status"]] = int(r["n"])
    return out


# ── watchlist ─────────────────────────────────────────────────────────────────
def list_watchlist():
    try:
        rows = query("SELECT rxcui, ingredient, added_at FROM surveillance_watchlist ORDER BY added_at")
    except Exception:
        return {"error": "Watchlist store unavailable.", "drugs": []}, 503
    return {"drugs": [{"rxcui": r["rxcui"], "ingredient": r["ingredient"], "added_at": str(r["added_at"])}
                      for r in rows]}, 200


def add_to_watchlist(drug, added_by=None):
    from services.identity import IdentityService
    res = IdentityService().resolve_drug(drug, live=False)
    if not res.get("resolved") or len(res["ingredients"]) != 1:
        return {"error": f"'{drug}' did not resolve to a single drug ingredient."}, 422
    ing = res["ingredients"][0]
    try:
        execute("INSERT INTO surveillance_watchlist (rxcui, ingredient, added_by) VALUES (%s, %s, %s) "
                "ON DUPLICATE KEY UPDATE ingredient = VALUES(ingredient)",
                (str(ing["rxcui"]), ing["name"], added_by))
    except Exception:
        return {"error": "Watchlist store unavailable."}, 503
    return {"rxcui": str(ing["rxcui"]), "ingredient": ing["name"]}, 201


def remove_from_watchlist(rxcui):
    try:
        n = execute("DELETE FROM surveillance_watchlist WHERE rxcui = %s", (str(rxcui),))
    except Exception:
        return {"error": "Watchlist store unavailable."}, 503
    return ({"removed": str(rxcui)}, 200) if n else ({"error": "Not on the watchlist."}, 404)
