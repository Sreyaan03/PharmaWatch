#!/usr/bin/env python3
"""
recalculate_signals.py — Recalculates signals_cache using global FAERS background reference.
Fixes single-cohort c=0 inflation across all drug-event pairs.
"""

import os
import sys

backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend"))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from dotenv import load_dotenv
load_dotenv(os.path.join(backend_dir, ".env"))

from db.mysql import query, query_one, execute
from faers.disproportionality import compute_metrics, get_global_event_count, TOTAL_FAERS_POPULATION

def recalculate_all():
    print("Fetching all pairs from drug_events_agg...")
    pairs = query("SELECT drug, event, report_count FROM drug_events_agg WHERE report_count >= 3")
    print(f"Found {len(pairs)} candidate pairs.")

    updated_count = 0
    for p in pairs:
        drug = p["drug"].lower().strip()
        event = p["event"].lower().strip()
        a = int(p["report_count"])

        row_ab = query_one("SELECT COALESCE(SUM(report_count), 0) AS cnt FROM drug_events_agg WHERE drug = %s", (drug,))
        ab = int(row_ab["cnt"]) if row_ab and row_ab.get("cnt") else a

        global_ac = get_global_event_count(event)
        ac = max(global_ac, a + 10)
        total = TOTAL_FAERS_POPULATION

        b = max(ab - a, 0)
        c = max(ac - a, 0)
        d = max(total - a - b - c, 0)

        metrics = compute_metrics(a, b, c, d)

        sql_signals = """
            INSERT INTO signals_cache (
                drug, event, a, b, c, d, prr, prr_ci_low, prr_ci_high, ror, ror_ci_low, ror_ci_high,
                chi2, bcpnn_ic, ic025, n_reports, signal_strength, source, computed_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'kafka_faers', NOW())
            ON DUPLICATE KEY UPDATE
                a = VALUES(a), b = VALUES(b), c = VALUES(c), d = VALUES(d),
                prr = VALUES(prr), prr_ci_low = VALUES(prr_ci_low), prr_ci_high = VALUES(prr_ci_high),
                ror = VALUES(ror), ror_ci_low = VALUES(ror_ci_low), ror_ci_high = VALUES(ror_ci_high),
                chi2 = VALUES(chi2), bcpnn_ic = VALUES(bcpnn_ic), ic025 = VALUES(ic025),
                n_reports = VALUES(n_reports), signal_strength = VALUES(signal_strength),
                source = VALUES(source), computed_at = NOW()
        """
        execute(sql_signals, (
            drug, event, metrics["a"], metrics["b"], metrics["c"], metrics["d"],
            metrics["prr"], metrics["prr_ci_low"], metrics["prr_ci_high"],
            metrics["ror"], metrics["ror_ci_low"], metrics["ror_ci_high"],
            metrics["chi2"], metrics["bcpnn_ic"], metrics["ic025"],
            metrics["n_reports"], metrics["signal_strength"]
        ))
        updated_count += 1

    print(f"[SUCCESS] Successfully recalculated {updated_count} signals with true global background reference.")

if __name__ == "__main__":
    recalculate_all()
