"""
Replay is isolated and repeatable: replaying the loaded quarters produces alerts in the replay
store, never touches the live surveillance_alerts table, and two replays of the same range give
the same alerts. The detector that runs on replay is the same one that runs live.
"""

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
        return query_one("SELECT COUNT(*) AS n FROM replay_alerts") is not None
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _mysql_ready(), reason="MySQL with the replay tables is not reachable")


@pytest.fixture
def results(small_lake):
    """Spark-shaped results for the small FAERS data; clean live and replay alert tables around it."""
    from db.mysql import execute
    con, _ = timing.run_duckdb(small_lake["lake"])
    out = os.path.join(small_lake["data_dir"], "lake", "results", "signals_by_quarter")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    con.execute(f"COPY results TO '{out}' (FORMAT PARQUET, PARTITION_BY (quarter))")
    con.close()
    runs = []
    execute("DELETE FROM surveillance_alerts WHERE rxcui IN ('111', '222', '333')")
    yield small_lake, runs
    execute("DELETE FROM surveillance_alerts WHERE rxcui IN ('111', '222', '333')")
    for run_id in runs:
        execute("DELETE FROM replay_alerts WHERE run_id = %s", (run_id,))
        execute("DELETE FROM replay_runs WHERE run_id = %s", (run_id,))


def _run_full_replay(runs):
    """Start a replay over all loaded quarters and step every quarter; return (run_id, alerts)."""
    start, status = sv.start_replay()
    assert status == 200, start
    run_id, span = start["run_id"], start["quarters"]
    runs.append(run_id)
    for q in span:
        _, s = sv.replay_step(run_id, q)
        assert s == 200
    feed, _ = sv.list_replay_alerts(run_id, upto=span[-1], limit=500)
    return run_id, feed["alerts"], span


def test_replay_populates_the_replay_store(results):
    _, _ = results
    _, alerts, span = _run_full_replay(_)
    assert len(span) >= 2
    assert alerts, "a replay should raise at least one alert on the known-signal fixture"
    # the strong 111/rhabdomyolysis signal shows up somewhere in the replay
    assert any(a["rxcui"] == "111" and a["pt"].lower() == "rhabdomyolysis" for a in alerts)


def test_replay_never_touches_live_alerts(results):
    small_lake, runs = results
    from db.mysql import query_one
    before = query_one("SELECT COUNT(*) AS n FROM surveillance_alerts")["n"]
    _run_full_replay(runs)
    after = query_one("SELECT COUNT(*) AS n FROM surveillance_alerts")["n"]
    assert after == before, "a replay must not write to the live surveillance_alerts table"


def test_two_replays_of_the_same_range_match(results):
    _, runs = results
    _, a1, span = _run_full_replay(runs)
    _, a2, _ = _run_full_replay(runs)
    key = lambda rows: sorted((a["quarter"], a["kind"], a["rxcui"], a["pt"]) for a in rows)
    assert key(a1) == key(a2)


def test_re_stepping_a_quarter_is_idempotent(results):
    _, runs = results
    start, _ = sv.start_replay()
    run_id, span = start["run_id"], start["quarters"]
    runs.append(run_id)
    sv.replay_step(run_id, span[-1])
    first, _ = sv.list_replay_alerts(run_id, quarter=span[-1], limit=500)
    sv.replay_step(run_id, span[-1])                     # step the same quarter again
    second, _ = sv.list_replay_alerts(run_id, quarter=span[-1], limit=500)
    assert first["count"] == second["count"]
