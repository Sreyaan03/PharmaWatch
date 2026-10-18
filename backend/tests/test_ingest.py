"""
Kafka ingest: the stream computes no statistics, announces new FAERS quarters, links external
evidence to drug identities, and changes nothing when a message is delivered twice.
"""

import os
import re
import sys

import pytest

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from faers import pipeline as faers_pipeline  # noqa: E402
from faers.mentions import MentionIndex  # noqa: E402
from ingest import consumer  # noqa: E402

REPO = os.path.dirname(BACKEND)

INDEX = MentionIndex(
    drugs={"metformin": ("6809", "metformin"), "glucophage": ("6809", "metformin"),
           "warfarin": ("11289", "warfarin"), "coumadin": ("11289", "warfarin"),
           "acetylsalicylic acid": ("1191", "aspirin"), "aspirin": ("1191", "aspirin"),
           "placebo": ("0", "placebo")},
    pts={"lactic acidosis": "Lactic acidosis", "haemorrhage": "Haemorrhage", "death": "Death",
         "rash": "Rash"},
)


# ── the stream computes no statistics ────────────────────────────────────────
def _source_files(*dirs):
    for d in dirs:
        for root, _, files in os.walk(os.path.join(BACKEND, d)):
            for f in files:
                if f.endswith(".py"):
                    yield os.path.join(root, f)


def test_ingest_imports_no_statistics():
    imports = re.compile(r"^\s*(?:from|import)\s+.*(?:disproportionality|compute_metrics|faers\.stats|faers\.analytics)", re.M)
    for path in _source_files("ingest"):
        assert not imports.search(open(path, encoding="utf-8").read()), path


def test_no_hard_coded_background_counts_remain():
    banned = re.compile(r"INITIAL_GLOBAL_BACKGROUND|TOTAL_FAERS_POPULATION|get_global_event_count|faers_background_events|drug_events_agg")
    offenders = []
    for path in _source_files("faers", "services", "ingest", "routes", "db", "core"):
        if banned.search(open(path, encoding="utf-8").read()) and not path.endswith(os.path.join("db", "migrate.py")):
            offenders.append(os.path.relpath(path, BACKEND))
    assert not offenders, offenders
    assert not os.path.exists(os.path.join(REPO, "scripts", "recalculate_signals.py"))
    assert "faers-raw" not in consumer.TOPIC_HANDLERS


# ── mention matching ─────────────────────────────────────────────────────────
def test_mentions_map_brands_to_ingredients_and_prefer_longer_names():
    found = INDEX.drugs_in("Glucophage and acetylsalicylic acid in elderly patients")
    assert [(d["rxcui"], d["matched"]) for d in found] == [("6809", "glucophage"), ("1191", "acetylsalicylic acid")]


def test_mentions_ignore_ordinary_words_and_partial_words():
    assert INDEX.drugs_in("Placebo-controlled study") == []
    assert INDEX.drugs_in("metformins or warfarinised") == []
    assert INDEX.drugs_in("Coumadin, warfarin and COUMADIN") == [{"rxcui": "11289", "ingredient": "warfarin", "matched": "coumadin"}]


def test_mentions_skip_terms_too_general_to_mean_anything():
    terms = [t["pt"] for t in INDEX.pts_in("Death after lactic acidosis with a rash")]
    assert terms == ["Lactic acidosis"]       # "death" is generic, "rash" is too short to trust


# ── linking ──────────────────────────────────────────────────────────────────
@pytest.fixture
def captured(monkeypatch):
    calls = []
    monkeypatch.setattr(consumer, "_index", INDEX)
    monkeypatch.setattr(consumer, "execute", lambda sql, params=None: calls.append((sql, params)))
    return calls


def _links(calls):
    return [(p[0], p[2], p[3], p[4]) for sql, p in calls if "evidence_links" in sql]


