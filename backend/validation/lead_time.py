#!/usr/bin/env python3
"""
validation/lead_time.py — did a FAERS signal cross threshold before the FDA label changed?

For each drug-event pair that the FDA added to a safety section of the label (reference/
label_changes.csv, built by fetch_srlc.py), we find the first quarter the pair was a signal in our
Spark results (cumulative from the earliest loaded quarter) and compare it with the quarter of the
label change. Four groups are reported SEPARATELY and never merged:

  flagged_before      a signal before the label changed, with the lead time in quarters
  flagged_at_or_after a signal only in or after the quarter of the change
  left_censored       already a signal in the earliest loaded quarter (true first flag unknown)
  never_flagged       never a signal in any loaded quarter

Caveats (shown on the Methods page): only 20 quarters are loaded; reporting rises after a label
change (stimulated reporting); FAERS dates are receipt dates; label changes are not a complete list
of a drug's true adverse effects. The label-change reference is author-built by automated PT
mention match and is PENDING FACULTY REVIEW.

    python -m validation.lead_time
"""

import csv
import json
import os
import random
from datetime import datetime, timezone

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
LABEL_CHANGES = os.path.join(os.path.dirname(__file__), "reference", "label_changes.csv")
REPORTS_DIR = os.path.join(os.path.dirname(_BACKEND), "reports", "validation")

CAVEATS = [
    "Only 20 quarters (2021Q3-2026Q2) are loaded, so lead times are bounded by that window and the "
    "first-signal quarter can be left-censored.",
    "Reporting to FAERS often rises after a label change or press coverage (stimulated reporting), "
    "which can make a signal appear around the change rather than before it.",
    "FAERS dates are report-receipt dates, not event dates, and a case counts from its latest "
    "report version, so the first-signal quarter is conservative (late).",
    "Label changes are not a complete or exclusive list of a drug's true adverse effects, so this "
    "measures agreement with FDA labelling, not detection of all real harms.",
    "The label-change reference maps added label text to Preferred Terms automatically and is "
    "pending faculty review; a mismatch in that mapping mis-scores a pair.",
]


def classify(label_q, first_signal_q, quarters):
    """Group one pair and, when flagged before, its lead time in quarters.
    quarters is the ordered list of loaded quarters; label_q is the label-change quarter."""
    idx = {q: i for i, q in enumerate(quarters)}
    earliest = quarters[0]
    if first_signal_q is None:
        return "never_flagged", None
    if first_signal_q == earliest:
        return "left_censored", None
    # clamp the label quarter into the loaded range for the index comparison
    if label_q in idx:
        li = idx[label_q]
    elif label_q < earliest:
        li = 0
    else:
        li = len(quarters) - 1
    fi = idx[first_signal_q]
    if fi < li:
        return "flagged_before", li - fi
    return "flagged_at_or_after", None


def _bootstrap_ci(values, statistic, n=2000, seed=12345):
    """Percentile 95% CI of statistic over resamples of values; None if empty."""
    vals = list(values)
    if not vals:
        return None
    rng = random.Random(seed)
    stats = []
    for _ in range(n):
        sample = [vals[rng.randrange(len(vals))] for _ in vals]
        s = statistic(sample)
        if s is not None:
            stats.append(s)
    if not stats:
        return None
    stats.sort()
    lo = stats[int(0.025 * len(stats))]
    hi = stats[min(len(stats) - 1, int(0.975 * len(stats)))]
    return [round(lo, 4), round(hi, 4)]


def _median(xs):
    xs = sorted(xs)
    if not xs:
        return None
    m = len(xs) // 2
    return xs[m] if len(xs) % 2 else (xs[m - 1] + xs[m]) / 2


