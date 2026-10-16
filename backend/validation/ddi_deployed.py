"""
Scores the DEPLOYED interaction model (the one named in trained_models/ddi_manifest.json) on the
test regimes, with its served calibration applied. Cheap enough for CI.

    python -m validation.ddi_deployed            # all test pairs
    python -m validation.ddi_deployed --smoke    # fixed sample of 5,000 pairs per regime

The full model comparison and ablations are produced by validation.ddi_eval.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from core.config import MODEL_DIR, SPLITS_DIR
from validation import ddi_calibrate as C
from validation import ddi_eval as E

REPORTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                           "reports", "validation")
SMOKE_PAIRS = 5000
SEED = 5


def run(smoke=False, out_path=None):
    manifest_path = os.path.join(MODEL_DIR, "ddi_manifest.json")
    if not os.path.exists(manifest_path):
        raise FileNotFoundError("no deployment manifest; run validation.ddi_eval then validation.ddi_calibrate")
    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)

    from ml.ddi.baselines import build_drug_fingerprints
    from ml.ddi.data import precompute_drug_structures
    with open(os.path.join(SPLITS_DIR, "drug_metadata.json")) as f:
        meta = json.load(f)
    graphs, fps = precompute_drug_structures(meta)
    ctx = {"all_drugs": sorted(meta["drug_smiles"]), "graphs": graphs, "fps": fps,
           "drug_fps": build_drug_fingerprints(meta)}
    candidate = {"kind": manifest["model"]["kind"], "checkpoint": manifest["model"]["file"]}

    regimes = {}
    for name, fn in E.REGIMES.items():
        df = pd.read_csv(os.path.join(SPLITS_DIR, fn))
        if smoke and len(df) > SMOKE_PAIRS:
            df = df.sample(n=SMOKE_PAIRS, random_state=SEED)
        pairs = [(r.drug_a, r.drug_b) for r in df.itertuples(index=False)]
        y = df["label"].values.astype(float)
        raw = np.asarray(C.candidate_scores(candidate, pairs, ctx), dtype=float)
        served = C.apply_calibration(manifest["calibration"], raw) if manifest.get("calibrated") else raw
        regimes[name] = {**E.evaluate_predictions(y, served), "pairs": int(len(df)),
                         "ece_served": round(C.compute_ece(served, y)[0], 4)}

    report = {
        "benchmark": "ddi_deployed", "smoke": smoke,
        "run_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "model": manifest["model"]["name"], "model_version": manifest["model_version"],
        "selected_by": manifest.get("selected_by"), "calibrated": manifest.get("calibrated"),
        "calibration_method": (manifest.get("calibration") or {}).get("method"),
        "predicted_interaction_threshold": manifest.get("predicted_interaction_threshold"),
        "dataset": "TDC DrugBank DDI, drug-level inductive splits; 1:1 sampled negatives (base rate 0.5)",
        "regimes": regimes,
    }
    out_path = out_path or os.path.join(REPORTS_DIR, "ddi_smoke.json" if smoke else "ddi.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(report, f, indent=2, sort_keys=True)
        f.write("\n")
    return report, out_path


def main(argv=None):
    parser = argparse.ArgumentParser(description="Score the deployed DDI model.")
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args(argv)
    try:
        report, path = run(smoke=args.smoke)
    except FileNotFoundError as exc:
        print(f"[-] {exc}", file=sys.stderr)
        return 2
    for name, m in report["regimes"].items():
        print(name, m)
    print("wrote", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
