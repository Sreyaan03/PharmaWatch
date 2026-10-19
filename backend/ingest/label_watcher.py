# backend/ingest/label_watcher.py
# Watches the FDA labels of the drugs under surveillance. Once a day the producer fetches each
# watched drug's current label (safety sections) and compares it with the stored snapshot. When
# the label's version or effective time changes, the added sentences are published on the
# `label-change` topic; the surveillance consumer turns that into a reviewable alert and links it
# to any open signal for that drug whose term now appears in the label.
#
# The first fetch of a drug only stores a baseline and publishes nothing, so the feed does not
# flood the day the watcher is first run.

import json
import logging
import re

from clients.openfda import fetch_label_sections
from db.mysql import execute, query, query_one

log = logging.getLogger("label_watcher")

TOPIC = "label-change"
DEFAULT_WATCH_LIMIT = 150            # cap API calls per run to stay inside the key's quota
MIN_SENTENCE_LEN = 15
_SENT_SPLIT = re.compile(r"(?<=[.;])\s+|\n+")
_WS = re.compile(r"\s+")


def split_sentences(text):
    """A section's text as a list of trimmed sentences long enough to be meaningful."""
    out = []
    for part in _SENT_SPLIT.split(text or ""):
        s = _WS.sub(" ", part).strip().rstrip(".;: ").strip()
        if len(s) >= MIN_SENTENCE_LEN:
            out.append(s)
    return out


def _norm(sentence):
    return _WS.sub(" ", sentence.lower()).strip()


def diff_sections(old_sections, new_sections):
    """{section: [added sentences]} for sentences in new that were not in the old label.

    Only sections present in the new label are considered. A section that is entirely new counts
    as all of its sentences added.
    """
    changed = {}
    for section, text in (new_sections or {}).items():
        old_norm = {_norm(s) for s in split_sentences((old_sections or {}).get(section, ""))}
        added = [s for s in split_sentences(text) if _norm(s) not in old_norm]
        if added:
            changed[section] = added
    return changed


# ── which drugs to watch ──────────────────────────────────────────────────────
def watched_drugs(limit=DEFAULT_WATCH_LIMIT):
    """[(rxcui, ingredient)] to watch: everything on the watchlist, then the drugs with the
    strongest signals in the latest quarter, up to `limit`. De-duplicated, watchlist first."""
    seen, out = set(), []
    for r in query("SELECT rxcui, ingredient FROM surveillance_watchlist ORDER BY added_at"):
        if r["rxcui"] not in seen:
            seen.add(r["rxcui"])
            out.append((r["rxcui"], r["ingredient"]))
    for rxcui, ingredient in _top_signal_drugs(limit):
        if len(out) >= limit:
            break
        if rxcui not in seen:
            seen.add(rxcui)
            out.append((rxcui, ingredient))
    return out[:limit]


def _top_signal_drugs(limit):
    """[(rxcui, ingredient)] of the drugs with the most latest-quarter signals, from Spark results."""
    import duckdb

    from core import storage
    from services.signal_history import RESULTS
    if storage.backend().name != "local":
        return []
    glob = f"{storage.backend().root('lake')}/{RESULTS}/*/*.parquet".replace("\\", "/")
    try:
        con = duckdb.connect()
        latest = con.execute(f"SELECT max(quarter) FROM read_parquet('{glob}', hive_partitioning = 1)").fetchone()[0]
        rows = con.execute(
            f"SELECT rxcui, count(*) AS n FROM read_parquet('{glob}', hive_partitioning = 1) "
            f"WHERE quarter = ? AND signal_strength <> 'none' GROUP BY rxcui ORDER BY n DESC LIMIT ?",
            [latest, int(limit)]).fetchall()
        con.close()
    except duckdb.Error:
        return []
    names = _ingredient_names([r[0] for r in rows])
    return [(rxcui, names.get(rxcui, rxcui)) for rxcui, _ in rows if rxcui in names]


def _ingredient_names(rxcuis):
    """{rxcui: ingredient name} from the FAERS identity table."""
    if not rxcuis:
        return {}
    import duckdb

    from faers.registry import database_path
    try:
        con = duckdb.connect(database_path(), read_only=True)
        marks = ", ".join("?" for _ in rxcuis)
        rows = con.execute(f"SELECT DISTINCT ingredient_rxcui, ingredient_name FROM drug_identity "
                           f"WHERE ingredient_rxcui IN ({marks})", list(rxcuis)).fetchall()
        con.close()
    except duckdb.Error:
        return {}
    return {str(r[0]): r[1] for r in rows}


# ── fetching and diffing one drug ──────────────────────────────────────────────
def _stored(rxcui):
    row = query_one("SELECT set_id, version, effective_time, sections_json FROM label_snapshots WHERE rxcui = %s",
                    (rxcui,))
    if not row:
        return None
    row["sections"] = json.loads(row["sections_json"]) if row.get("sections_json") else {}
    return row


def _store(rxcui, label):
    execute(
        "INSERT INTO label_snapshots (rxcui, generic_name, set_id, version, effective_time, sections_json, fetched_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, NOW()) "
        "ON DUPLICATE KEY UPDATE generic_name = VALUES(generic_name), set_id = VALUES(set_id), "
        "version = VALUES(version), effective_time = VALUES(effective_time), sections_json = VALUES(sections_json), "
        "fetched_at = NOW()",
        (rxcui, label.get("generic_name"), label.get("set_id"), label.get("version"),
         label.get("effective_time"), json.dumps(label.get("sections", {}))))


def _version_changed(stored, label):
    return (stored["version"], stored["effective_time"]) != (label.get("version"), label.get("effective_time"))


def check_drug(rxcui, name, fetch=None, now_store=True):
    """Fetch the drug's label and compare with the snapshot. Returns the label-change payload to
    publish, or None (no label, first fetch, or no change). Updates the stored snapshot."""
    label = (fetch or fetch_label_sections)(name)
    if not label.get("found"):
        return None
    stored = _stored(rxcui)
    if now_store:
        _store(rxcui, label)
    if stored is None:                       # first time: baseline only, no event
        return None
    if not _version_changed(stored, label):
        return None
    changed = diff_sections(stored["sections"], label.get("sections", {}))
    if not changed:                          # version bumped but no new sentences
        return None
    return {
        "source": "fda_label", "rxcui": rxcui, "generic_name": label.get("generic_name"),
        "set_id": label.get("set_id"), "effective_time": label.get("effective_time"),
        "previous_effective_time": stored["effective_time"],
        "changed_sections": changed,
    }


def poll_labels(producer, limit=DEFAULT_WATCH_LIMIT, publish=None):
    """Check every watched drug once. Returns how many label-change events were published."""
    import time
    published = 0
    drugs = watched_drugs(limit)
    log.info(f"=== Checking FDA labels for {len(drugs)} watched drug(s) ===")
    for rxcui, name in drugs:
        try:
            change = check_drug(rxcui, name)
        except Exception as exc:
            log.error(f"label check failed for {name} ({rxcui}): {exc}")
            continue
        if change:
            (publish or _publish)(producer, change)
            published += 1
            log.info(f"→ [label-change] {name}: {list(change['changed_sections'])}")
        time.sleep(0.2)                      # be polite to openFDA
    log.info(f"Label check done — {published} change(s) published")
    return published


def _publish(producer, change):
    from ingest.producer import publish
    publish(producer, TOPIC, change["rxcui"], change)
