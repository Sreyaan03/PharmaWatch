# backend/validation/label_check_benchmark.py
# Measures the label-text check (services.label_check) against an author-labelled gold set
# (reference/label_check_gold.csv): for each drug-event pair, does the matcher's finding
# (in_label_text vs not_found_in_label_text) agree with whether the effect is genuinely described
# in the label?
#
# The reviewer-facing claim is "not found in the label text" (a possible new signal), so precision
# and recall are reported for that class: of the pairs the matcher calls not-in-label, how many
# truly are (precision); of the pairs truly not in the label, how many the matcher catches (recall).
#
#   python -m validation.label_check_benchmark          # needs network (openFDA); cached 24 h

import csv
import json
import os
import sys
import time

from clients.openfda import fetch_label_sections
from core.config import BACKEND_DIR, REPORTS_DIR
from services import label_check

GOLD = os.path.join(BACKEND_DIR, "validation", "reference", "label_check_gold.csv")
OUT = os.path.join(REPORTS_DIR, "validation", "label_check.json")


def load_gold(path=GOLD):
    rows = []
    with open(path, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(l for l in f if not l.startswith("#")):
            rows.append(row)
    return rows


def run(fetch=fetch_label_sections, log=print):
    gold = load_gold()
    labels, results = {}, []
    tp = fp = tn = fn = no_label = 0            # positive class = "not found in label text"
    for row in gold:
        name, pt = row["ingredient"], row["pt"]
        truly_absent = row["in_label"].strip().lower() == "no"
        if name not in labels:
            labels[name] = fetch(name)
            time.sleep(0.1)
        label = labels[name]
        if not label.get("found"):
            no_label += 1
            results.append({"ingredient": name, "pt": pt, "status": "no_label"})
            continue
        res = label_check.check_sections(label["sections"], pt)
        matcher_absent = res["status"] == "not_found_in_label_text"
        if matcher_absent and truly_absent:
            tp += 1
        elif matcher_absent and not truly_absent:
            fp += 1                             # matcher says absent, but it is in the label (a miss of the matcher)
        elif not matcher_absent and truly_absent:
            fn += 1                             # matcher found it, but it is genuinely not an effect (over-call)
        else:
            tn += 1
        results.append({"ingredient": name, "pt": pt, "gold_in_label": not truly_absent,
                        "matcher": res["status"], "section": res.get("section"),
                        "agree": matcher_absent == truly_absent})
    scored = tp + fp + tn + fn
    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None
    report = {
        "gold_set": os.path.basename(GOLD), "status": "author-labelled, pending faculty review",
        "pairs": len(gold), "scored": scored, "no_label_available": no_label,
        "positive_class": "not_found_in_label_text (a possible new signal)",
        "confusion": {"true_not_in_label": tp, "false_not_in_label": fp,
                      "true_in_label": tn, "false_in_label": fn},
        "precision_not_in_label": round(precision, 4) if precision is not None else None,
        "recall_not_in_label": round(recall, 4) if recall is not None else None,
        "accuracy": round((tp + tn) / scored, 4) if scored else None,
        "disagreements": [r for r in results if r.get("agree") is False][:20],
        "caveat": ("The matcher reads only the boxed-warning, warnings, precautions, contraindications "
                   "and adverse-reactions sections with simple variants, so an effect the label describes "
                   "in other words or sections is missed. 'Not found in label text' never means 'unlabelled'."),
        "run_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        json.dump(report, f, indent=2)
        f.write("\n")
    log(f"[+] label-text check: precision {report['precision_not_in_label']}, recall "
        f"{report['recall_not_in_label']} for 'not in label' on {scored} pairs "
        f"({no_label} had no label); wrote {OUT}")
    return report, OUT


def main(argv=None):
    from core.config import load_env
    load_env()
    run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
