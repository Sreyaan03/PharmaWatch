"""
One-off conversion of the two published reference sets from the OHDSI MethodEvaluation package's
R data files (omopReferenceSet.rda, euadrReferenceSet.rda) into the CSVs committed under
backend/validation/reference/.

Needs `pyreadr`, which is not a project dependency (the committed CSVs are what the app and CI
use):

    pip install --target <dir> pyreadr
    python scripts/validation/convert_reference_sets.py <rda_dir> <pyreadr_dir>

Source: https://github.com/OHDSI/MethodEvaluation (data/*.rda). The sets originate from
  OMOP:   Ryan PB et al. Drug Saf 2013;36 Suppl 1:S33-47. doi:10.1007/s40264-013-0097-8
  EU-ADR: Coloma PM et al. Drug Saf 2013;36(1):13-23.     doi:10.1007/s40264-012-0002-x
"""

import csv
import hashlib
import os
import sys

OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "backend", "validation", "reference")

# The package's outcome names are internal labels; these keys are what the rest of the code uses.
OUTCOME_KEYS = {
    "HOI Upper GI #3": "upper_gi_bleeding",
    "OMOP Acute Liver Failure 1": "acute_liver_injury",
    "OMOP Acute Renal Failure 1": "acute_kidney_injury",
    "OMOP Acute myocardial Infarction  1": "acute_myocardial_infarction",
    "Anaphylaxis #1": "anaphylaxis",
    "Cardiac Valve Fibrosis #1": "cardiac_valve_fibrosis",
    "Leukopenia Including Neutropenia and Agranulocytosis": "leukopenia_neutropenia_agranulocytosis",
    "OMOP Aplastic Anemia 1": "aplastic_anaemia",
    "Rhabdomyolysis #1": "rhabdomyolysis",
    "Stevens-Johnson Syndrome #1": "stevens_johnson_syndrome",
}


def main(rda_dir, pyreadr_dir):
    sys.path.insert(0, pyreadr_dir)
    import pyreadr

    os.makedirs(OUT_DIR, exist_ok=True)
    sums = []
    for rda_name, out_name in (("omopReferenceSet", "omop.csv"), ("euadrReferenceSet", "euadr.csv")):
        df = list(pyreadr.read_r(os.path.join(rda_dir, f"{rda_name}.rda")).values())[0]
        rows = []
        for r in df.itertuples():
            key = OUTCOME_KEYS[r.outcomeName]
            rows.append({
                "drug": str(r.exposureName).strip().lower(),
                "outcome": key,
                "label": int(r.groundTruth),
                "omop_exposure_id": int(r.exposureId),
                "omop_outcome_id": int(r.outcomeId),
                "source_outcome_name": r.outcomeName,
            })
        rows.sort(key=lambda x: (x["outcome"], -x["label"], x["drug"]))
        path = os.path.join(OUT_DIR, out_name)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator="\n")
            w.writeheader()
            w.writerows(rows)
        digest = hashlib.sha256(open(path, "rb").read()).hexdigest()
        sums.append(f"{digest}  {out_name}\n")
        print(out_name, len(rows), "rows", digest[:12])

    print("rda sha256 (record in README):")
    for rda_name in ("omopReferenceSet", "euadrReferenceSet"):
        p = os.path.join(rda_dir, f"{rda_name}.rda")
        print(" ", rda_name, hashlib.sha256(open(p, "rb").read()).hexdigest())


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