def test_paper_links_each_drug_with_each_term_in_its_title(captured):
    consumer.handle_pubmed({"pmid": "123", "title": "Metformin-associated lactic acidosis and warfarin haemorrhage"})
    assert sorted(_links(captured)) == sorted([
        ("6809", "Lactic acidosis", "paper", "123"), ("6809", "Haemorrhage", "paper", "123"),
        ("11289", "Lactic acidosis", "paper", "123"), ("11289", "Haemorrhage", "paper", "123")])


def test_trial_links_its_drug_but_not_the_condition_it_treats(captured):
    consumer.handle_trials({"nct_id": "NCT1", "drug": "metformin", "title": "Metformin in lactic acidosis risk"})
    assert _links(captured) == [("6809", "", "trial", "NCT1")]


def test_recall_links_drugs_named_in_the_product(captured):
    consumer.handle_alerts({"source": "fda_enforcement", "recall_num": "D-1", "title": "Failed dissolution",
                            "product": "Warfarin Sodium Tablets 5 mg"})
    assert _links(captured) == [("11289", "", "alert", "D-1")]


def test_an_item_naming_no_known_drug_is_stored_but_not_linked(captured):
    consumer.handle_pubmed({"pmid": "9", "title": "Pharmacovigilance methods in 2026"})
    assert any("kafka_pubmed" in sql for sql, _ in captured)
    assert _links(captured) == []


def test_pipeline_stats_ignore_a_redelivered_message(captured):
    consumer.record_pipeline_stat("fda-alerts", 0, 41)
    sql, params = captured[-1]
    assert sql.startswith("INSERT IGNORE") and params == ("fda-alerts", 0, "41")


# ── FAERS release ────────────────────────────────────────────────────────────
@pytest.fixture
def release_env(monkeypatch):
    state = {"rows": {}, "started": []}
    monkeypatch.setattr(faers_pipeline, "loaded_quarters", lambda db_file=None: {"2026q1", "2026q2"})
    monkeypatch.setattr(faers_pipeline, "query_one",
                        lambda sql, p: ({"status": state["rows"][p[0]]} if p[0] in state["rows"] else None))

    def fake_execute(sql, p):
        if sql.startswith("INSERT INTO faers_releases (quarter, status) VALUES"):
            state["rows"].setdefault(p[0], "available")
    monkeypatch.setattr(faers_pipeline, "execute", fake_execute)
    monkeypatch.setattr(faers_pipeline, "start_refresh",
                        lambda q, created_by=None: state["started"].append(q) or "job-1")
    monkeypatch.delenv("FAERS_AUTO_REFRESH", raising=False)
    return state


def test_release_of_a_loaded_quarter_does_nothing(release_env):
    assert consumer.handle_release({"quarter": "2026Q2"}) == "already_loaded"
    assert release_env["rows"] == {} and release_env["started"] == []


def test_release_with_auto_refresh_off_only_records_a_notice(release_env):
    assert consumer.handle_release({"quarter": "2026Q3"}) == "recorded"
    assert release_env["rows"] == {"2026q3": "available"} and release_env["started"] == []
    assert consumer.handle_release({"quarter": "2026q3"}) == "available"     # delivered again: unchanged
    assert release_env["rows"] == {"2026q3": "available"}


def test_release_with_auto_refresh_on_starts_one_job(release_env, monkeypatch):
    monkeypatch.setenv("FAERS_AUTO_REFRESH", "1")
    assert consumer.handle_release({"quarter": "2026Q3"}) == "refreshing"
    consumer.handle_release({"quarter": "2026Q3"})                              # redelivered
    assert release_env["started"] == ["2026q3"]


def test_release_with_a_malformed_quarter_is_ignored(release_env):
    assert consumer.handle_release({"quarter": "next one"}) == "ignored"
    assert consumer.handle_release({}) == "ignored"


