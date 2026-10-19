"""
The alert detector: a pair that newly becomes a signal gives exactly one alert, replaying gives
none, not-in-label is distinguished from new-signal by the stored label, a label change raises an
alert and links to an open signal alert, and the consumer computes no statistics.
"""

import json
import os
import sys

import pytest

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from bigdata import timing  # noqa: E402
from services import surveillance as sv  # noqa: E402


def _mysql_ready():
    try:
        from db.mysql import query_one
        return query_one("SELECT COUNT(*) AS n FROM surveillance_alerts") is not None
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _mysql_ready(), reason="MySQL with the surveillance tables is not reachable")


@pytest.fixture
def results(small_lake):
    """Spark-shaped results for the small FAERS data in the lake; a clean alert table."""
    from db.mysql import execute
    con, _ = timing.run_duckdb(small_lake["lake"])
    out = os.path.join(small_lake["data_dir"], "lake", "results", "signals_by_quarter")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    con.execute(f"COPY results TO '{out}' (FORMAT PARQUET, PARTITION_BY (quarter))")
    con.close()
    execute("DELETE FROM surveillance_alerts WHERE rxcui IN ('111', '222', '333')")
    execute("DELETE FROM label_snapshots WHERE rxcui IN ('111', '222', '333')")
    yield small_lake
    execute("DELETE FROM surveillance_alerts WHERE rxcui IN ('111', '222', '333')")
    execute("DELETE FROM label_snapshots WHERE rxcui IN ('111', '222', '333')")


def _alerts(rxcui=None):
    from db.mysql import query
    where = f"WHERE rxcui = '{rxcui}'" if rxcui else "WHERE rxcui IN ('111','222','333')"
    return query(f"SELECT kind, rxcui, pt, quarter, status, detail_json FROM surveillance_alerts {where}")


def test_a_newly_signalling_pair_gives_one_alert_and_replay_gives_none(results):
    first = sv.detect_new_signals(quarter='2024q2')
    assert first["created"] >= 1 and first["by_kind"]["new_signal"] >= 1
    rows1 = _alerts()
    from db.mysql import query_one
    stamp = query_one("SELECT MAX(created_at) AS m FROM surveillance_alerts").get("m")
    second = sv.detect_new_signals(quarter='2024q2')             # replay the same quarter
    rows2 = _alerts()
    assert len(rows2) == len(rows1)                            # no new rows
    assert query_one("SELECT MAX(created_at) AS m FROM surveillance_alerts").get("m") == stamp


def test_not_in_label_is_distinguished_from_new_signal(results):
    from db.mysql import execute
    # 111 has a strong rhabdomyolysis signal; store a label that does NOT mention it
    execute("INSERT INTO label_snapshots (rxcui, generic_name, version, effective_time, sections_json) "
            "VALUES ('111', 'statinol', '1', '20250101', %s) ON DUPLICATE KEY UPDATE sections_json = VALUES(sections_json)",
            (json.dumps({"adverse_reactions": "The most common reaction is nausea."}),))
    sv.detect_new_signals(quarter='2024q2')
    kinds = {(a["pt"].lower(), a["kind"]) for a in _alerts("111")}
    assert ("rhabdomyolysis", "not_in_label") in kinds        # signal, and not in the stored label


def test_in_label_effect_is_a_plain_new_signal(results):
    from db.mysql import execute
    execute("INSERT INTO label_snapshots (rxcui, generic_name, version, effective_time, sections_json) "
            "VALUES ('111', 'statinol', '1', '20250101', %s) ON DUPLICATE KEY UPDATE sections_json = VALUES(sections_json)",
            (json.dumps({"warnings_and_cautions": "Rhabdomyolysis has been reported."}),))
    sv.detect_new_signals(quarter='2024q2')
    rhabdo = [a for a in _alerts("111") if a["pt"].lower() == "rhabdomyolysis"]
    assert rhabdo and rhabdo[0]["kind"] == "new_signal"        # it IS in the label, so just a new signal


def test_alerts_carry_the_possible_signal_wording_and_evidence(results):
    sv.detect_new_signals(quarter='2024q2')
    a = _alerts("111")[0]
    detail = json.loads(a["detail_json"])
    assert "reporting pattern" in detail["note"] and "not proof" in detail["note"]
    assert "ic025" in detail and "cases" in detail and detail["became_signal_in"]


def test_a_label_change_raises_an_alert_and_links_an_open_signal(results):
    from db.mysql import execute, query_one
    # an open not-in-label alert for rhabdomyolysis on drug 111
    execute("INSERT INTO surveillance_alerts (kind, rxcui, ingredient, pt, quarter, ic025, cases, detail_json) "
            "VALUES ('not_in_label', '111', 'statinol', 'Rhabdomyolysis', '2024q3', 5.0, 12, '{}')")
    change = {"rxcui": "111", "generic_name": "statinol", "effective_time": "20250601",
              "previous_effective_time": "20240101",
              "changed_sections": {"warnings_and_cautions": ["Rhabdomyolysis has been reported in postmarketing use"]}}
    summary = sv.handle_label_change(change)
    assert summary["created"] == 1 and summary["linked_existing_alerts"] == 1
    lc = [a for a in _alerts("111") if a["kind"] == "label_change"]
    assert lc and json.loads(lc[0]["detail_json"])["changed_sections"]
    open_alert = query_one("SELECT detail_json FROM surveillance_alerts WHERE kind = 'not_in_label' AND rxcui = '111'")
    assert "label_caught_up" in (open_alert["detail_json"] or "")


def test_replaying_a_label_change_changes_nothing(results):
    change = {"rxcui": "222", "generic_name": "other", "effective_time": "20250601",
              "changed_sections": {"boxed_warning": ["New boxed warning text here"]}}
    sv.handle_label_change(change)
    rows1 = _alerts("222")
    sv.handle_label_change(change)
    assert len(_alerts("222")) == len(rows1)


def test_the_consumer_imports_no_statistics():
    import re
    text = open(os.path.join(BACKEND, "services", "surveillance.py"), encoding="utf-8").read()
    assert not re.search(r"compute_metrics|disproportionality\.compute", text)
    # it reads Spark's precomputed classes, it does not recompute them
    assert "signal_strength" in text and "read_parquet" in text
