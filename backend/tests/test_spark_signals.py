"""
The batch job: the lake holds exactly what the app counts, the job's formulas equal
compute_metrics, Spark (when available) gives the same result as DuckDB, and the checks that
guard the real run catch a wrong value.
"""

import os
import sys

import duckdb
import pytest

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from bigdata import timing, verify  # noqa: E402
from faers.disproportionality import compute_metrics  # noqa: E402
from faers.stats import contingency  # noqa: E402

METRICS = verify.METRICS


def _parquet(lake, table):
    return f"read_parquet('{lake}/clean/{table}/*/*.parquet', hive_partitioning = 1)"


# ── the lake ────────────────────────────────────────────────────────────────
def test_the_lake_holds_exactly_what_the_app_counts(small_lake):
    con = duckdb.connect(small_lake["db"], read_only=True)
    lake = small_lake["lake"]
    assert con.execute(f"SELECT count(*) FROM {_parquet(lake, 'case')}").fetchone()[0] == \
        con.execute("SELECT count(*) FROM faers_demo").fetchone()[0]
    # only suspect drugs, one row per case and ingredient
    assert con.execute(f"SELECT count(*) FROM {_parquet(lake, 'case_drug')} WHERE rxcui = '444'").fetchone()[0] == 0
    assert con.execute(f"SELECT count(*) FROM {_parquet(lake, 'case_drug')}").fetchone()[0] == \
        con.execute("SELECT count(*) FROM (SELECT DISTINCT primaryid, ingredient_rxcui FROM case_drug WHERE suspect)").fetchone()[0]
    # terms in lower case, one row per case and term
    assert con.execute(f"SELECT count(*) FROM {_parquet(lake, 'reaction')} WHERE pt_lower <> lower(pt_lower)").fetchone()[0] == 0
    from faers.export_lake import read_manifest
    m = read_manifest("reaction")
    assert m["quarters"] == ["2024q1", "2024q2", "2024q3"] and m["rows"] > 0


# ── the formulas ────────────────────────────────────────────────────────────
@pytest.fixture
def duck_results(small_lake):
    con, _ = timing.run_duckdb(small_lake["lake"])
    rows = con.execute("SELECT * FROM results ORDER BY rxcui, pt_lower, quarter").fetchall()
    names = [d[0] for d in con.description]
    con.close()
    return [dict(zip(names, r)) for r in rows]


def test_every_pair_quarter_equals_the_apps_own_statistics(small_lake, duck_results):
    con = duckdb.connect(small_lake["db"], read_only=True)
    assert duck_results
    for row in duck_results:
        cells = contingency(con, row["rxcui"], row["pt_lower"], where=("quarter <= ?", [row["quarter"]]))
        app = compute_metrics(cells["a"], cells["b"], cells["c"], cells["d"])
        assert [row[k] for k in "abcd"] == [cells[k] for k in "abcd"], row
        assert row["signal_strength"] == app["signal_strength"], row
        for m in METRICS:
            assert verify.close(row[m], app[m]), (m, row, app)


def test_pairs_start_at_their_first_quarter_with_three_cases(duck_results):
    rhabdo = [r for r in duck_results if r["rxcui"] == "111" and r["pt_lower"] == "rhabdomyolysis"]
    assert [r["quarter"] for r in rhabdo] == ["2024q2", "2024q3"]          # 2, then 2+4, then 2+4+6 cases
    assert [r["a"] for r in rhabdo] == [6, 12]
    assert all(r["a"] >= 3 for r in duck_results)


def test_the_zero_cell_correction_is_applied(duck_results):
    row = next(r for r in duck_results if r["rxcui"] == "333" and r["pt_lower"] == "lactic acidosis")
    assert row["b"] == 0 and row["c"] == 0 and row["continuity_correction"]


def test_a_concomitant_drug_is_never_counted(duck_results):
    assert not [r for r in duck_results if r["rxcui"] == "444"]


