"""
Signal-detection benchmark: how well does PharmaWatch separate known adverse reactions
(positive controls) from known non-reactions (negative controls)?

    python -m validation.signals_benchmark            # full run on the loaded FAERS database
    python -m validation.signals_benchmark --smoke    # small fixed run on the test fixture

For each reference pair the 2x2 table is built over the union of the outcome's Preferred Terms,
then scored three ways (PRR, lower 95% bound of the ROR, IC025) and by the application's own
class rule. Pairs with fewer than 3 cases have no signal by the app's rule; they are reported
separately as coverage and scored two ways: "covered pairs only", and "all pairs" with uncovered
pairs counted as no signal. Results go to reports/validation/signals.json.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

import duckdb

from faers.disproportionality import MIN_CASES, SIGNAL, SIGNAL_NONE, SIGNAL_STRONG, compute_metrics
from validation import metrics as M
from validation import reference_sets as ref

REPORTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                           "reports", "validation")
SCORES = {"prr": "prr", "ror_lower": "ror_ci_low", "ic025": "ic025"}
BOOTSTRAP = 1000


def _tables(con, rows, drug_map, outcome_pts):
    """{(drug, outcome): (a, exposed, event, n)} for every resolvable pair, in one pass.

    Equivalent to faers.stats.contingency for each pair (suspect exposure, every ingredient of a
    combination required, event = any of the outcome's Preferred Terms), but the large tables are
    scanned once for all pairs, not once per pair.
    """
    drugs = sorted({r["drug"] for r in rows if drug_map.get(r["drug"], {}).get("rxcuis")})
    outcomes = sorted({r["outcome"] for r in rows if outcome_pts.get(r["outcome"])})
    if not drugs or not outcomes:
        return {}
    con.execute("CREATE OR REPLACE TEMP TABLE b_drug_map (drug VARCHAR, rxcui VARCHAR, need INTEGER)")
    con.executemany("INSERT INTO b_drug_map VALUES (?, ?, ?)", [
        (d, x, len(set(drug_map[d]["rxcuis"]))) for d in drugs for x in set(drug_map[d]["rxcuis"])])
    con.execute("CREATE OR REPLACE TEMP TABLE b_outcome_map (outcome VARCHAR, pt VARCHAR)")
    con.executemany("INSERT INTO b_outcome_map VALUES (?, ?)", [
        (o, t.lower()) for o in outcomes for t in set(outcome_pts[o])])
    con.execute("""
        CREATE OR REPLACE TEMP TABLE b_exposed AS
        SELECT m.drug, cd.primaryid
        FROM case_drug cd JOIN b_drug_map m ON m.rxcui = cd.ingredient_rxcui
        WHERE cd.suspect
        GROUP BY m.drug, cd.primaryid, m.need
        HAVING count(DISTINCT cd.ingredient_rxcui) = m.need
    """)
    con.execute("""
        CREATE OR REPLACE TEMP TABLE b_event AS
        SELECT DISTINCT o.outcome, r.primaryid
        FROM faers_reac r JOIN b_outcome_map o ON o.pt = lower(r.pt)
    """)
    n = con.execute("SELECT count(*) FROM faers_demo").fetchone()[0]
    exposed = dict(con.execute("SELECT drug, count(*) FROM b_exposed GROUP BY drug").fetchall())
    event = dict(con.execute("SELECT outcome, count(*) FROM b_event GROUP BY outcome").fetchall())
    both = {(d, o): k for d, o, k in con.execute("""
        SELECT x.drug, e.outcome, count(*) FROM b_exposed x JOIN b_event e USING (primaryid)
        GROUP BY x.drug, e.outcome""").fetchall()}
    return {(d, o): (int(both.get((d, o), 0)), int(exposed.get(d, 0)), int(event.get(o, 0)), int(n))
            for d in drugs for o in outcomes}


def score_pairs(con, rows, drug_map, outcome_pts):
    """One result per reference pair: the table, the metrics, and why it was or was not scored."""
    tables = _tables(con, rows, drug_map, outcome_pts)
    out = []
    for r in rows:
        drug, outcome = r["drug"], r["outcome"]
        entry = {"drug": drug, "outcome": outcome, "label": r["label"], "status": None,
                 "a": 0, "prr": 0.0, "ror_lower": 0.0, "ic025": 0.0, "signal_strength": SIGNAL_NONE}
        if not drug_map.get(drug, {}).get("rxcuis"):
            entry["status"] = "drug_unresolved"
        elif not outcome_pts.get(outcome):
            entry["status"] = "outcome_unmapped"
        else:
            a, exposed, event, n = tables[(drug, outcome)]
            m = compute_metrics(a, exposed - a, event - a, n - exposed - event + a)
            entry.update(a=m["a"], prr=m["prr"], ror_lower=m["ror_ci_low"], ic025=m["ic025"],
                         signal_strength=m["signal_strength"], n_exposed=exposed,
                         status="covered" if m["a"] >= MIN_CASES else "uncovered")
        out.append(entry)
    return out


def _summary(pairs, covered_only):
    """Metrics for one selection of pairs."""
    sel = [p for p in pairs if (p["status"] == "covered" if covered_only else p["status"] in ("covered", "uncovered"))]
    y = [p["label"] for p in sel]
    res = {"pairs": len(sel), "positives": sum(y), "negatives": len(y) - sum(y)}
    for name in SCORES:
        # an uncovered pair has no signal by the app's rule, so it is scored below every covered one
        s = [(-1.0 if p["status"] == "uncovered" else p[name]) for p in sel]
        res[f"auc_{name}"] = _round(M.auc(y, s))
        res[f"auc_{name}_ci"] = _ci(M.bootstrap_ci(M.auc, y, s, n=BOOTSTRAP))
    for label, accepted in (("signal", (SIGNAL, SIGNAL_STRONG)), ("strong_signal", (SIGNAL_STRONG,))):
        pred = [int(p["signal_strength"] in accepted) for p in sel]
        sens, spec = M.sensitivity_specificity(y, pred)
        res[f"{label}_sensitivity"] = _round(sens)
        res[f"{label}_specificity"] = _round(spec)
        res[f"{label}_sensitivity_ci"] = _ci(M.bootstrap_ci(lambda yy, pp: M.sensitivity_specificity(yy, pp)[0],
                                                             y, pred, n=BOOTSTRAP))
        res[f"{label}_specificity_ci"] = _ci(M.bootstrap_ci(lambda yy, pp: M.sensitivity_specificity(yy, pp)[1],
                                                             y, pred, n=BOOTSTRAP))
    k = min(20, len(sel))
    res["precision_at_20_prr"] = _round(M.precision_at_k(y, [p["prr"] for p in sel], k)) if sel else None
    return res


def _round(v):
    return None if v is None else round(float(v), 4)


def _ci(v):
    return None if v is None else [round(v[0], 4), round(v[1], 4)]


def run_set(con, name, drug_map, outcome_pts):
    rows = ref.load_set(name)
    pairs = score_pairs(con, rows, drug_map, outcome_pts)
    counts = {}
    for p in pairs:
        counts[p["status"]] = counts.get(p["status"], 0) + 1
    by_outcome = {}
    for p in pairs:
        o = by_outcome.setdefault(p["outcome"], {"pairs": 0, "covered": 0, "positives": 0, "negatives": 0})
        o["pairs"] += 1
        o["covered"] += int(p["status"] == "covered")
        o["positives" if p["label"] else "negatives"] += 1
    return {
        "reference_pairs": len(rows),
        "status_counts": counts,
        "coverage": _round(counts.get("covered", 0) / len(rows)) if rows else None,
        "covered_pairs_only": _summary(pairs, covered_only=True),
        "all_pairs_uncovered_as_no_signal": _summary(pairs, covered_only=False),
        "by_outcome": by_outcome,
        "pairs": pairs,
    }


def run(db_path, smoke=False, out_path=None):
    drug_map = ref.load_drug_map()
    outcome_pts, statuses = ref.load_outcome_pts()
    con = duckdb.connect(db_path, read_only=True)
    try:
        from faers.registry import data_version
        version = data_version(db_path)
    except Exception:
        version = "unknown"
    try:
        results = {name: run_set(con, name, drug_map, outcome_pts) for name in ref.SETS}
    finally:
        con.close()

    if smoke:                       # keep CI output small: no per-pair rows
        for r in results.values():
            r.pop("pairs")

    report = {
        "benchmark": "signal_detection",
        "run_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "data_version": version,
        "smoke": smoke,
        "app_rule": {"signal": "a>=3, PRR>=2, chi2>=4",
                     "strong_signal": "signal and ROR lower bound>1 and IC025>0"},
        "outcome_definitions": {"status": sorted(statuses),
                                "note": "Drafted by the assistant, not clinically reviewed, until status is 'reviewed'."},
        "reference_checksums": {n: ref._sha256(os.path.join(ref.REFERENCE_DIR, n)) for n in ref.FROZEN},
        "reference_files_match_freeze": not ref.verify_checksums(),
        "bootstrap": {"resamples": BOOTSTRAP, "interval": "95% percentile"},
        "results": results,
    }
    out_path = out_path or os.path.join(REPORTS_DIR, "signals_smoke.json" if smoke else "signals.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(report, f, indent=2, sort_keys=True)
        f.write("\n")
    return report, out_path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--smoke", action="store_true", help="small run for CI; writes signals_smoke.json")
    parser.add_argument("--db", help="DuckDB file (default: the loaded FAERS database)")
    args = parser.parse_args(argv)
    from faers.analytics import DB_PATH
    report, path = run(args.db or DB_PATH, smoke=args.smoke)
    for name, r in report["results"].items():
        c, a = r["covered_pairs_only"], r["all_pairs_uncovered_as_no_signal"]
        print(f"{name}: {r['reference_pairs']} pairs, covered {r['status_counts'].get('covered', 0)} "
              f"({r['coverage']}); AUC(PRR) covered {c['auc_prr']} {c['auc_prr_ci']}, all {a['auc_prr']} {a['auc_prr_ci']}")
    print("wrote", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
