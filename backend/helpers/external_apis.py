# backend/helpers/external_apis.py
# ─────────────────────────────────────────────────────────────────────────────
# External API helpers (PubChem, openFDA, ClinicalTrials, HBase, GNN prediction)
# ─────────────────────────────────────────────────────────────────────────────

import os
import re as _re
import time
import requests as original_requests
from helpers.http_client import http_requests

PUBCHEM_BASE = "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound"

# ── HBase Circuit Breaker Cache ──────────────────────────────────────────────
_hbase_last_check = 0.0
_hbase_online_cached = True


def _hbase_connect():
    """
    Return a live happybase Connection.

    Connection strategy
    -------------------
    * The Thrift daemon inside the HBase container silently drops idle TCP
      sockets after ~60 s, so the first call after a quiet period raises
      ``TTransportException: TSocket read 0 bytes``.  That is NOT a sign
      the container is down — it just means the socket is stale.
    * We therefore allow one silent retry with a brand-new Connection before
      surfacing the error.  A genuine refusal (container stopped / port closed)
      trips the 30-second circuit breaker and fast-fails immediately.
    * Returns None only when happybase is not installed.
    """
    global _hbase_last_check, _hbase_online_cached
    try:
        import happybase
    except ImportError:
        return None  # happybase not installed

    try:
        from thrift.transport.TTransport import TTransportException
    except ImportError:
        try:
            from thriftpy2.transport.base import TTransportException
        except ImportError:
            TTransportException = Exception

    now = time.time()
    if not _hbase_online_cached and (now - _hbase_last_check < 30):
        raise RuntimeError("HBase is offline (circuit breaker active — wait 30 s)")

    def _open_fresh():
        """Open a brand-new Thrift connection with a short timeout."""
        host = os.getenv("HBASE_HOST", "127.0.0.1")
        port = int(os.getenv("HBASE_PORT", 9090))
        c = happybase.Connection(host, port=port, timeout=3000)
        c.open()
        return c

    # First attempt
    try:
        conn = _open_fresh()
        _hbase_online_cached = True
        _hbase_last_check = now
        return conn
    except TTransportException:
        # Stale socket — retry once with a fresh connection
        pass
    except Exception as e:
        # Genuine failure (port closed, container down, …)
        _hbase_online_cached = False
        _hbase_last_check = now
        raise

    # Second attempt (after stale-socket failure)
    try:
        conn = _open_fresh()
        _hbase_online_cached = True
        _hbase_last_check = now
        return conn
    except Exception as e:
        _hbase_online_cached = False
        _hbase_last_check = now
        raise

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

# ── GNN Pair Execution ───────────────────────────────────────────────────────
def _run_gnn_pair(smiles_a: str, smiles_b: str):
    """
    Run the trained GINE DDI model with temperature scaling on a SMILES pair.
    Returns (is_harmful: bool, interaction_likelihood_score: float).
    """
    from helpers.models import _load_gnn, get_gnn_temperature
    from models_ddi import smiles_to_molecular_graph, smiles_to_fingerprint
    import torch as _torch

    gnn = _load_gnn()
    if gnn is None:
        raise RuntimeError("GNN model not available")

    g1 = smiles_to_molecular_graph(smiles_a)
    g2 = smiles_to_molecular_graph(smiles_b)
    if g1 is None or g2 is None:
        raise ValueError("Invalid SMILES - could not build molecular graph")

    fp1 = smiles_to_fingerprint(smiles_a)
    fp2 = smiles_to_fingerprint(smiles_b)

    temperature = get_gnn_temperature()

    with _torch.no_grad():
        out = gnn(g1, g2, fp1, fp2)
        scaled_logit = out / temperature
        prob = _torch.sigmoid(scaled_logit).item()

    is_harmful = prob >= 0.50
    return is_harmful, round(prob, 4)

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