# ── Spark, when a JVM and PySpark are available (CI installs them) ───────────
def _java_major():
    import re
    import subprocess
    try:
        out = subprocess.run(["java", "-version"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.search(r'version "(\d+)(?:\.(\d+))?', out.stderr + out.stdout)
    if not m:
        return None
    major = int(m.group(1))
    return int(m.group(2)) if major == 1 else major            # "1.8.0" is Java 8


def _spark_or_skip():
    pyspark = pytest.importorskip("pyspark")
    needed = 17 if int(pyspark.__version__.split(".")[0]) >= 4 else 8
    java = _java_major()
    if java is None or java < needed:                 # starting the JVM would hang, not fail
        pytest.skip(f"pyspark {pyspark.__version__} needs Java {needed}+; found {java}")
    try:
        from pyspark.sql import SparkSession
        return (SparkSession.builder.master("local[2]").appName("pw-test")
                .config("spark.sql.shuffle.partitions", "4").config("spark.ui.enabled", "false").getOrCreate())
    except Exception as exc:                          # no usable Java for this PySpark
        pytest.skip(f"Spark cannot start here ({type(exc).__name__}: {str(exc)[:80]}); pyspark {pyspark.__version__}")


def test_spark_gives_exactly_the_duckdb_result(small_lake, duck_results):
    spark = _spark_or_skip()
    from bigdata.spark_signals import build
    try:
        out, quarters = build(spark, small_lake["lake"])
        rows = {(r["rxcui"], r["pt_lower"], r["quarter"]): r.asDict() for r in out.collect()}
    finally:
        spark.stop()
    assert quarters == ["2024q1", "2024q2", "2024q3"]
    assert set(rows) == {(r["rxcui"], r["pt_lower"], r["quarter"]) for r in duck_results}
    for r in duck_results:
        s = rows[(r["rxcui"], r["pt_lower"], r["quarter"])]
        assert [s[k] for k in "abcd"] == [r[k] for k in "abcd"] and s["signal_strength"] == r["signal_strength"]
        for m in METRICS:
            assert abs(s[m] - r[m]) <= 1e-9 * max(1.0, abs(r[m])), (m, s, r)


# ── the checks that guard the real run ───────────────────────────────────────
def test_the_agreement_rule_accepts_rounding_and_rejects_a_real_difference():
    assert verify.close(2.123449999, 2.1234)                 # the app rounds to 4 decimals
    assert not verify.close(2.1236, 2.1234)
    assert verify.close(123456.78912, 123456.7891)
    assert not verify.close(None, 1.0) and verify.close(None, None)


def test_verify_reports_a_wrong_spark_value(small_lake, duck_results, monkeypatch, tmp_path):
    """Write the DuckDB results as if Spark had produced them, corrupt one value, and verify."""
    import pandas as pd
    out = os.path.join(small_lake["data_dir"], "lake", "results", "signals_by_quarter")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    frame = pd.DataFrame(duck_results)
    frame.loc[0, "a"] = frame.loc[0, "a"] + 1                 # one wrong count
    con = duckdb.connect()
    con.register("frame", frame)
    con.execute(f"COPY frame TO '{out}' (FORMAT PARQUET, PARTITION_BY (quarter))")
    con.close()
    monkeypatch.setattr(verify, "database_path", lambda: small_lake["db"])
    monkeypatch.setattr(verify, "OUT", str(tmp_path / "agreement.json"))
    report = verify.verify(per_quarter=1000, log=lambda *_: None)
    assert report["checked"] >= len(duck_results) - 1 and report["mismatches"] >= 1
    assert any("cell a" in p for e in report["examples"] for p in e["problems"])


# ── sample databases ─────────────────────────────────────────────────────────
def test_a_sample_database_keeps_whole_cases_and_says_it_is_a_sample(small_lake, tmp_path):
    import duckdb as d
    from faers import sample_db
    from faers.registry import data_version
    con = d.connect(small_lake["db"])
    con.execute("CREATE TABLE faers_drug AS SELECT primaryid, 1 AS drug_seq FROM case_drug")
    for t in ("faers_outc", "faers_ther"):
        con.execute(f"CREATE TABLE {t} AS SELECT primaryid FROM faers_demo WHERE false")
    for t in ("deleted_cases", "drug_brand_map", "drug_identity", "drug_mappings"):
        con.execute(f"CREATE TABLE {t} (x INTEGER)")
    con.execute("CREATE TABLE identity_build_info (key VARCHAR, value VARCHAR)")
    con.execute("CREATE TABLE faers_quarters AS SELECT DISTINCT quarter, 'z' AS zip_file, 'h' AS zip_sha256, 0 AS reports_in_file, "
                "0 AS deleted_cases, 0 AS cases_kept, 0 AS drug_rows, 0 AS reac_rows, false AS is_fixture, now()::TIMESTAMP AS loaded_at FROM faers_demo")
    con.close()
    out = str(tmp_path / "sample.duckdb")
    cases = sample_db.make_sample(50, out, source=small_lake["db"], log=lambda *_: None)
    s = d.connect(out, read_only=True)
    try:
        orphans = s.execute("SELECT count(*) FROM case_drug WHERE primaryid NOT IN (SELECT primaryid FROM faers_demo)").fetchone()[0]
        total = s.execute("SELECT sum(cases_kept) FROM faers_quarters").fetchone()[0]
    finally:
        s.close()
    assert 0 < cases < 1000 and orphans == 0 and total == cases
    assert data_version(out).endswith("[50% sample]")


# ── the announcement surveillance listens for ────────────────────────────────
def test_a_finished_run_is_announced_on_signals_updated(monkeypatch):
    kafka = pytest.importorskip("kafka")
    from bigdata import run_spark
    sent = []

    class Producer:
        def __init__(self, **kw):
            pass

        def send(self, topic, key, value):
            sent.append((topic, key, value))

            class Done:
                def get(self, timeout=None):
                    return None
            return Done()

        def close(self):
            pass
    monkeypatch.setattr(kafka, "KafkaProducer", Producer)
    manifest = {"run_id": "r1", "quarters": ["2024q1", "2024q2"], "data_version": "v"}
    assert run_spark.publish_signals_updated(manifest) is True
    topic, key, value = sent[0]
    assert topic == "signals-updated" and key == "r1" and value["latest_quarter"] == "2024q2"

    def broken(**kw):
        raise ConnectionError("no broker")
    monkeypatch.setattr(kafka, "KafkaProducer", broken)
    assert run_spark.publish_signals_updated(manifest) is False          # reported, not raised
