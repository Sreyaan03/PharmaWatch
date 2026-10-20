#!/usr/bin/env python3
"""
validation/fetch_srlc.py — build the label-change reference for the lead-time study.

Reads reference/srlc_index.csv (FDA's Drug Safety-related Labeling Changes export: drug, active
ingredient, application number, supplement date, detail-page link), keeps the drugs that map to a
FAERS ingredient we hold and that changed in the loaded window, fetches each drug's SrLC detail
page, and parses every safety-section change: its date, the labeling section, and the text added
(the underlined additions). The added text is matched against our FAERS Preferred Terms with the
same MentionIndex the Kafka consumer uses, so each label change becomes zero or more
(ingredient, Preferred Term, date, section) rows in our own vocabulary.

The drug->effect->PT mapping is automated (PT mention match) and marked PENDING FACULTY REVIEW,
the same bar as the outcome map and the label-check gold set. Output: reference/label_changes.csv.

    python -m validation.fetch_srlc                 # full run (~1600 pages, cached)
    python -m validation.fetch_srlc --limit 5       # pilot: first 5 matched drugs
    python -m validation.fetch_srlc --ids 339,2183  # specific DrugNameIDs

Pages are cached under DATA_DIR/srlc_cache so re-runs and the pilot do not refetch.
"""

import argparse
import csv
import os
import re
import time

from core import config
from faers.mentions import MentionIndex
from faers.registry import database_path

INDEX_CSV = os.path.join(os.path.dirname(__file__), "reference", "srlc_index.csv")
OUT_CSV = os.path.join(os.path.dirname(__file__), "reference", "label_changes.csv")
DETAIL_URL = ("https://www.accessdata.fda.gov/scripts/cder/safetylabelingchanges/"
              "index.cfm?event=searchdetail.page&DrugNameID={}")

# Loaded FAERS window: 2021Q3..2026Q2 -> July 2021 to June 2026 inclusive (YYYYMM bounds).
WINDOW_LO, WINDOW_HI = 202107, 202606

# Salt / form words stripped from an SrLC active ingredient to reach the FAERS base ingredient.
SALTS = {"SODIUM", "HYDROCHLORIDE", "SULFATE", "CALCIUM", "POTASSIUM", "MESYLATE", "MALEATE",
         "TARTRATE", "CITRATE", "ACETATE", "FUMARATE", "SUCCINATE", "PHOSPHATE", "HYDROBROMIDE",
         "BESYLATE", "TOSYLATE", "DIHYDROCHLORIDE", "BITARTRATE", "DISODIUM", "DIMALEATE",
         "MONOHYDRATE", "DIMESYLATE", "HYCLATE", "MEGLUMINE", "HEMIHYDRATE", "PAMOATE", "OLAMINE",
         "ETABONATE", "XINAFOATE", "PROPIONATE", "FUROATE", "DIPROPIONATE", "VALERATE", "ENANTHATE",
         "CYPIONATE", "UNDECANOATE", "DIPHOSPHATE", "CAMSYLATE", "LAUROXIL", "TRIFENATATE", "AXETIL",
         "MALATE", "BROMIDE", "CHLORIDE", "NITRATE", "LACTATE", "GLUCONATE", "CARBONATE", "DECANOATE",
         "ESYLATE", "KAMEDOXOMIL", "MEDOXOMIL", "ETEXILATE", "DIFUMARATE", "ARGININE", "LYSINE",
         "CHOLINE", "TROMETHAMINE", "RECOMBINANT", "DIASPARTATE", "SUCCINATE;"}

# Safety sections we score. Keyword -> canonical label. Order matters (longest/most specific first).
SAFETY_SECTIONS = [
    ("boxed warning", "boxed_warning"),
    ("warnings and precautions", "warnings_and_cautions"),
    ("contraindications", "contraindications"),
    ("adverse reactions", "adverse_reactions"),
]
# Top-level PLR section names, used to bound where one section's additions end.
TOP_SECTIONS = ["boxed warning", "indications and usage", "dosage and administration",
                "dosage forms and strengths", "contraindications", "warnings and precautions",
                "adverse reactions", "drug interactions", "use in specific populations",
                "drug abuse and dependence", "overdosage", "description", "clinical pharmacology",
                "nonclinical toxicology", "clinical studies", "references", "how supplied",
                "patient counseling", "medication guide", "instructions for use"]


def normalize_ingredient(ing):
    """SrLC active-ingredient string -> set of FAERS-style base ingredient names."""
    out = set()
    for part in re.split(r"[;,/]", (ing or "").upper()):
        toks = [t for t in re.split(r"\s+", part.strip()) if t]
        while toks and toks[-1] in SALTS:
            toks.pop()
        if toks:
            out.add(" ".join(toks))
    return out


def in_window(date_str):
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})", date_str or "")
    if not m:
        return False
    mm, _, yy = int(m.group(1)), int(m.group(2)), int(m.group(3))
    return WINDOW_LO <= yy * 100 + mm <= WINDOW_HI


def date_to_quarter(date_str):
    """mm/dd/yyyy -> 'YYYYqN' (the quarter the label change took effect)."""
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})", date_str)
    mm, yy = int(m.group(1)), int(m.group(3))
    return f"{yy}q{(mm - 1) // 3 + 1}"


def load_matches(faers_names):
    """Rows of srlc_index.csv that map to a FAERS ingredient and changed in window.
    Returns {drug_name_id: {drug, ingredient, rxcui?}} (deduped by detail page)."""
    rows = list(csv.DictReader(open(INDEX_CSV, encoding="utf-8-sig")))
    matched = {}
    for r in rows:
        if not in_window(r["Supplement Date"]):
            continue
        hit = normalize_ingredient(r["Active Ingredient"]) & faers_names
        if not hit:
            continue
        did = re.search(r"DrugNameID=(\d+)", r["Link"])
        did = did.group(1) if did else None
        if not did:
            continue
        matched.setdefault(did, {"drug": r["Drug Name"], "ingredient": sorted(hit)[0]})
    return matched


