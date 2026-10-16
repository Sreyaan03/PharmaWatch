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
]


def _load(name, smoke):
    base = name + ("_smoke" if smoke else "")
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


BUILDERS = {"signals": _signals_component, "ddi": _ddi_component, "ner": _ner_component}


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
