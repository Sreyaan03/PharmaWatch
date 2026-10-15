"""
Loaders for the committed signal-detection reference sets (see reference/README.md).

    python -m validation.reference_sets --resolve     # rebuild reference/drug_rxnorm_map.csv
    python -m validation.reference_sets --checksums   # rewrite CHECKSUMS.sha256 (a deliberate act)
"""

import csv
import hashlib
import os
import sys

REFERENCE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reference")
SETS = {"omop": "omop.csv", "euadr": "euadr.csv"}
FROZEN = ["omop.csv", "euadr.csv", "outcome_pt_map.csv", "drug_rxnorm_map.csv"]
CHECKSUMS = os.path.join(REFERENCE_DIR, "CHECKSUMS.sha256")


def _sha256(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def load_set(name):
    """[{'drug', 'outcome', 'label'(int), ...}] for 'omop' or 'euadr'."""
    with open(os.path.join(REFERENCE_DIR, SETS[name]), encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["label"] = int(r["label"])
    return rows


def load_outcome_pts():
    """{outcome: [pt, ...]} and the set of statuses present (all 'draft' until reviewed)."""
    out, statuses = {}, set()
    with open(os.path.join(REFERENCE_DIR, "outcome_pt_map.csv"), encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            out.setdefault(r["outcome"], []).append(r["pt"])
            statuses.add(r["status"])
    return out, statuses


def load_drug_map():
    """{drug: {'rxcuis': [...], 'ingredients': [...], 'resolved': bool}} from drug_rxnorm_map.csv."""
    path = os.path.join(REFERENCE_DIR, "drug_rxnorm_map.csv")
    out = {}
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            rx = [x for x in r["rxcuis"].split("|") if x]
            out[r["drug"]] = {"rxcuis": rx, "ingredients": [x for x in r["ingredients"].split("|") if x],
                              "resolved": bool(rx), "source": r["source"]}
    return out


def verify_checksums():
    """[] when every frozen file matches CHECKSUMS.sha256, else the names that differ."""
    expected = {}
    with open(CHECKSUMS, encoding="utf-8") as f:
        for line in f:
            digest, name = line.split()
            expected[name] = digest
    bad = [n for n in FROZEN if expected.get(n) != _sha256(os.path.join(REFERENCE_DIR, n))]
    return bad


def write_checksums():
    with open(CHECKSUMS, "w", encoding="utf-8", newline="\n") as f:
        for n in FROZEN:
            f.write(f"{_sha256(os.path.join(REFERENCE_DIR, n))}  {n}\n")


def resolve_drugs(identity, names):
    """Resolve each distinct drug name through the identity service (cached RxNav lookups)."""
    rows = []
    for name in sorted(set(names)):
        try:
            r = identity.resolve_drug(name)
        except Exception as exc:                      # network trouble: recorded, not guessed
            rows.append({"drug": name, "rxcuis": "", "ingredients": "", "source": f"error: {exc}"})
            continue
        rows.append({
            "drug": name,
            "rxcuis": "|".join(i["rxcui"] for i in r["ingredients"]),
            "ingredients": "|".join(i["name"] for i in r["ingredients"]),
            "source": r.get("source") or ("suggestion only" if r.get("suggestion") else "unresolved"),
        })
    return rows


def main(argv):
    if "--resolve" in argv:
        from services.signals import _identity
        names = {r["drug"] for s in SETS for r in load_set(s)}
        identity = _identity()
        rows = resolve_drugs(identity, names)
        with open(os.path.join(REFERENCE_DIR, "drug_rxnorm_map.csv"), "w", encoding="utf-8", newline="\n") as f:
            w = csv.DictWriter(f, fieldnames=["drug", "rxcuis", "ingredients", "source"], lineterminator="\n")
            w.writeheader()
            w.writerows(rows)
        n_ok = sum(1 for r in rows if r["rxcuis"])
        print(f"{n_ok}/{len(rows)} drugs resolved")
        for r in rows:
            if not r["rxcuis"]:
                print("  unresolved:", r["drug"], "-", r["source"])
    if "--checksums" in argv:
        write_checksums()
        print("wrote", CHECKSUMS)


if __name__ == "__main__":
    main(sys.argv[1:])
