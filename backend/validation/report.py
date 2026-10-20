"""
Builds reports/validation/summary.json from the component result files.

The Methods & Validation page shows only what is in this file: each number is read from a
component's own results file, with its reference set, data version and run date.
"""

import json
import os
from datetime import datetime, timezone

REPORTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                           "reports", "validation")

KNOWN_LIMITATIONS = [
    "FAERS holds spontaneous reports. Disproportionality finds reporting patterns; it cannot show "
    "causation, incidence or relative risk in patients.",
    "The signal benchmark's outcome definitions (MedDRA Preferred Terms per outcome) are a draft "
    "that no clinical reviewer has signed off.",
    "The OMOP reference set's negative controls are imperfect; a published audit found a "
    "substantial share (about 17%) may be misclassified.",
    "The EU-ADR copy used has 93 pairs; the original paper reports 94.",
    "Interaction-model test sets use sampled negatives (unlabelled pairs, not confirmed "
    "non-interactions) at a 1:1 ratio, so precision at k is read against a base rate of 0.5.",
    "NER precision is a lower bound: the ADE corpus annotates only entities in annotated relations.",
    "NER results come from published case-report text, not from prescriptions or social media.",
    "Prescription extraction was measured on synthetic printed documents. Handwriting is untested, and "
    "brand names missing from FAERS (for example many Indian brands) are not recognised; they are flagged for checking.",
    "The lead-time study compares FAERS signals with FDA label changes over only 20 quarters; "
    "reporting rises after label changes, FAERS dates are receipt dates, and the label-change "
    "reference maps label text to Preferred Terms automatically and is pending faculty review.",
]