# ── ClinicalTrials.gov Safety Monitoring ─────────────────────────────────────
def fetch_trial_monitoring(drug):
    """Check ClinicalTrials.gov for active recruiting or active safety studies of a drug."""
    CT_BASE = "https://clinicaltrials.gov/api/v2/studies"
    try:
        params = {
            "query.intr": drug,
            "filter.overallStatus": "RECRUITING,ACTIVE_NOT_RECRUITING",
            "pageSize": 10,
            "fields": "NCTId,BriefTitle,OverallStatus,Phase"
        }
        res = http_requests.get(CT_BASE, params=params, timeout=8).json()
        studies = res.get("studies", [])

        phase34_count = 0
        for s in studies:
            proto = s.get("protocolSection", {})
            phases = proto.get("designModule", {}).get("phases", [])
            if any(p in ["PHASE3", "PHASE4"] for p in phases):
                phase34_count += 1

        return {
            "active_studies": len(studies),
            "phase34_studies": phase34_count,
            "being_monitored": len(studies) > 0
        }
    except Exception:
        return {"active_studies": 0, "phase34_studies": 0, "being_monitored": False}

# ── True Warning Year Resolution ─────────────────────────────────────────────
HISTORICAL_WARNING_YEARS: dict[str, int] = {
    "metformin":           1995,
    "rosiglitazone":       2007,
    "pioglitazone":        2011,
    "canagliflozin":       2015,
    "empagliflozin":       2015,
    "dapagliflozin":       2015,
    "semaglutide":         2021,
    "liraglutide":         2010,
    "exenatide":           2009,
    "sitagliptin":         2015,
    "saxagliptin":         2016,
    "alogliptin":          2016,
    "tirzepatide":         2022,
    "warfarin":            1997,
    "amiodarone":          1985,
    "dronedarone":         2009,
    "digoxin":             2001,
    "flecainide":          1989,
    "propafenone":         1990,
    "sotalol":             1992,
    "dofetilide":          1999,
    "ibutilide":           1996,
    "atorvastatin":        2012,
    "simvastatin":         2010,
    "rosuvastatin":        2012,
    "pravastatin":         2012,
    "lovastatin":          2001,
    "cerivastatin":        1999,
    "lisinopril":          1992,
    "enalapril":           1992,
    "ramipril":            1995,
    "valsartan":           1997,
    "losartan":            1995,
    "spironolactone":      2008,
    "eplerenone":          2003,
    "amlodipine":          1992,
    "clonidine":           2000,
    "hydralazine":         1994,
    "minoxidil":           1979,
    "nitroglycerin":       2000,
    "clopidogrel":         1997,
    "prasugrel":           2009,
    "ticagrelor":          2011,
    "ticlopidine":         1993,
    "cilostazol":          1999,
    "bivalirudin":         2000,
    "fondaparinux":        2001,
    "heparin":             1982,
    "enoxaparin":          1993,
    "rivaroxaban":         2011,
    "apixaban":            2012,
    "dabigatran":          2010,
    "edoxaban":            2015,
    "amoxicillin":         2005,
    "ciprofloxacin":       2008,
    "levofloxacin":        2008,
    "moxifloxacin":        2008,
    "ofloxacin":           2008,
    "norfloxacin":         2008,
    "metronidazole":       1995,
    "clarithromycin":      2005,
    "azithromycin":        2013,
    "vancomycin":          1986,
    "linezolid":           2000,
    "rifampin":            1971,
    "isoniazid":           1971,
    "ethambutol":          1971,
    "pyrazinamide":        1971,
    "dapsone":             1998,
    "clindamycin":         2008,
    "nitrofurantoin":      1990,
    "trimethoprim":        1985,
    "sulfamethoxazole":    1985,
    "ibuprofen":           2005,
    "naproxen":            2005,
    "celecoxib":           2005,
    "diclofenac":          2005,
    "indomethacin":        2005,
    "ketorolac":           1991,
    "meloxicam":           2005,
    "piroxicam":           2005,
    "aspirin":             1999,
    "acetaminophen":       2011,
    "tramadol":            2016,
    "codeine":             2013,
    "hydrocodone":         2014,
    "oxycodone":           2001,
    "morphine":            2001,
    "fentanyl":            2005,
    "methadone":           2006,
    "buprenorphine":       2002,
    "naloxone":            2018,
    "naltrexone":          1984,
    "pregabalin":          2019,
    "gabapentin":          2019,
    "haloperidol":         2008,
    "olanzapine":          2003,
    "quetiapine":          2005,
    "risperidone":         2003,
    "clozapine":          1990,
    "aripiprazole":        2005,
    "ziprasidone":         2001,
    "asenapine":           2009,
    "lurasidone":          2010,
    "paliperidone":        2006,
    "lithium":             1970,
    "valproate":           1997,
    "lamotrigine":         1994,
    "carbamazepine":       1994,
    "phenytoin":           1994,
    "topiramate":          2011,
    "levetiracetam":       2008,
    "fluoxetine":          2004,
    "sertraline":          2004,
    "paroxetine":          2004,
    "citalopram":          2011,
    "escitalopram":        2011,
    "venlafaxine":         2004,
    "duloxetine":          2004,
    "amitriptyline":       2004,
    "clomipramine":        2004,
    "imipramine":          2004,
    "nortriptyline":       2004,
    "bupropion":           2009,
    "varenicline":         2009,
    "alprazolam":          2020,
    "diazepam":            2020,
    "lorazepam":           2020,
    "clonazepam":          2020,
    "zolpidem":            2013,
    "eszopiclone":         2013,
    "zaleplon":            2013,
    "methylphenidate":     2006,
    "amphetamine":         2006,
    "atomoxetine":         2004,
    "donepezil":           2009,
    "memantine":           2003,
    "methotrexate":        1988,
    "azathioprine":        1991,
    "cyclophosphamide":    1988,
    "mycophenolate":       2007,
    "tacrolimus":          2003,
    "cyclosporine":        1994,
    "leflunomide":         1998,
    "infliximab":          2004,
    "adalimumab":          2004,
    "etanercept":          2004,
    "certolizumab":        2009,
    "golimumab":           2009,
    "rituximab":           2004,
    "tofacitinib":         2021,
    "baricitinib":         2021,
    "upadacitinib":        2021,
    "abatacept":           2005,
    "belimumab":           2011,
    "natalizumab":         2006,
    "alemtuzumab":         2013,
    "fingolimod":          2010,
    "dimethyl fumarate":   2020,
    "hydroxyurea":         1995,
    "imatinib":            2001,
    "erlotinib":           2004,
    "gefitinib":           2003,
    "sunitinib":           2006,
    "sorafenib":           2005,
    "vemurafenib":         2011,
    "ipilimumab":          2011,
    "nivolumab":           2014,
    "pembrolizumab":       2014,
    "atezolizumab":        2016,
    "bortezomib":          2003,
    "lenalidomide":        2005,
    "thalidomide":         1998,
    "pomalidomide":        2013,
    "salmeterol":          2003,
    "formoterol":          2003,
    "fluticasone":         2015,
    "budesonide":          2015,
    "montelukast":         2020,
    "theophylline":        1995,
    "ipratropium":         2009,
    "tiotropium":          2009,
    "omeprazole":          2011,
    "esomeprazole":        2011,
    "pantoprazole":        2011,
    "lansoprazole":        2011,
    "rabeprazole":         2011,
    "cisapride":           1993,
    "metoclopramide":      2009,
    "domperidone":         2014,
    "ondansetron":         2011,
    "vedolizumab":         2014,
    "ustekinumab":         2016,
    "levothyroxine":       2010,
    "testosterone":        2015,
    "methyltestosterone":  2015,
    "anastrozole":         2000,
    "tamoxifen":           1994,
    "raloxifene":          2007,
    "estradiol":           2003,
    "conjugated estrogens":2003,
    "medroxyprogesterone": 2004,
    "dexamethasone":       2010,
    "prednisone":          2010,
    "hydrocortisone":      2010,
    "fludrocortisone":     2010,
    "furosemide":          1982,
    "torsemide":           1993,
    "hydrochlorothiazide": 1980,
    "sildenafil":          2007,
    "tadalafil":           2007,
    "vardenafil":          2007,
    "finasteride":         2011,
    "dutasteride":         2011,
    "tamsulosin":          1997,
    "isotretinoin":        1988,
    "doxycycline":         1975,
    "minocycline":         2003,
    "pimecrolimus":        2006,
}

def _get_true_warning_year(drug_name: str, api_year: int | None) -> int | None:
    """Return the historically-accurate year a drug's boxed warning was FIRST issued."""
    key = drug_name.strip().lower()
    if key in HISTORICAL_WARNING_YEARS:
        return HISTORICAL_WARNING_YEARS[key]
    first_word = key.split()[0].rstrip("-")
    if first_word in HISTORICAL_WARNING_YEARS:
        return HISTORICAL_WARNING_YEARS[first_word]
    return api_year
