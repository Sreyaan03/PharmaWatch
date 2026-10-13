# backend/services/identity.py
# One identity for every drug and event, used by every feature.
#
# Drugs resolve to RxNorm ingredient(s); events resolve to a MedDRA Preferred Term (PT) that
# is present in the loaded FAERS data. Nothing is guessed: an input that cannot be matched
# is returned as unresolved, with candidates or a suggestion for a person to confirm.

import csv
import os

import duckdb

from faers.registry import database_path

SYNONYMS_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "faers", "event_synonyms.csv")


def _norm(text):
    return " ".join((text or "").split()).lower()


def load_synonyms(path=SYNONYMS_PATH):
    """{lowercase term: {'pt', 'source', 'note'}} from the reviewed synonym table."""
    table = {}
    with open(path, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            table[_norm(row["term"])] = {"pt": row["pt"], "source": row["source"], "note": row.get("note") or ""}
    return table


class IdentityService:
    def __init__(self, db_file=None, rxnav=None, synonyms=None):
        self.db_file = db_file
        self.rxnav = rxnav
        self.synonyms = synonyms if synonyms is not None else load_synonyms()
        self._pt_cache = (None, {})

    # ── database helpers ─────────────────────────────────────────────────────
    def _path(self):
        return self.db_file or database_path()

    def _query(self, sql, params=()):
        path = self._path()
        if not os.path.exists(path):
            return []
        con = duckdb.connect(path, read_only=True)
        try:
            return con.execute(sql, list(params)).fetchall()
        except duckdb.Error:
            return []                     # table not built yet
        finally:
            con.close()

    def pt_dictionary(self):
        """{lowercase PT: (PT, report_count)} for the loaded data, refreshed when the file changes."""
        path = self._path()
        stamp = os.path.getmtime(path) if os.path.exists(path) else None
        if self._pt_cache[0] != stamp:
            rows = self._query("SELECT pt_lower, pt, n_reports FROM pt_dictionary")
            self._pt_cache = (stamp, {r[0]: (r[1], r[2]) for r in rows})
        return self._pt_cache[1]

    # ── drugs ────────────────────────────────────────────────────────────────
    def resolve_drug(self, text, live=True):
        """Resolve a drug name (brand, generic, or salt form) to RxNorm ingredient(s).

        Looks in the identity table of the loaded data first, then asks RxNav (cached) when
        `live` is set. Returns:
          {'input', 'resolved': bool, 'ingredients': [{'rxcui','name'}], 'match', 'source',
           'suggestion'}
        """
        name = _norm(text)
        out = {"input": text, "resolved": False, "ingredients": [], "match": None,
               "source": None, "suggestion": None}
        if not name:
            return out

        rows = self._query(
            "SELECT DISTINCT ingredient_rxcui, ingredient_name, match_type FROM drug_identity WHERE raw_name = ?",
            [name])
        if not rows:
            # a plain generic name: the data may only contain salt forms, but their resolved
            # ingredient is known, so this works without a network call
            rows = self._query(
                "SELECT DISTINCT ingredient_rxcui, ingredient_name, 'exact' FROM drug_identity "
                "WHERE lower(ingredient_name) = ?", [name])
        if rows:
            out.update(resolved=True, source="loaded_data", match=rows[0][2],
                       ingredients=[{"rxcui": r[0], "name": r[1]} for r in rows])
            return out

        if live and self.rxnav is not None:
            result = self.rxnav.resolve(text)
            if result["ingredients"]:
                out.update(resolved=True, source="rxnav", match=result["match"], ingredients=result["ingredients"])
            else:
                out["suggestion"] = result.get("suggestion")
        return out

    # ── events ───────────────────────────────────────────────────────────────
    def resolve_event(self, text, max_candidates=5):
        """Resolve free text to a Preferred Term present in the loaded data.

        Returns {'input', 'resolved': bool, 'pt', 'match': 'exact'|'synonym'|None, 'n_reports',
                 'note', 'candidates': [{'pt','n_reports'}]}
        """
        name = _norm(text)
        out = {"input": text, "resolved": False, "pt": None, "match": None, "n_reports": None,
               "note": "", "candidates": []}
        if not name:
            return out
        dictionary = self.pt_dictionary()

        if name in dictionary:
            pt, n = dictionary[name]
            out.update(resolved=True, pt=pt, match="exact", n_reports=n)
            return out

        syn = self.synonyms.get(name)
        if syn and _norm(syn["pt"]) in dictionary:
            pt, n = dictionary[_norm(syn["pt"])]
            out.update(resolved=True, pt=pt, match="synonym", n_reports=n, note=syn["note"])
            return out

        words = [w for w in name.split() if len(w) > 2]
        if words:
            hits = [(pt, n) for low, (pt, n) in dictionary.items() if all(w in low for w in words)]
            hits.sort(key=lambda h: -h[1])
            out["candidates"] = [{"pt": pt, "n_reports": n} for pt, n in hits[:max_candidates]]
        return out
