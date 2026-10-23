def pytest_addoption(parser):
    parser.addoption(
        "--update-inventory",
        action="store_true",
        default=False,
        help="Regenerate backend/tests/fixtures/route_inventory.json from the live app.",
    )


# ── CI test categories ───────────────────────────────────────────────────────
# Every test is tagged exactly one of: statistics (numerical / validation correctness),
# integration (the live app, MySQL routes, ingestion, big-data clients), or unit (everything
# else — the default). The tagging is by file name so individual tests stay untouched, and it is
# a true partition: CI runs the three markers as parallel jobs and together they are the whole
# suite. The marker only decides which job runs a test, so a mis-tag never changes its outcome.
_STATISTICS = {
    "test_disproportionality", "test_faers_stats", "test_zscore_spikes", "test_single_stats_path",
    "test_lead_time", "test_forecast", "test_ddi_calibration", "test_ddi_eval_and_inference",
    "test_ddi_model", "test_ddi_dataset", "test_signals_benchmark", "test_prescription_benchmark",
    "test_validation_report", "test_validation_gate",
}
_INTEGRATION = {
    "test_mysql_foundation", "test_mysql_routes", "test_interactions_api", "test_surveillance",
    "test_replay", "test_jobs", "test_queue", "test_storage", "test_label_watcher",
    "test_label_check", "test_faers_ingest", "test_ingest", "test_hbase_lookup",
    "test_spark_signals", "test_signal_history", "test_drug_profile", "test_route_inventory",
    "test_frontend_contract", "test_frontend_wording", "test_no_invented_numbers",
    "test_boxed_warnings", "test_surveillance",
}


def pytest_configure(config):
    for name in ("unit", "integration", "statistics"):
        config.addinivalue_line("markers", f"{name}: CI category (auto-tagged by file name)")


def pytest_collection_modifyitems(config, items):
    import pytest as _pt
    for item in items:
        stem = _os.path.splitext(_os.path.basename(str(item.fspath)))[0]
        if stem in _STATISTICS:
            item.add_marker(_pt.mark.statistics)
        elif stem in _INTEGRATION:
            item.add_marker(_pt.mark.integration)
        else:
            item.add_marker(_pt.mark.unit)


# ── a small FAERS database and its lake, for the batch-job tests ─────────────
import os as _os
import sys as _sys

import pytest as _pytest

_BACKEND = _os.path.abspath(_os.path.join(_os.path.dirname(__file__), ".."))
if _BACKEND not in _sys.path:
    _sys.path.insert(0, _BACKEND)


def build_small_faers(path):
    """Three quarters of synthetic cases with known structure: drugs at different event rates,
    a pair where every exposed case has the event (b = 0), and an event seen only with one drug
    (c = 0), so the zero-cell correction is exercised."""
    import duckdb
    con = duckdb.connect(path)
    con.execute("CREATE TABLE faers_demo (primaryid VARCHAR, quarter VARCHAR)")
    con.execute("CREATE TABLE case_drug (primaryid VARCHAR, ingredient_rxcui VARCHAR, suspect BOOLEAN)")
    con.execute("CREATE TABLE faers_reac (primaryid VARCHAR, pt VARCHAR, quarter VARCHAR)")
    n = 0

    def add(quarter, drugs, events, count, suspect=True):
        nonlocal n
        for _ in range(count):
            n += 1
            pid = f"p{n}"
            con.execute("INSERT INTO faers_demo VALUES (?, ?)", [pid, quarter])
            for d in drugs:
                con.execute("INSERT INTO case_drug VALUES (?, ?, ?)", [pid, d, suspect])
            for e in events:
                con.execute("INSERT INTO faers_reac VALUES (?, ?, ?)", [pid, e, quarter])

    for q, scale in (("2024q1", 1), ("2024q2", 2), ("2024q3", 3)):
        add(q, [], ["Nausea"], 30 * scale)
        add(q, [], ["Headache", "Nausea"], 10 * scale)
        add(q, [], [], 200 * scale)
        add(q, ["111"], ["Rhabdomyolysis"], 2 * scale)          # rare event, strongly with drug 111
        add(q, ["111"], ["Nausea"], 5 * scale)
        add(q, ["111"], [], 20 * scale)
        add(q, ["222"], ["Nausea", "Headache"], 6 * scale)       # common events, ordinary rate
        add(q, ["222"], [], 40 * scale)
        add(q, ["333"], ["Lactic acidosis"], 4 * scale)          # every exposed case has it: b = 0
        add(q, ["111", "222"], ["Nausea"], 3 * scale)            # both suspect
        add(q, ["444"], ["Rhabdomyolysis"], 1, suspect=False)   # concomitant: not counted
    con.close()
    return path


@_pytest.fixture
def small_lake(tmp_path, monkeypatch):
    """DATA_DIR with a small FAERS database exported to the lake (local storage)."""
    from faers import export_lake
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    db = build_small_faers(str(tmp_path / "pharmawatch_faers.duckdb"))
    export_lake.export(db, log=lambda *_: None)
    return {"db": db, "lake": str(tmp_path / "lake").replace("\\", "/"), "data_dir": str(tmp_path)}
