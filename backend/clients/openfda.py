# backend/clients/openfda.py
# openFDA queries: FAERS co-prescription counts and label interaction text.

from clients.http import http_requests
from clients.pubchem import _pubchem_synonyms

# ── openFDA and FAERS queries ────────────────────────────────────────────────
def _query_faers_coprescription(name_a: str, name_b: str) -> int:
    """Returns the count of FAERS AE reports where Drug A and Drug B appear together."""
    FDA_BASE = "https://api.fda.gov/drug/event.json"
    try:
        url = (f'{FDA_BASE}?search=patient.drug.medicinalproduct:"{name_a}"'
               f'+AND+patient.drug.medicinalproduct:"{name_b}"&limit=1')
        res = http_requests.get(url, timeout=8).json()
        count = res.get("meta", {}).get("results", {}).get("total", 0)
        if count > 0:
            return count
    except Exception:
        return 0

    try:
        syns_a = _pubchem_synonyms(name_a, max_syns=2)
        syns_b = _pubchem_synonyms(name_b, max_syns=2)
        sa = syns_a[1] if len(syns_a) > 1 else name_a
        sb = syns_b[1] if len(syns_b) > 1 else name_b
        if sa != name_a or sb != name_b:
            url = (f'{FDA_BASE}?search=patient.drug.medicinalproduct:"{sa}"'
                   f'+AND+patient.drug.medicinalproduct:"{sb}"&limit=1')
            res = http_requests.get(url, timeout=3).json()
            return res.get("meta", {}).get("results", {}).get("total", 0)
    except Exception:
        pass
    return 0

def _query_openfda_label_interaction(name_a: str, name_b: str) -> dict:
    """Checks openFDA drug label for name_a to see if name_b is mentioned in safety sections."""
    LABEL_BASE = "https://api.fda.gov/drug/label.json"
    try:
        url = f'{LABEL_BASE}?search=openfda.generic_name:"{name_a}"&limit=1'
        res = http_requests.get(url, timeout=4).json()
        if "results" not in res:
            return {"documented": False, "severity": "unknown", "found_in": "", "snippet": ""}

        label = res["results"][0]
        interactions_text = " ".join(label.get("drug_interactions", [""])).lower()
        warnings_text     = " ".join(label.get("warnings", [""])).lower()
        contraindications = " ".join(label.get("contraindications", [""])).lower()

        candidates = [name_b.lower()] + [s.lower() for s in _pubchem_synonyms(name_b, max_syns=3)[1:]]

        found_in = ""
        snippet  = ""
        for target in candidates:
            if target in contraindications:
                found_in = "contraindications"
                idx = contraindications.find(target)
                snippet = contraindications[max(0, idx-60):idx+100].strip()
                break
            elif target in warnings_text:
                found_in = "warnings"
                idx = warnings_text.find(target)
                snippet = warnings_text[max(0, idx-60):idx+100].strip()
                break
            elif target in interactions_text:
                found_in = "drug_interactions"
                idx = interactions_text.find(target)
                snippet = interactions_text[max(0, idx-60):idx+100].strip()
                break

        if not found_in:
            return {"documented": False, "severity": "unknown", "found_in": "", "snippet": ""}

        context = snippet + " " + interactions_text[:500]
        severity = "minor"
        for kw, sev in [
            ("contraindicated", "contraindicated"),
            ("avoid",          "major"),
            ("fatal",          "major"),
            ("serious",        "major"),
            ("monitor",        "moderate"),
            ("caution",        "moderate"),
            ("may increase",   "moderate"),
            ("may decrease",   "moderate"),
        ]:
            if kw in context:
                severity = sev
                break

        return {
            "documented": True,
            "severity":   severity,
            "found_in":   found_in,
            "snippet":    snippet[:220]
        }
    except Exception:
        return {"documented": False, "severity": "unknown", "found_in": "", "snippet": ""}
