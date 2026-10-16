"""
Regression gate for the validation benchmarks.

baselines.json records, per benchmark, the metrics of a smoke run and how far each may fall:

    {"ddi_smoke": {"S1 (One Unseen).AUPRC": {"baseline": 0.82, "tolerance": 0.03}, ...}, ...}

A metric below `baseline - tolerance` fails the gate; so does a metric that has gone missing.
Rising above the baseline never fails. Changing a baseline is a deliberate commit whose message
says why.
"""

import json
import os

BASELINES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "baselines.json")


def load_baselines(path=BASELINES_PATH):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def check(metrics, expected):
    """Failure messages (empty when the gate passes). metrics: {name: value}; expected: baselines entry."""
    failures = []
    for name, spec in expected.items():
        if name.startswith("_"):
            continue
        if name not in metrics or metrics[name] is None:
            failures.append(f"{name}: no value produced (baseline {spec['baseline']})")
            continue
        floor = spec["baseline"] - spec["tolerance"]
        if metrics[name] < floor:
            failures.append(f"{name}: {metrics[name]:.4f} is below {floor:.4f} "
                            f"(baseline {spec['baseline']}, tolerance {spec['tolerance']})")
    return failures


# ── pulling the gated numbers out of each benchmark's results ────────────────
def ddi_metrics(report):
    return {f"{regime}.{metric}": m[metric]
            for regime, m in report["regimes"].items() for metric in ("AUROC", "AUPRC", "P@100")}


def signals_metrics(report):
    out = {}
    for set_name, r in report["results"].items():
        for view, tag in (("covered_pairs_only", "covered"), ("all_pairs_uncovered_as_no_signal", "all")):
            s = r[view]
            out[f"{set_name}.{tag}.auc_prr"] = s["auc_prr"]
            out[f"{set_name}.{tag}.auc_ic025"] = s["auc_ic025"]
        out[f"{set_name}.coverage"] = r["coverage"]
    return out


def ner_metrics(report):
    out = {}
    for kind in ("drugs", "effects"):
        for mode in ("exact", "overlap"):
            out[f"{kind}.{mode}.f1"] = report["entities"][kind][mode]["f1"]
    out["pairing.sentence_level.f1"] = report["pairing"]["sentence_level_rule"]["f1"]
    return out
