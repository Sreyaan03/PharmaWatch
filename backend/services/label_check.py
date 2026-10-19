# backend/services/label_check.py
# Is a signal's side effect described in the drug's FDA label text?
#
# A strong FAERS signal for an effect the label does not mention is the case a pharmacovigilance
# reviewer most wants to see. This searches the stored label sections (label_snapshots, kept by
# the label watcher) for the Preferred Term and a small set of variants and reviewed synonyms, and
# reports one of:
#   in_label_text          found, with the section and the sentence it is in
#   not_found_in_label_text the search did not find it (NOT "unlabelled": the match is text-based,
#                           and a label can describe an effect in words this matcher does not know)
#   no_label               no label has been stored for this drug
#
# The matcher is deliberate and explainable, not exhaustive; its precision and recall are measured
# against an author-labelled gold set (validation/label_check_benchmark.py).

import csv
import json
import os
import re

from core.config import BACKEND_DIR
from db.mysql import query_one

EVENT_SYNONYMS = os.path.join(BACKEND_DIR, "faers", "event_synonyms.csv")
# Sections searched, most specific first, so the reported location is the strongest one.
SECTION_ORDER = ["boxed_warning", "contraindications", "warnings_and_cautions", "warnings",
                 "precautions", "adverse_reactions"]
_WS = re.compile(r"\s+")
_SENT = re.compile(r"[^.;\n]*")
_synonyms_cache = None


def _pt_synonyms():
    """{lowercase PT: [extra words to search for]} inverted from the reviewed event-synonym file."""
    global _synonyms_cache
    if _synonyms_cache is None:
        table = {}
        if os.path.exists(EVENT_SYNONYMS):
            with open(EVENT_SYNONYMS, encoding="utf-8", newline="") as f:
                for row in csv.DictReader(f):
                    pt, term = row.get("pt", "").strip().lower(), row.get("term", "").strip().lower()
                    if pt and term:
                        table.setdefault(pt, []).append(term)
        _synonyms_cache = table
    return _synonyms_cache


# MedDRA Preferred Terms use British spelling; US labels use American. Apply British -> American
# only (reversing a bare "e" -> "ae" everywhere would make nonsense), plus the common digraphs.
_SPELLING = [("haem", "hem"), ("tumour", "tumor"), ("oedema", "edema"), ("paed", "ped"),
             ("anaemia", "anemia"), ("diarrhoea", "diarrhea"), ("leukaemia", "leukemia"),
             ("oesophag", "esophag"), ("caesar", "cesar"), ("gynaec", "gynec"), ("ischaemi", "ischemi"),
             ("ae", "e"), ("oe", "e")]


def _spelling_variants(word):
    """British/American spellings seen in MedDRA PTs vs US labels (British -> American)."""
    out = word
    for british, american in _SPELLING:
        out = out.replace(british, american)
    return {word, out}


def variants(pt):
    """The phrases to search a label for, given a Preferred Term. Lower case, de-duplicated."""
    base = _WS.sub(" ", (pt or "").strip().lower())
    if not base:
        return []
    forms = {base}
    forms |= _spelling_variants(base)
    words = base.split()
    if len(words) == 2:                       # MedDRA "Neuropathy peripheral" -> label "peripheral neuropathy"
        forms |= _spelling_variants(f"{words[1]} {words[0]}")
    # simple singular/plural of the whole phrase (cataract <-> cataracts)
    if base.endswith("s") and len(base) >= 5:
        forms.add(base[:-1])
    else:
        forms.add(base + "s")
    forms |= {s for s in _pt_synonyms().get(base, [])}
    return sorted({f for f in forms if len(f) >= 4})


def _find_in(text, phrase):
    """The sentence containing `phrase` as whole words, or None."""
    m = re.search(r"(?<![a-z])" + re.escape(phrase) + r"(?![a-z])", text, re.I)
    if not m:
        return None
    sentence = next((s for s in _SENT.findall(text) if m.group(0).lower() in s.lower()), "")
    return _WS.sub(" ", sentence).strip()[:300] or _WS.sub(" ", text[max(0, m.start() - 60):m.start() + 100]).strip()


def check_sections(sections, pt):
    """Search the given {section: text} for the PT. Returns the result dict."""
    phrases = variants(pt)
    for section in SECTION_ORDER:
        text = (sections or {}).get(section)
        if not text:
            continue
        for phrase in phrases:
            sentence = _find_in(text, phrase)
            if sentence is not None:
                return {"status": "in_label_text", "section": section, "matched": phrase, "sentence": sentence}
    return {"status": "not_found_in_label_text", "searched": phrases}


def stored_sections(rxcui):
    row = query_one("SELECT generic_name, sections_json, effective_time FROM label_snapshots WHERE rxcui = %s",
                    (rxcui,))
    if not row or not row.get("sections_json"):
        return None
    return {"generic_name": row["generic_name"], "effective_time": row["effective_time"],
            "sections": json.loads(row["sections_json"])}


def find(rxcui, pt, sections=None):
    """Whether `pt` is described in the drug's stored FDA label. Pass `sections` to check a label
    that is not stored (the benchmark does this)."""
    label = None
    if sections is None:
        label = stored_sections(rxcui)
        if label is None:
            return {"status": "no_label", "rxcui": rxcui, "pt": pt}
        sections = label["sections"]
    result = check_sections(sections, pt)
    result.update(rxcui=rxcui, pt=pt)
    if label:
        result["generic_name"] = label["generic_name"]
        result["label_effective_time"] = label["effective_time"]
    return result