def _load(name, smoke):
    base = name + ("_smoke" if smoke and name not in SAME_FILE_FOR_SMOKE else "")
    path = os.path.join(REPORTS_DIR, base + ".json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _signals_component(d):
    rows = []
    for set_name, r in d["results"].items():
        for view, label in (("covered_pairs_only", "pairs with 3+ cases"),
                            ("all_pairs_uncovered_as_no_signal", "all pairs (uncovered = no signal)")):
            s = r[view]
            rows.append({"set": f"{set_name.upper()} - {label}", "pairs": s["pairs"],
                         "metrics": {
                             "AUC (PRR)": [s["auc_prr"], s["auc_prr_ci"]],
                             "AUC (ROR lower bound)": [s["auc_ror_lower"], s["auc_ror_lower_ci"]],
                             "AUC (IC025)": [s["auc_ic025"], s["auc_ic025_ci"]],
                             "Sensitivity (signal rule)": [s["signal_sensitivity"], s["signal_sensitivity_ci"]],
                             "Specificity (signal rule)": [s["signal_specificity"], s["signal_specificity_ci"]],
                         }})
        rows.append({"set": f"{set_name.upper()} - coverage", "pairs": r["reference_pairs"],
                     "metrics": {"Pairs with 3+ cases": [r["coverage"], None]}})
    return {"title": "Signal detection", "reference": "OMOP and EU-ADR reference sets",
            "data_version": d.get("data_version"), "run_at": d.get("run_at"), "rows": rows,
            "caveat": "Outcome definitions are a draft pending clinical review."
            if "draft" in d.get("outcome_definitions", {}).get("status", []) else None}


def _ddi_component(d):
    rows = [{"set": regime, "pairs": m.get("pairs"),
             "metrics": {"AUROC": [m["AUROC"], None], "AUPRC": [m["AUPRC"], None],
                         "Precision@100": [m["P@100"], None], "ECE (served score)": [m["ece_served"], None]}}
            for regime, m in d["regimes"].items()]
    return {"title": "Drug interaction model", "reference": d.get("dataset"),
            "model": f"{d.get('model')} ({d.get('model_version')})",
            "calibrated": d.get("calibrated"), "run_at": d.get("run_at"), "rows": rows,
            "data_version": None, "caveat": None}


def _ner_component(d):
    rows = []
    for kind, label in (("drugs", "Drug mentions"), ("effects", "Adverse-effect mentions")):
        for mode in ("exact", "overlap"):
            m = d["entities"][kind][mode]
            rows.append({"set": f"{label} - {mode} match", "pairs": m["gold"],
                         "metrics": {"Precision": [m["precision"], None], "Recall": [m["recall"], None],
                                     "F1": [m["f1"], None]}})
        rows.append({"set": f"{label} - fragments", "pairs": d["entities"][kind]["fragments"],
                     "metrics": {"Predictions that are only part of an entity": [d["entities"][kind]["fragment_share"], None]}})
    for rule in ("all_pairs_rule (current)", "sentence_level_rule"):
        m = d["pairing"][rule]
        rows.append({"set": f"Drug-effect pairing - {rule}", "pairs": m["gold"],
                     "metrics": {"Precision": [m["precision"], None], "Recall": [m["recall"], None], "F1": [m["f1"], None]}})
    return {"title": "Text mining (NER)", "reference": d["dataset"]["name"], "model": d["model"],
            "data_version": d["dataset"]["sha256"][:12], "run_at": d.get("run_at"), "rows": rows,
            "caveat": "Precision is a lower bound (see limitations)."}


def _prescription_component(d):
    o = d["overall"] or {}
    pct = lambda k: [o.get(k), None]                                                     # noqa: E731
    rows = [{"set": "All documents", "pairs": o.get("documents_lines"),
             "metrics": {"Lines found": pct("line_recall"), "Lines that are real": pct("line_precision"),
                         "Drug identified": pct("ingredient_accuracy"), "Strength": pct("strength_accuracy"),
                         "Frequency": pct("frequency_accuracy"), "Duration": pct("duration_accuracy"),
                         "Route": pct("route_accuracy")}}]
    for key, label in (("by_format", "format"), ("by_image_quality", "image quality")):
        for name, v in (d.get(key) or {}).items():
            rows.append({"set": f"{label}: {name}", "pairs": v.get("documents_lines"),
                         "metrics": {"Lines found": [v.get("line_recall"), None],
                                     "Drug identified": [v.get("ingredient_accuracy"), None],
                                     "Frequency": [v.get("frequency_accuracy"), None]}})
    rev = d.get("review") or {}
    if rev.get("recall") is not None:
        rows.append({"set": "Review: planted findings reported", "pairs": rev["expected"],
                     "metrics": {"Recall": [rev["recall"], None]}})
    return {"title": "Prescription extraction", "reference": d["test_set"]["note"],
            "model": f"{d['extractor']} ({d['model']})", "data_version": d["dictionary"], "run_at": d.get("run_at"),
            "rows": rows,
            "caveat": "Synthetic printed prescriptions only; no handwriting has been tested. Invented lines: "
                      f"{o.get('lines_invented')}; wrong drug identities flagged for checking: "
                      f"{o.get('wrong_ingredients_flagged_for_checking')}."}


def _scaling_component(d):
    rows = [{"set": f"Check: {name.replace('_', ' ')}", "pairs": None,
             "metrics": {"Passed": [1.0 if c.get("passed") else 0.0, None]}}
            for name, c in (d.get("checks") or {}).items()]
    m = d.get("measurements") or {}
    for key, label in (("workers_ocr_jobs", "default threads"), ("workers_ocr_jobs_shared_cores", "threads = cores / workers")):
        for r in m.get(key) or []:
            rows.append({"set": f"Workers: {r['workers']}, {label} ({r['jobs']} OCR jobs)", "pairs": r["jobs"],
                         "metrics": {"Jobs per minute": [r["jobs_per_minute"], None], "Seconds": [r["seconds"], None]}})
    for r in m.get("web_copies_load") or []:
        rows.append({"set": f"Web copies: {r['web_copies']} ({r['concurrency']} concurrent clients)", "pairs": r["requests"],
                     "metrics": {"Requests per second": [r["per_second"], None], "p95 ms": [r["p95_ms"], None],
                                 "Errors": [r["errors"], None]}})
    mach = d.get("machine") or {}
    return {"title": "Architecture: scale-out", "reference": d.get("profile"),
            "model": None, "data_version": f"{mach.get('cpus')} CPUs, {mach.get('ram_gb')} GB RAM, one machine",
            "run_at": d.get("run_at"), "rows": rows, "caveat": d.get("note")}


def _bigdata_component(d):
    sp, dk, ag = d.get("spark") or {}, d.get("duckdb") or {}, d.get("agreement") or {}
    rows = [{"set": f"Spark {sp.get('version')} ({sp.get('master')}, {sp.get('parallelism')} cores)", "pairs": sp.get("rows"),
             "metrics": {"Seconds": [sp.get("seconds"), None], "Seconds incl. start-up": [sp.get("seconds_with_startup"), None]}},
            {"set": f"DuckDB {dk.get('version')} (same job)", "pairs": dk.get("rows"),
             "metrics": {"Seconds": [dk.get("seconds"), None]}},
            {"set": "Spark vs DuckDB: every pair-quarter compared", "pairs": ag.get("spark_rows"),
             "metrics": {"Rows in only one engine": [ag.get("rows_in_only_one_engine"), None],
                         "Rows differing": [ag.get("rows_differing"), None]}}]
    check = _load("spark_agreement", smoke=False)
    if check:
        rows.append({"set": "Spark vs the app's own statistics code (sampled)", "pairs": check.get("checked"),
                     "metrics": {"Mismatches": [check.get("mismatches"), None]}})
    return {"title": "Batch job: all pairs, all quarters (Spark)", "reference": d.get("job"), "model": None,
            "data_version": (check or {}).get("data_version"), "run_at": d.get("run_at"), "rows": rows,
            "caveat": d.get("note")}


def _label_check_component(d):
    c = d.get("confusion") or {}
    rows = [{"set": "Not-in-label detection (possible new signals)", "pairs": d.get("scored"),
             "metrics": {"Precision": [d.get("precision_not_in_label"), None],
                         "Recall": [d.get("recall_not_in_label"), None],
                         "Accuracy": [d.get("accuracy"), None]}},
            {"set": "Confusion (positive = not in label)", "pairs": d.get("scored"),
             "metrics": {"True not-in-label": [c.get("true_not_in_label"), None],
                         "Missed by matcher (in label, called absent)": [c.get("false_not_in_label"), None],
                         "Over-called (absent effect, called present)": [c.get("false_in_label"), None]}}]
    return {"title": "Label-text check", "reference": d.get("gold_set"), "model": None,
            "data_version": None, "run_at": d.get("run_at"), "rows": rows,
            "caveat": f"{d.get('status')}. {d.get('caveat')}"}


def _lead_time_component(d):
    g = d.get("groups") or {}
    rows = [{"set": "Flagged before the label change", "pairs": d.get("pairs"),
             "metrics": {"Pairs": [g.get("flagged_before"), None],
                         "Share of all pairs": [d.get("share_flagged_before"), d.get("share_flagged_before_ci")],
                         "Median lead (quarters)": [d.get("median_lead_quarters"), d.get("median_lead_ci")]}},
            {"set": "Flagged at or after the change", "pairs": None,
             "metrics": {"Pairs": [g.get("flagged_at_or_after"), None]}},
            {"set": "Left-censored (signal already present at the start of our data)", "pairs": None,
             "metrics": {"Pairs": [g.get("left_censored"), None]}},
            {"set": "Never flagged in any loaded quarter", "pairs": None,
             "metrics": {"Pairs": [g.get("never_flagged"), None]}}]
    return {"title": "Early-warning lead time vs FDA label changes", "reference": d.get("gold_set"),
            "model": None, "data_version": d.get("data_version"), "run_at": d.get("run_at"), "rows": rows,
            "caveat": f"{d.get('status')}. Window {d.get('window')}. " + " ".join(d.get("caveats", []))}


BUILDERS = {"signals": _signals_component, "ddi": _ddi_component, "ner": _ner_component,
            "prescription": _prescription_component, "scaling": _scaling_component,
            "bigdata_timing": _bigdata_component, "label_check": _label_check_component,
            "lead_time": _lead_time_component}
SAME_FILE_FOR_SMOKE = {"scaling", "bigdata_timing", "label_check", "lead_time"}   # measured runs, never a smoke run


def build_summary(smoke=False, write=True):
    components, missing = {}, []
    for name, build in BUILDERS.items():
        data = _load(name, smoke)
        if data is None:
            missing.append(name)
            continue
        comp = build(data)
        comp["smoke"] = bool(data.get("smoke"))
        components[name] = comp
    summary = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "smoke": smoke, "components": components, "not_run": missing,
        "known_limitations": KNOWN_LIMITATIONS,
    }
    if write:
        os.makedirs(REPORTS_DIR, exist_ok=True)
        with open(os.path.join(REPORTS_DIR, "summary.json"), "w", encoding="utf-8", newline="\n") as f:
            json.dump(summary, f, indent=2)
            f.write("\n")
    return summary
