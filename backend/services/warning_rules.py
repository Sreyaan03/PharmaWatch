# backend/services/warning_rules.py
# Rules for the boxed-warning features that do not need the network: which year a warning was
# issued (only from a sourced record), and whether a reported event is named in a warning text.

import csv
import os
import re
from datetime import datetime

DATES_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "data", "boxed_warning_dates.csv")

REPORTING_CAVEAT = (
    "Report counts reflect reporting behaviour (publicity, litigation, regulatory attention, "
    "changes to the reporting system) as well as how many patients take the drug and how many "
    "are affected. They are not rates and do not show what a warning changed."
)


def _norm(text):
    return " ".join((text or "").replace("_", " ").replace("^", "'").split()).lower()


# ── warning year ──────────────────────────────────────────────────────────────
def load_warning_dates(path=DATES_PATH):
    """{ingredient: {'year', 'source_url', 'verified_by'}} from the sourced dates file.

    A row without a year or without a source_url is ignored: an unsourced year is not recorded.
    """
    table = {}
    try:
        with open(path, encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                name = _norm(row.get("ingredient"))
                year = (row.get("year") or "").strip()
                source = (row.get("source_url") or "").strip()
                if name and year.isdigit() and source:
                    table[name] = {"year": int(year), "source_url": source,
                                   "verified_by": (row.get("verified_by") or "").strip()}
    except FileNotFoundError:
        pass
    return table


def resolve_warning_year(drug, override=None, dates=None):
    """The year a boxed warning was issued, and where that came from.

    Returns {'year', 'origin', 'source_url', 'verified_by'}; year is None when there is no
    sourced answer. `override` (a request parameter) wins and is labelled as supplied by the
    caller. A year from the label's own revision date is never used as the warning year.
    """
    if override not in (None, ""):
        try:
            year = int(str(override).strip())
            if 1950 <= year <= datetime.now().year:
                return {"year": year, "origin": "request", "source_url": None, "verified_by": None}
        except ValueError:
            pass
    table = dates if dates is not None else load_warning_dates()
    hit = table.get(_norm(drug))
    if hit:
        return {"year": hit["year"], "origin": "dates_file", "source_url": hit["source_url"],
                "verified_by": hit["verified_by"]}
    return {"year": None, "origin": None, "source_url": None, "verified_by": None}


# ── event matching ────────────────────────────────────────────────────────────
def _phrase_regex(phrase):
    return re.compile(r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])")


def synonyms_by_term(synonym_table):
    """{preferred term (lowercase): [synonym, ...]} from the reviewed synonym table."""
    out = {}
    for term, info in synonym_table.items():
        out.setdefault(_norm(info["pt"]), []).append(term)
    return out


def event_in_warning(term, warning_text, synonyms=None):
    """How a reported event term is named in a warning text, or None.

    The whole term must appear as a phrase (so "pain" in a warning does not match "abdominal
    pain upper"), or one of its reviewed synonyms must. Returns {'matched': phrase, 'by': 'term'|'synonym'}.
    """
    text = _norm(warning_text)
    if not text:
        return None
    wanted = _norm(term)
    if wanted and _phrase_regex(wanted).search(text):
        return {"matched": wanted, "by": "term"}
    for synonym in (synonyms or {}).get(wanted, []):
        if _phrase_regex(synonym).search(text):
            return {"matched": synonym, "by": "synonym"}
    return None