def compute(pairs, first_signal, quarters):
    """Pure core. pairs: {(rxcui, pt_lower): label_quarter}. first_signal: {(rxcui, pt_lower):
    first_signal_quarter or None}. Returns the summary dict (no IO)."""
    groups = {"flagged_before": 0, "flagged_at_or_after": 0, "left_censored": 0, "never_flagged": 0}
    leads, per_pair = [], []
    for key, label_q in pairs.items():
        fs = first_signal.get(key)
        group, lead = classify(label_q, fs, quarters)
        groups[group] += 1
        if lead is not None:
            leads.append(lead)
        per_pair.append({"rxcui": key[0], "pt": key[1], "label_quarter": label_q,
                         "first_signal_quarter": fs, "group": group, "lead_quarters": lead})
    total = len(pairs)
    before_flags = [1 if p["group"] == "flagged_before" else 0 for p in per_pair]
    dist = {}
    for lt in leads:
        dist[str(lt)] = dist.get(str(lt), 0) + 1
    return {
        "pairs": total,
        "groups": groups,
        "share_flagged_before": round(sum(before_flags) / total, 4) if total else None,
        "share_flagged_before_ci": _bootstrap_ci(
            before_flags, lambda s: sum(s) / len(s) if s else None),
        "median_lead_quarters": _median(leads),
        "median_lead_ci": _bootstrap_ci(leads, _median),
        "lead_time_quarters_distribution": dict(sorted(dist.items(), key=lambda kv: int(kv[0]))),
        "pairs_detail": per_pair,
    }


# ── IO ────────────────────────────────────────────────────────────────────────
def load_label_change_pairs(path=LABEL_CHANGES):
    """{(rxcui, pt_lower): earliest label-change quarter} from label_changes.csv."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"label-change reference not found at {path}; run fetch_srlc.py")
    pairs = {}
    for r in csv.DictReader(open(path, encoding="utf-8")):
        rxcui, pt, q = r.get("rxcui", ""), (r.get("pt") or "").lower(), r.get("quarter", "")
        if not rxcui or not pt or not q:
            continue
        key = (rxcui, pt)
        if key not in pairs or q < pairs[key]:
            pairs[key] = q
    return pairs


def first_signal_quarters(keys):
    """{(rxcui, pt_lower): first quarter it was a signal, or None} from the Spark results."""
    from services.surveillance import _quarters, _results_glob
    glob = _results_glob()
    if glob is None:
        raise FileNotFoundError("Spark results are not in local storage")
    quarters = _quarters(glob)
    if len(quarters) < 2:
        raise FileNotFoundError("need at least two quarters of Spark results")
    import duckdb
    con = duckdb.connect()
    try:
        rows = con.execute(f"""
            SELECT rxcui, pt_lower, MIN(quarter) AS first_q
            FROM read_parquet('{glob}', hive_partitioning = 1)
            WHERE signal_strength <> 'none'
            GROUP BY rxcui, pt_lower""").fetchall()
    finally:
        con.close()
    first = {(str(r[0]), r[1]): r[2] for r in rows}
    return {k: first.get(k) for k in keys}, quarters


def run(smoke=False):
    from services.signal_history import data_version
    pairs = load_label_change_pairs()
    first, quarters = first_signal_quarters(set(pairs))
    summary = compute(pairs, first, quarters)
    summary.update({
        "gold_set": "FDA Drug Safety-related Labeling Changes (SrLC), Jul 2021-Jun 2026, "
                    "mapped to FAERS ingredients and Preferred Terms",
        "status": "author-built by automated PT mention match; PENDING FACULTY REVIEW",
        "window": f"{quarters[0]}-{quarters[-1]}",
        "data_version": data_version(),
        "run_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "smoke": bool(smoke),
        "caveats": CAVEATS,
    })
    # keep the per-pair detail out of the published summary file; write a sidecar for inspection
    detail = summary.pop("pairs_detail")
    os.makedirs(REPORTS_DIR, exist_ok=True)
    path = os.path.join(REPORTS_DIR, "lead_time.json")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(summary, f, indent=2)
        f.write("\n")
    with open(os.path.join(REPORTS_DIR, "lead_time_pairs.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(detail, f, indent=2)
        f.write("\n")
    return summary, path


def main():
    summary, path = run()
    g = summary["groups"]
    print(f"[lead_time] {summary['pairs']} label-change pairs")
    print(f"  flagged before:      {g['flagged_before']}  (share {summary['share_flagged_before']}, "
          f"CI {summary['share_flagged_before_ci']})")
    print(f"  flagged at/after:    {g['flagged_at_or_after']}")
    print(f"  left-censored:       {g['left_censored']}")
    print(f"  never flagged:       {g['never_flagged']}")
    print(f"  median lead (qtrs):  {summary['median_lead_quarters']}  CI {summary['median_lead_ci']}")
    print(f"  wrote {path}")


if __name__ == "__main__":
    main()