def fetch_detail(drug_name_id, cache_dir, session):
    path = os.path.join(cache_dir, f"{drug_name_id}.html")
    if os.path.exists(path) and os.path.getsize(path) > 2000:
        return open(path, encoding="utf-8", errors="replace").read()
    resp = session.get(DETAIL_URL.format(drug_name_id), timeout=30)
    resp.raise_for_status()
    html = resp.text
    if "abuse-detection" in html or len(html) < 2000:
        raise RuntimeError(f"blocked or empty for DrugNameID={drug_name_id}")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return html


def parse_changes(html):
    """Yield (date_str, section_label, added_text) for safety-section additions in the page."""
    panels = re.split(r'(?is)<h3 class="background_text accordion-header', html)
    for seg in panels[1:]:
        hdr = re.match(r'(?is).*?>\s*(\d{2}/\d{2}/\d{4})', seg)
        if not hdr:
            continue
        date_str = hdr.group(1)
        if not in_window(date_str):
            continue
        # flatten the panel, marking underlined additions with [[+ ... +]]
        text = re.sub(r"(?is)<u>", "[[+", seg)
        text = re.sub(r"(?is)</u>", "+]]", text)
        text = re.sub(r"(?is)<[^>]+>", " ", text)
        text = re.sub(r"&nbsp;", " ", text)
        text = re.sub(r"\s+", " ", text).strip().lower()
        # locate each safety section and the slice until the next top-level section
        low = text
        for key, label in SAFETY_SECTIONS:
            for mm in re.finditer(re.escape(key), low):
                start = mm.end()
                # next top-level section heading after this point bounds the slice
                ends = [low.find(t, start) for t in TOP_SECTIONS if 0 <= low.find(t, start)]
                stop = min(ends) if ends else len(low)
                chunk = low[start:stop]
                adds = re.findall(r"\[\[\+(.*?)\+\]\]", chunk)
                added = " ".join(a.strip() for a in adds if a.strip())
                if len(added) >= 6:                       # ignore trivial underlined fragments
                    yield date_str, label, added


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="only the first N matched drugs (pilot)")
    ap.add_argument("--ids", default="", help="comma-separated DrugNameIDs (overrides matching)")
    ap.add_argument("--delay", type=float, default=0.4, help="seconds between fetches")
    args = ap.parse_args()

    import duckdb
    con = duckdb.connect(database_path(), read_only=True)
    name_to_rxcui = {}
    for rxcui, name in con.execute(
            "SELECT ingredient_rxcui, UPPER(ingredient_name) FROM drug_identity").fetchall():
        name_to_rxcui.setdefault(name, str(rxcui))
    con.close()
    faers_names = set(name_to_rxcui)
    index = MentionIndex.from_database(database_path())

    if args.ids:
        matched = {i.strip(): {"drug": "", "ingredient": ""} for i in args.ids.split(",") if i.strip()}
    else:
        matched = load_matches(faers_names)
    ids = list(matched)
    if args.limit:
        ids = ids[:args.limit]
    print(f"[srlc] {len(ids)} detail pages to fetch")

    cache_dir = os.path.join(config.get("DATA_DIR"), "srlc_cache")
    os.makedirs(cache_dir, exist_ok=True)

    import requests
    session = requests.Session()
    session.headers.update({"User-Agent": config.get("SRLC_USER_AGENT"),
                            "Accept": "text/html,application/xhtml+xml",
                            "Accept-Language": "en-US,en;q=0.9"})

    seen, out_rows, errors = set(), [], 0
    for n, did in enumerate(ids, 1):
        try:
            fresh = not os.path.exists(os.path.join(cache_dir, f"{did}.html"))
            html = fetch_detail(did, cache_dir, session)
        except Exception as e:
            errors += 1
            print(f"[srlc] {did}: {e}")
            continue
        info = matched.get(did, {})
        ing = (info.get("ingredient") or "").upper()
        rxcui = name_to_rxcui.get(ing, "")
        for date_str, section, added in parse_changes(html):
            for pt in index.pts_in(added):
                key = (rxcui or ing or did, pt["pt"].lower(), section, date_to_quarter(date_str))
                if key in seen:
                    continue
                seen.add(key)
                out_rows.append({
                    "rxcui": rxcui, "ingredient": ing, "drug_name_id": did,
                    "drug_name": info.get("drug", ""), "pt": pt["pt"],
                    "section": section, "date": date_str, "quarter": date_to_quarter(date_str),
                    "matched_text": pt["matched"], "source_phrase": added[:300],
                })
        if fresh and args.delay:
            time.sleep(args.delay)
        if n % 100 == 0:
            print(f"[srlc] {n}/{len(ids)} pages, {len(out_rows)} change-PT rows so far")

    fields = ["rxcui", "ingredient", "drug_name_id", "drug_name", "pt", "section",
              "date", "quarter", "matched_text", "source_phrase"]
    with open(OUT_CSV, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(out_rows)
    pairs = {(r["rxcui"] or r["ingredient"], r["pt"].lower()) for r in out_rows}
    print(f"[srlc] done: {len(ids)} pages, {errors} errors, {len(out_rows)} rows, "
          f"{len(pairs)} distinct (ingredient, PT) label-change pairs")
    print(f"[srlc] wrote {OUT_CSV}  (automated PT mapping, PENDING FACULTY REVIEW)")


if __name__ == "__main__":
    main()
