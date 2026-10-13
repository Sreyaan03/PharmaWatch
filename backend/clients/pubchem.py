# backend/clients/pubchem.py
# PubChem name/SMILES/CID resolution and the local fast-path drug registry.

import re as _re
import requests as original_requests

PUBCHEM_BASE = "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound"

# ── PubChem Helpers ──────────────────────────────────────────────────────────
def _is_smiles(text: str) -> bool:
    """Heuristic: SMILES strings contain chemistry chars not found in drug names."""
    return bool(_re.search(r'[=#@\[\]\\\/\+\-]|\d', text)) and ' ' not in text.strip()

def _pubchem_name_to_cid(name: str):
    """Resolve a drug name to a PubChem CID. Returns int or None."""
    try:
        url = f"{PUBCHEM_BASE}/name/{original_requests.utils.quote(name)}/cids/JSON"
        r = original_requests.get(url, timeout=8)
        if r.status_code == 200:
            cids = r.json().get("IdentifierList", {}).get("CID", [])
            return cids[0] if cids else None
    except Exception:
        pass
    return None

def _pubchem_smiles_to_cid(smiles: str):
    """Resolve a SMILES string to a PubChem CID. Returns int or None."""
    try:
        url = f"{PUBCHEM_BASE}/smiles/{original_requests.utils.quote(smiles)}/cids/JSON"
        r = original_requests.get(url, timeout=8)
        if r.status_code == 200:
            cids = r.json().get("IdentifierList", {}).get("CID", [])
            return cids[0] if cids else None
    except Exception:
        pass
    return None

def _pubchem_cid_to_smiles(cid: int):
    """Fetch the canonical SMILES for a PubChem CID. Returns str or None."""
    try:
        url = f"{PUBCHEM_BASE}/cid/{cid}/property/IsomericSMILES,CanonicalSMILES/JSON"
        r = original_requests.get(url, timeout=8)
        if r.status_code == 200:
            props = r.json().get("PropertyTable", {}).get("Properties", [])
            if props:
                p = props[0]
                return p.get("IsomericSMILES") or p.get("CanonicalSMILES") or p.get("SMILES")
    except Exception:
        pass
    return None

def _pubchem_cid_to_name(cid: int):
    """Fetch the preferred IUPAC/common name for a PubChem CID. Returns str or None."""
    try:
        url = f"{PUBCHEM_BASE}/cid/{cid}/property/IUPACName,Title/JSON"
        r = original_requests.get(url, timeout=8)
        if r.status_code == 200:
            props = r.json().get("PropertyTable", {}).get("Properties", [])
            if props:
                return props[0].get("Title") or props[0].get("IUPACName")
    except Exception:
        pass
    return None