def test_producer_announces_only_a_quarter_not_yet_loaded():
    pytest.importorskip("kafka")
    from ingest.producer import check_faers_release
    assert check_faers_release(latest_released=lambda n: [(2026, 3)], loaded={"2026q2"}) == "2026q3"
    assert check_faers_release(latest_released=lambda n: [(2026, 2)], loaded={"2026q2"}) is None


# ── idempotency against MySQL (runs in CI, where the schema is migrated) ────
def _mysql_ready():
    try:
        from db.mysql import query_one
        return query_one("SELECT COUNT(*) AS n FROM evidence_links") is not None
    except Exception:
        return False


MESSAGES = [
    ("pubmed-stream", {"pmid": "TEST-PMID-1", "title": "Metformin and lactic acidosis: a case", "pubdate": "2026"}),
    ("clinical-trials", {"nct_id": "TEST-NCT-1", "drug": "warfarin", "title": "Warfarin dosing", "phase": ["PHASE3"], "status": "RECRUITING"}),
    ("fda-alerts", {"source": "fda_enforcement", "recall_num": "TEST-D-1", "title": "Subpotent", "product": "Coumadin 5 mg"}),
    ("faers-release", {"quarter": "2099q1"}),
]
SNAPSHOT = [
    ("evidence_links", "SELECT rxcui, ingredient, pt, kind, ref_id, title, url, published, matched_text, linked_at "
                       "FROM evidence_links WHERE ref_id LIKE 'TEST-%%' ORDER BY rxcui, pt, kind, ref_id"),
    ("kafka_pubmed", "SELECT * FROM kafka_pubmed WHERE pmid LIKE 'TEST-%%'"),
    ("kafka_trials", "SELECT * FROM kafka_trials WHERE nct_id LIKE 'TEST-%%'"),
    ("kafka_alerts", "SELECT * FROM kafka_alerts WHERE alert_key LIKE 'TEST-%%'"),
    ("faers_releases", "SELECT quarter, status, job_id, detected_at FROM faers_releases WHERE quarter = '2099q1'"),
    ("kafka_pipeline_stats", "SELECT topic, partition_id, last_offset, recorded_at FROM kafka_pipeline_stats "
                             "WHERE topic LIKE 'test-%%' ORDER BY topic, last_offset"),
]


def _cleanup(execute):
    execute("DELETE FROM evidence_links WHERE ref_id LIKE 'TEST-%%'")
    execute("DELETE FROM kafka_pubmed WHERE pmid LIKE 'TEST-%%'")
    execute("DELETE FROM kafka_trials WHERE nct_id LIKE 'TEST-%%'")
    execute("DELETE FROM kafka_alerts WHERE alert_key LIKE 'TEST-%%'")
    execute("DELETE FROM faers_releases WHERE quarter = '2099q1'")
    execute("DELETE FROM kafka_pipeline_stats WHERE topic LIKE 'test-%%'")


@pytest.mark.skipif(not _mysql_ready(), reason="MySQL with the migrated schema is not reachable")
def test_delivering_every_message_twice_leaves_the_database_unchanged(monkeypatch):
    from db.mysql import execute, query
    monkeypatch.setattr(consumer, "_index", INDEX)
    monkeypatch.setattr(faers_pipeline, "loaded_quarters", lambda db_file=None: set())
    monkeypatch.delenv("FAERS_AUTO_REFRESH", raising=False)
    _cleanup(execute)
    try:
        def deliver():
            for offset, (topic, msg) in enumerate(MESSAGES):
                consumer.TOPIC_HANDLERS[topic](msg)
                consumer.record_pipeline_stat("test-" + topic, 0, offset)

        deliver()
        first = {name: query(sql) for name, sql in SNAPSHOT}
        assert len(first["evidence_links"]) == 3 and len(first["kafka_pipeline_stats"]) == 4
        assert first["faers_releases"][0]["status"] == "available"
        deliver()
        second = {name: query(sql) for name, sql in SNAPSHOT}
        assert second == first
    finally:
        _cleanup(execute)