COMMON_DRUGS = {
    "metformin": {"name": "Metformin", "smiles": "CN(C)C(=N)N=C(N)N", "cid": 4091},
    "aspirin": {"name": "Aspirin", "smiles": "CC(=O)OC1=CC=CC=C1C(=O)O", "cid": 2244},
    "warfarin": {"name": "Warfarin", "smiles": "CC(=O)CC(C1=CC=CC=C1)C2=C(C3=CC=CC=C3OC2=O)O", "cid": 54678486},
    "lisinopril": {"name": "Lisinopril", "smiles": "C1CC(N(C1)C(=O)C(CCCCN)NC(CCC2=CC=CC=C2)C(=O)O)C(=O)O", "cid": 5362119},
    "simvastatin": {"name": "Simvastatin", "smiles": "CCC(C)(C)C(=O)OC1CC(C=C2C1C(C(C=C2)C)CCC3CC(CC(=O)O3)O)C", "cid": 54454},
    "amiodarone": {"name": "Amiodarone", "smiles": "CCCCC1=C(C2=C(O1)C=CC(=C2)C(=O)C3=CC(=C(C(=C3)I)OCCN(CC)CC)I)CCCC", "cid": 2157},
    "clozapine": {"name": "Clozapine", "smiles": "CN1CCN(CC1)C2=C3C=CC=CC3=NC4=C(N2)C=C(C=C4)Cl", "cid": 2818},
    "atorvastatin": {"name": "Atorvastatin", "smiles": "CC(C)C1=C(C(=C(N1CCC(CC(CC(=O)O)O)O)C2=CC=C(C=C2)F)C3=CC=CC=C3)C(=O)NC4=CC=CC=C4", "cid": 60823},
    "omeprazole": {"name": "Omeprazole", "smiles": "CC1=CN=C(C(=C1OC)C)CS(=O)C2=NC3=C(N2)C=C(C=C3)OC", "cid": 4594},
    "ciprofloxacin": {"name": "Ciprofloxacin", "smiles": "C1CC1N2C=C(C(=O)C3=CC(=C(C=C32)N4CCNCC4)F)C(=O)O", "cid": 2764},
    "ibuprofen": {"name": "Ibuprofen", "smiles": "CC(C)CC1=CC=C(C=C1)C(C)C(=O)O", "cid": 3672},
    "acetaminophen": {"name": "Acetaminophen", "smiles": "CC(=O)NC1=CC=C(C=C1)O", "cid": 1983},
    "paracetamol": {"name": "Paracetamol", "smiles": "CC(=O)NC1=CC=C(C=C1)O", "cid": 1983},
    "amlodipine": {"name": "Amlodipine", "smiles": "CCOC(=O)C1=C(NC(=C(C1C2=CC=CC=C2Cl)C(=O)OC)C)COCCN", "cid": 2162},
    "metoprolol": {"name": "Metoprolol", "smiles": "CC(C)NCC(COC1=CC=C(C=C1)CCOC)O", "cid": 4171},
}

_RESOLVE_CACHE = {}

def _resolve_drug(input_text: str):
    """Resolve a drug name or SMILES to { name, smiles, cid, input_type }. Returns None if fails."""
    input_text = input_text.strip()
    if not input_text:
        return None

    cache_key = input_text.lower()
    if cache_key in _RESOLVE_CACHE:
        return dict(_RESOLVE_CACHE[cache_key])

    # Check fast-path local registry
    if cache_key in COMMON_DRUGS:
        entry = dict(COMMON_DRUGS[cache_key])
        entry["input_type"] = "name"
        _RESOLVE_CACHE[cache_key] = entry
        return entry

    if _is_smiles(input_text):
        cid = _pubchem_smiles_to_cid(input_text)
        if cid is None:
            return None
        name   = _pubchem_cid_to_name(cid) or input_text
        smiles = input_text
        res = {"name": name, "smiles": smiles, "cid": cid, "input_type": "smiles"}
        _RESOLVE_CACHE[cache_key] = res
        return res
    else:
        cid = _pubchem_name_to_cid(input_text)
        if cid is None:
            return None
        smiles = _pubchem_cid_to_smiles(cid)
        name   = input_text.title()
        res = {"name": name, "smiles": smiles, "cid": cid, "input_type": "name"}
        _RESOLVE_CACHE[cache_key] = res
        return res

def _pubchem_synonyms(name: str, max_syns: int = 6):
    """Returns a list of common synonyms for a drug name from PubChem. Limits to max_syns."""
    try:
        cid = _pubchem_name_to_cid(name)
        if cid is None:
            return [name]
        url = f"{PUBCHEM_BASE}/cid/{cid}/synonyms/JSON"
        r = original_requests.get(url, timeout=8)
        if r.status_code == 200:
            syns = r.json().get("InformationList", {}).get("Information", [{}])[0].get("Synonym", [])
            clean = [s for s in syns if len(s) < 30 and not s.startswith("InChI") and "=" not in s]
            result = [name]
            for s in clean:
                if s.lower() != name.lower() and len(result) < max_syns:
                    result.append(s)
            return result
    except Exception:
        pass
    return [name]
