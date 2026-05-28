# backend/app.py
# ─────────────────────────────────────────────────────────────────────────────
# PharmaWatch — BioBERT Named-Entity Recognition API
# Serves POST /api/ner   →  { "entities": [{token, label}, ...] }
# ─────────────────────────────────────────────────────────────────────────────

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from transformers import AutoTokenizer, AutoModelForTokenClassification, pipeline
import torch
import requests as original_requests
import numpy as np

import os
from dotenv import load_dotenv

load_dotenv()
# Patch requests.get to automatically include openFDA API key
FDA_API_KEY = os.getenv("FDA_API_KEY")
class PatchedRequests:
    @staticmethod
    def get(url, **kwargs):
        if "api.fda.gov" in url and "api_key" not in url:
            connector = "&" if "?" in url else "?"
            url = f"{url}{connector}api_key={FDA_API_KEY}"
        return original_requests.get(url, **kwargs)

http_requests = PatchedRequests()
from database import (init_db, insert_drug_event, get_drug_event_counts,
                       get_prr_data, get_db,
                       insert_violation, get_violations, get_all_violations)
init_db()
from reddit_scraper import scrape_all, process_posts_with_ner
try:
    from lstm_model import train_model as lstm_train, predict as lstm_predict, scan_warning_gap
    LSTM_AVAILABLE = True
    print("[LSTM] lstm_model.py loaded OK")
except ImportError as e:
    LSTM_AVAILABLE = False
    print(f"[LSTM] lstm_model.py not found — LSTM endpoints disabled: {e}")


app = Flask(__name__)
CORS(app)  # Allow browser requests from any origin (needed for localhost dev)

# Resolve the absolute path to the src/ folder (one level up from backend/)
import pathlib
SRC_DIR = pathlib.Path(__file__).parent.parent / "src"

@app.route("/")
def serve_index():
    """Serve the PharmaWatch frontend at http://127.0.0.1:5000/"""
    return send_from_directory(str(SRC_DIR), "index.html")

@app.route("/src/<path:filename>")
def serve_src(filename):
    """Serve JS/CSS/assets from the src/ directory."""
    return send_from_directory(str(SRC_DIR), filename)

# ── Load Biomedical NER model once at server startup ─────────────────────────
# This downloads ~260 MB the first time, then caches it in ~/.cache/huggingface
# Subsequent runs load from the cache (fast).
print("Loading Biomedical NER model — first run downloads ~260MB...")
MODEL_NAME = "d4data/biomedical-ner-all"
tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
model = AutoModelForTokenClassification.from_pretrained(MODEL_NAME)
ner_pipeline = pipeline("ner", model=model, tokenizer=tokenizer, aggregation_strategy="simple")
print("Biomedical NER model loaded OK")

# ── Helper: run NER on a single string ───────────────────────────────────────
def run_ner(text: str):
    """
    Run the fine-tuned biomedical NER pipeline on text.
    Returns grouped entities with word, label, score, start, end.
    """
    results = ner_pipeline(text)
    entities = []
    for ent in results:
        entities.append({
            "token": ent["word"],
            "label": ent["entity_group"],
            "score": round(float(ent["score"]), 4),
            "start": ent["start"],
            "end": ent["end"]
        })
    return entities


# ── Route: POST /api/ner ──────────────────────────────────────────────────────
@app.route("/api/ner", methods=["POST"])
def ner_endpoint():
    """
    Expects JSON body:  { "text": "Patient reported nausea after taking Aspirin." }
    Returns:            { "entities": [{token, label}, ...] }
    """
    data = request.get_json(silent=True)

    if not data or "text" not in data:
        return jsonify({"error": "Request body must be JSON with a 'text' field."}), 400

    text = data["text"].strip()
    if not text:
        return jsonify({"error": "'text' field cannot be empty."}), 400

    try:
        entities = run_ner(text)

        # Store drug-event pairs in database
        drugs_found = [e for e in entities if e["label"] in ("Medication", "Drug")]
        events_found = [e for e in entities if e["label"] in ("Disease_disorder", "Sign_symptom")]
        for d in drugs_found:
            for ev in events_found:
                insert_drug_event(d["token"], ev["token"], source="biobert",
                                 confidence=min(d["score"], ev["score"]),
                                 raw_text=text[:500])

        return jsonify({"entities": entities})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── Route: GET /api/health ────────────────────────────────────────────────────
@app.route("/api/health", methods=["GET"])
def health():
    """Simple ping endpoint — useful to check the server is running."""
    return jsonify({"status": "ok", "model": MODEL_NAME})


# ── Route: GET /api/prr ──────────────────────────────────────────────────────
@app.route("/api/prr", methods=["GET"])
def get_prr():
    """
    Fetches REAL adverse event counts from openFDA for a specific drug+event pair,
    then computes the PRR formula dynamically.
    """
    drug  = request.args.get('drug',  'Metformin')
    event = request.args.get('event', 'Nausea')

    FDA_BASE = "https://api.fda.gov/drug/event.json"

    try:
        # a = reports with THIS drug AND THIS event
        url_a = f'{FDA_BASE}?search=patient.drug.medicinalproduct:"{drug}"+AND+patient.reaction.reactionmeddrapt:"{event}"&limit=1'
        res_a = http_requests.get(url_a).json()
        a = res_a.get("meta", {}).get("results", {}).get("total", 0)

        # (a+b) = ALL reports with THIS drug (any event)
        url_ab = f'{FDA_BASE}?search=patient.drug.medicinalproduct:"{drug}"&limit=1'
        res_ab = http_requests.get(url_ab).json()
        ab = res_ab.get("meta", {}).get("results", {}).get("total", 1)

        # (a+c) = ALL reports with THIS event (any drug)  -- we only need c = (a+c) - a
        url_ac = f'{FDA_BASE}?search=patient.reaction.reactionmeddrapt:"{event}"&limit=1'
        res_ac = http_requests.get(url_ac).json()
        ac = res_ac.get("meta", {}).get("results", {}).get("total", 1)

        # Total reports in FAERS database (approximate)
        url_total = f'{FDA_BASE}?search=_exists_:patient&limit=1'
        res_total = http_requests.get(url_total).json()
        total = res_total.get("meta", {}).get("results", {}).get("total", 1)

        b = ab - a
        c = ac - a
        d = total - a - b - c

        if (a + b) == 0 or (c + d) == 0:
            return jsonify({"error": "Division by zero — not enough data", "prr": 0})

        prr = (a / (a + b)) / (c / (c + d))
        return jsonify({
            "drug": drug,
            "event": event,
            "a": a, "b": b, "c": c, "d": d,
            "prr": round(prr, 4),
            "is_signal": prr > 2.0 and a >= 3
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Route: POST /api/lstm/train ──────────────────────────────────────────────
@app.route("/api/lstm/train", methods=["POST"])
def train_lstm():
    """
    Train a real PyTorch LSTM on openFDA historical data for a given drug.
    Takes ~30-60 seconds. Model is saved to backend/trained_models/.
    """
    if not LSTM_AVAILABLE:
        return jsonify({"error": "lstm_model.py not loaded"}), 500
    drug = request.args.get('drug', 'Metformin')
    epochs = int(request.args.get('epochs', 100))
    try:
        _, error = lstm_train(drug, epochs=epochs)
        if error:
            return jsonify({"error": error}), 400
        return jsonify({"status": "ok", "drug": drug,
                        "message": f"LSTM trained and saved for '{drug}'"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Route: GET /api/lstm ──────────────────────────────────────────────────────
@app.route("/api/lstm", methods=["GET"])
def get_lstm_forecast():
    """
    Returns real LSTM predictions vs actual FAERS counts.
    Includes boxed_warning_index so the frontend can draw a vertical
    marker on the chart showing when the FDA warning was added.
    If model not yet trained, falls back to the rolling average.
    """
    drug = request.args.get('drug', 'Metformin')

    if LSTM_AVAILABLE:
        try:
            result, error = lstm_predict(drug)
            if result:
                return jsonify(result)
            # Model not trained yet — fall through to rolling average fallback
            print(f"[LSTM] Predict error for '{drug}': {error} — using fallback")
        except Exception as e:
            print(f"[LSTM] Exception: {e} — using fallback")

    # ── Fallback: rolling average (original behaviour) ────────────────────
    FDA_BASE = "https://api.fda.gov/drug/event.json"
    try:
        url = f'{FDA_BASE}?search=patient.drug.medicinalproduct:"{drug}"&count=receivedate'
        res = http_requests.get(url).json()
        results = res.get("results", [])
        raw_counts = [r["count"] for r in results]
        if len(raw_counts) < 30:
            raw_counts = (raw_counts * 5)[:30]
        else:
            raw_counts = raw_counts[-30:]
        actual = raw_counts
        baseline = []
        window = 5
        for i in range(len(actual)):
            start = max(0, i - window)
            avg = np.mean(actual[start:i+1])
            baseline.append(round(float(avg), 1))
        labels = [f"Day {i+1}" for i in range(len(actual))]
        return jsonify({"drug": drug, "labels": labels,
                        "baseline": baseline, "actual": actual,
                        "fallback": True, "note": "Using rolling average — train LSTM via POST /api/lstm/train"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500



# ── Route: GET /api/graph ────────────────────────────────────────────────────
@app.route("/api/graph", methods=["GET"])
def get_interaction_graph():
    """
    Fetches REAL co-prescribed drug data from openFDA for the selected drug.
    Returns a node/link graph structure for D3.js visualization.
    """
    target_drug = request.args.get('drug', 'Metformin').upper()
    FDA_BASE = "https://api.fda.gov/drug/event.json"

    try:
        url = f'{FDA_BASE}?search=patient.drug.medicinalproduct:"{target_drug}"&count=patient.drug.medicinalproduct.exact&limit=15'
        response = http_requests.get(url)
        fda_data = response.json()

        nodes = [{"id": target_drug, "group": 1}]
        links = []
        co_drugs = []

        if "results" in fda_data:
            for item in fda_data["results"]:
                co_drug = item["term"].upper()
                interaction_count = item["count"]
                if co_drug == target_drug:
                    continue
                co_drugs.append((co_drug, interaction_count))

        if co_drugs:
            # Use relative thresholds: top third = critical, middle = moderate, bottom = low
            counts = [c for _, c in co_drugs]
            max_count = max(counts)
            min_count = min(counts)
            range_val = max_count - min_count if max_count != min_count else 1
            high_threshold = min_count + range_val * 0.66
            mod_threshold = min_count + range_val * 0.33

            for co_drug, interaction_count in co_drugs:
                if interaction_count >= high_threshold:
                    group = 2   # critical
                elif interaction_count >= mod_threshold:
                    group = 3   # moderate
                else:
                    group = 4   # low

                value = max(1, interaction_count / 500)
                nodes.append({"id": co_drug, "group": group})
                links.append({
                    "source": target_drug,
                    "target": co_drug,
                    "value": value
                })

        return jsonify({"nodes": nodes, "links": links})

    except Exception as e:
        return jsonify({"error": str(e)}), 500



# ── Route: GET /api/local-stats ──────────────────────────────────────────────
@app.route("/api/local-stats", methods=["GET"])
def local_stats():
    conn = get_db()
    total = conn.execute("SELECT COUNT(*) FROM drug_events").fetchone()[0]
    sources = conn.execute(
        "SELECT source, COUNT(*) as c FROM drug_events GROUP BY source"
    ).fetchall()
    conn.close()
    return jsonify({
        "total_records": total,
        "by_source": {r[0]: r[1] for r in sources}
    })


# ── Route: POST /api/reddit/scrape ───────────────────────────────────────────
@app.route("/api/reddit/scrape", methods=["POST"])
def trigger_scrape():
    count = scrape_all(limit_per_sub=25)
    return jsonify({"status": "ok", "posts_scraped": count})

@app.route("/api/reddit/process", methods=["POST"])
def trigger_processing():
    process_posts_with_ner(ner_pipeline)
    return jsonify({"status": "ok"})


# ── Route: GET /api/graph/detailed ───────────────────────────────────────────
@app.route("/api/graph/detailed", methods=["GET"])
def get_detailed_graph():
    """Enhanced graph: co-prescription + FDA label warnings."""
    target_drug = request.args.get('drug', 'Metformin')
    FDA_BASE = "https://api.fda.gov/drug/event.json"
    LABEL_BASE = "https://api.fda.gov/drug/label.json"

    try:
        # 1. Get co-prescribed drugs
        url = f'{FDA_BASE}?search=patient.drug.medicinalproduct:"{target_drug}"&count=patient.drug.medicinalproduct.exact&limit=15'
        fda_data = http_requests.get(url).json()

        # 2. Get FDA label warnings for this drug
        label_url = f'{LABEL_BASE}?search=openfda.generic_name:"{target_drug}"&limit=1'
        label_res = http_requests.get(label_url).json()
        warnings_text = ""
        interactions_text = ""
        if "results" in label_res:
            label = label_res["results"][0]
            warnings_text = " ".join(label.get("warnings", [""])).lower()
            interactions_text = " ".join(label.get("drug_interactions", [""])).lower()

        nodes = [{"id": target_drug.upper(), "group": 1}]
        links = []

        if "results" in fda_data:
            for item in fda_data["results"]:
                co_drug = item["term"].upper()
                if co_drug == target_drug.upper():
                    continue
                count = item["count"]

                # Check if FDA officially warns about this combination
                in_warnings = co_drug.lower() in warnings_text
                in_interactions = co_drug.lower() in interactions_text

                if in_warnings or in_interactions:
                    group = 2; risk = "critical"
                else:
                    group = 4; risk = "low"

                nodes.append({"id": co_drug, "group": group, "risk": risk})
                links.append({
                    "source": target_drug.upper(), "target": co_drug,
                    "value": max(1, count / 500), "risk": risk
                })

        return jsonify({"nodes": nodes, "links": links})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Route: GET /api/boxed-warning/<drug_name> ────────────────────────────────
@app.route("/api/boxed-warning/<drug_name>", methods=["GET"])
def get_boxed_warning(drug_name):
    """
    Fetches the boxed warning text from the openFDA Drug Labeling API
    for ANY drug. Works dynamically — no hardcoded list needed.
    """
    LABEL_BASE = "https://api.fda.gov/drug/label.json"
    try:
        url = f'{LABEL_BASE}?search=openfda.generic_name:"{drug_name}"+AND+_exists_:boxed_warning&limit=1'
        res = http_requests.get(url, timeout=10).json()

        if "results" not in res or len(res["results"]) == 0:
            # Try brand name search as fallback
            url2 = f'{LABEL_BASE}?search=openfda.brand_name:"{drug_name}"+AND+_exists_:boxed_warning&limit=1'
            res = http_requests.get(url2, timeout=10).json()

        if "results" not in res or len(res["results"]) == 0:
            return jsonify({
                "has_warning": False,
                "drug": drug_name,
                "warning_text": "",
                "brand_name": "",
                "generic_name": ""
            })

        label = res["results"][0]
        warning_text = " ".join(label.get("boxed_warning", [""]))
        openfda = label.get("openfda", {})

        return jsonify({
            "has_warning": True,
            "drug": drug_name,
            "warning_text": warning_text,
            "brand_name": ", ".join(openfda.get("brand_name", [])),
            "generic_name": ", ".join(openfda.get("generic_name", [])),
            "pharm_class": ", ".join(openfda.get("pharm_class_epc", [])),
            "route": ", ".join(openfda.get("route", []))
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Route: GET /api/boxed-warning-events/<drug_name> ─────────────────────────
@app.route("/api/boxed-warning-events/<drug_name>", methods=["GET"])
def get_boxed_warning_events(drug_name):
    """
    Fetches FAERS adverse event counts for a drug, then cross-references them
    against the drug's boxed warning text to identify which reported events
    match the warned conditions. Works for ANY drug dynamically.
    """
    FDA_BASE = "https://api.fda.gov/drug/event.json"
    LABEL_BASE = "https://api.fda.gov/drug/label.json"

    try:
        # 1. Get total reports for this drug
        total_url = f'{FDA_BASE}?search=patient.drug.openfda.generic_name:"{drug_name}"&limit=1'
        total_res = http_requests.get(total_url, timeout=10).json()
        total_reports = total_res.get("meta", {}).get("results", {}).get("total", 0)

        if total_reports == 0:
            # Try medicinalproduct as fallback
            total_url2 = f'{FDA_BASE}?search=patient.drug.medicinalproduct:"{drug_name}"&limit=1'
            total_res = http_requests.get(total_url2, timeout=10).json()
            total_reports = total_res.get("meta", {}).get("results", {}).get("total", 0)

        # 2. Get top 25 adverse events
        events_url = f'{FDA_BASE}?search=patient.drug.openfda.generic_name:"{drug_name}"&count=patient.reaction.reactionmeddrapt.exact&limit=25'
        events_res = http_requests.get(events_url, timeout=10).json()
        events = events_res.get("results", [])

        if not events:
            events_url2 = f'{FDA_BASE}?search=patient.drug.medicinalproduct:"{drug_name}"&count=patient.reaction.reactionmeddrapt.exact&limit=25'
            events_res = http_requests.get(events_url2, timeout=10).json()
            events = events_res.get("results", [])

        # 3. Get the boxed warning text to cross-reference
        label_url = f'{LABEL_BASE}?search=openfda.generic_name:"{drug_name}"+AND+_exists_:boxed_warning&limit=1'
        label_res = http_requests.get(label_url, timeout=10).json()
        warning_text = ""
        if "results" in label_res and len(label_res["results"]) > 0:
            warning_text = " ".join(label_res["results"][0].get("boxed_warning", [""])).lower()

        # 4. Cross-reference: check each event term against boxed warning text
        # We use keyword matching — if the MedDRA term (or a simplified version)
        # appears in the warning text, it's flagged as a boxed-warning event
        warned_events = []
        non_warned_events = []
        boxed_total = 0

        for ev in events:
            term = ev["term"]
            count = ev["count"]
            term_lower = term.lower().replace("_", " ").replace("^", "'")

            # Build search keywords from the MedDRA term
            keywords = term_lower.split()
            # Check if any significant keyword (3+ chars) appears in the warning
            is_match = False
            if warning_text:
                for kw in keywords:
                    if len(kw) >= 4 and kw in warning_text:
                        is_match = True
                        break

            entry = {
                "term": term,
                "count": count,
                "is_boxed_warning": is_match,
                "percentage": round(count / total_reports * 100, 2) if total_reports > 0 else 0
            }

            if is_match:
                warned_events.append(entry)
                boxed_total += count
            else:
                non_warned_events.append(entry)

        boxed_pct = round(boxed_total / total_reports * 100, 2) if total_reports > 0 else 0

        return jsonify({
            "drug": drug_name,
            "total_reports": total_reports,
            "has_boxed_warning": bool(warning_text),
            "warned_events": warned_events,
            "other_events": non_warned_events,
            "boxed_warning_event_count": boxed_total,
            "boxed_warning_percentage": boxed_pct
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Route: GET /api/trials/<drug_name> ───────────────────────────────────────
@app.route("/api/trials/<drug_name>", methods=["GET"])
def get_trials(drug_name):
    """
    Fetches REAL clinical trial data from ClinicalTrials.gov API v2 for a drug.
    Returns structured study data: title, phase, status, conditions, enrollment.
    Used for PRR corroboration (Option B) and gap-scan enrichment (Option C).
    """
    CT_BASE = "https://clinicaltrials.gov/api/v2/studies"
    try:
        # Primary search: interventions (drug name in intervention field)
        params = {
            "query.intr": drug_name,
            "filter.overallStatus": "RECRUITING,ACTIVE_NOT_RECRUITING,COMPLETED",
            "pageSize": 20,
            "fields": "NCTId,BriefTitle,OverallStatus,Phase,Condition,EnrollmentCount,StartDate,PrimaryCompletionDate,InterventionName"
        }
        res = http_requests.get(CT_BASE, params=params, timeout=10)
        data = res.json()
        studies = data.get("studies", [])

        results = []
        active_safety_count = 0
        recruiting_count = 0

        for study in studies:
            proto = study.get("protocolSection", {})
            id_module = proto.get("identificationModule", {})
            status_module = proto.get("statusModule", {})
            desc_module = proto.get("descriptionModule", {})
            design_module = proto.get("designModule", {})
            conditions_module = proto.get("conditionsModule", {})
            interventions_module = proto.get("armsInterventionsModule", {})

            nct_id = id_module.get("nctId", "")
            title = id_module.get("briefTitle", "Untitled Study")
            status = status_module.get("overallStatus", "UNKNOWN")
            phases = design_module.get("phases", ["N/A"])
            phase_str = ", ".join(phases) if phases else "N/A"
            conditions = conditions_module.get("conditions", [])
            enrollment = design_module.get("enrollmentInfo", {}).get("count", 0)
            start_date = status_module.get("startDateStruct", {}).get("date", "")

            # Determine if this is an active safety monitoring study
            # (phase 3/4 with safety as a likely endpoint, or RECRUITING status)
            is_safety_relevant = (
                any(p in ["PHASE3", "PHASE4"] for p in phases) or
                status in ["RECRUITING", "ACTIVE_NOT_RECRUITING"]
            )
            if is_safety_relevant:
                active_safety_count += 1
            if status == "RECRUITING":
                recruiting_count += 1

            results.append({
                "nct_id": nct_id,
                "title": title,
                "status": status,
                "phase": phase_str,
                "conditions": conditions[:5],  # Top 5 conditions
                "enrollment": enrollment,
                "start_date": start_date,
                "is_safety_relevant": is_safety_relevant,
                "url": f"https://clinicaltrials.gov/study/{nct_id}" if nct_id else ""
            })

        return jsonify({
            "drug": drug_name,
            "total_studies": len(results),
            "recruiting_count": recruiting_count,
            "active_safety_monitoring": active_safety_count > 0,
            "active_safety_count": active_safety_count,
            "studies": results
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Route: GET /api/prr-trials ─────────────────────────────────────────────────
@app.route("/api/prr-trials", methods=["GET"])
def get_prr_with_trials():
    """
    Option B: Cross-reference FAERS PRR signal with ClinicalTrials.gov data.
    If a drug has a high PRR signal AND active clinical trials studying it,
    both independent data sources corroborate the signal — stronger evidence.
    """
    drug = request.args.get('drug', 'Metformin')
    event = request.args.get('event', 'Nausea')
    FDA_BASE = "https://api.fda.gov/drug/event.json"
    CT_BASE = "https://clinicaltrials.gov/api/v2/studies"

    try:
        # 1. Compute PRR (same logic as /api/prr)
        url_a = f'{FDA_BASE}?search=patient.drug.medicinalproduct:"{drug}"+AND+patient.reaction.reactionmeddrapt:"{event}"&limit=1'
        res_a = http_requests.get(url_a).json()
        a = res_a.get("meta", {}).get("results", {}).get("total", 0)

        url_ab = f'{FDA_BASE}?search=patient.drug.medicinalproduct:"{drug}"&limit=1'
        res_ab = http_requests.get(url_ab).json()
        ab = res_ab.get("meta", {}).get("results", {}).get("total", 1)

        url_ac = f'{FDA_BASE}?search=patient.reaction.reactionmeddrapt:"{event}"&limit=1'
        res_ac = http_requests.get(url_ac).json()
        ac = res_ac.get("meta", {}).get("results", {}).get("total", 1)

        url_total = f'{FDA_BASE}?search=_exists_:patient&limit=1'
        res_total = http_requests.get(url_total).json()
        total = res_total.get("meta", {}).get("results", {}).get("total", 1)

        b = ab - a
        c = ac - a
        d = total - a - b - c

        prr = 0.0
        if (a + b) > 0 and (c + d) > 0:
            prr = (a / (a + b)) / (c / (c + d))

        # 2. Query ClinicalTrials.gov for active studies on this drug+event
        ct_params = {
            "query.intr": drug,
            "query.cond": event,
            "filter.overallStatus": "RECRUITING,ACTIVE_NOT_RECRUITING",
            "pageSize": 5,
            "fields": "NCTId,BriefTitle,OverallStatus,Phase,EnrollmentCount"
        }
        ct_res = http_requests.get(CT_BASE, params=ct_params, timeout=10).json()
        ct_studies = ct_res.get("studies", [])

        # Also search with just the drug to check general active monitoring
        ct_drug_params = {
            "query.intr": drug,
            "filter.overallStatus": "RECRUITING,ACTIVE_NOT_RECRUITING",
            "pageSize": 5,
            "fields": "NCTId,BriefTitle,OverallStatus,Phase"
        }
        ct_drug_res = http_requests.get(CT_BASE, params=ct_drug_params, timeout=10).json()
        ct_drug_studies = ct_drug_res.get("studies", [])

        trial_matches = []
        for s in ct_studies:
            proto = s.get("protocolSection", {})
            id_mod = proto.get("identificationModule", {})
            status_mod = proto.get("statusModule", {})
            design_mod = proto.get("designModule", {})
            nct_id = id_mod.get("nctId", "")
            trial_matches.append({
                "nct_id": nct_id,
                "title": id_mod.get("briefTitle", ""),
                "status": status_mod.get("overallStatus", ""),
                "phase": ", ".join(design_mod.get("phases", ["N/A"])),
                "url": f"https://clinicaltrials.gov/study/{nct_id}" if nct_id else ""
            })

        # 3. Corroboration assessment
        has_signal = prr > 2.0 and a >= 3
        has_trial_match = len(trial_matches) > 0
        active_drug_monitoring = len(ct_drug_studies) > 0

        if has_signal and has_trial_match:
            corroboration = "STRONG"
            corroboration_msg = (
                f"FAERS PRR={prr:.2f} (signal confirmed) AND ClinicalTrials found {len(trial_matches)} "
                f"active stud{'y' if len(trial_matches)==1 else 'ies'} investigating '{drug}+{event}'. "
                f"Two independent sources corroborate this signal."
            )
        elif has_signal and active_drug_monitoring:
            corroboration = "MODERATE"
            corroboration_msg = (
                f"FAERS PRR={prr:.2f} (signal confirmed). ClinicalTrials shows {len(ct_drug_studies)} "
                f"active stud{'y' if len(ct_drug_studies)==1 else 'ies'} for '{drug}' (not specifically for '{event}'). "
                f"Signal is partially corroborated."
            )
        elif has_signal:
            corroboration = "FAERS_ONLY"
            corroboration_msg = (
                f"FAERS PRR={prr:.2f} (signal confirmed) but NO active ClinicalTrials found for '{drug}+{event}'. "
                f"Signal is real per FAERS but not yet being studied in clinical trials."
            )
        else:
            corroboration = "NO_SIGNAL"
            corroboration_msg = f"PRR={prr:.2f} — below signal threshold. No pharmacovigilance concern detected."

        return jsonify({
            "drug": drug,
            "event": event,
            "prr": round(prr, 4),
            "a": a, "b": b, "c": c, "d": d,
            "is_signal": has_signal,
            "trial_matches": trial_matches,
            "active_drug_study_count": len(ct_drug_studies),
            "corroboration": corroboration,
            "corroboration_message": corroboration_msg
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Helper: fetch ClinicalTrials safety monitoring status for a drug ───────────
def fetch_trial_monitoring(drug):
    """
    Check ClinicalTrials.gov for active safety monitoring of a drug.
    Returns a dict with monitoring status and count of active studies.
    Used by scan_warning_gap to enrich the risk assessment (Option C).
    """
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

        # Phase 3/4 studies are the most relevant for safety monitoring
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


# Override the gap-scan route to also include ClinicalTrials data (Option C)
@app.route("/api/lstm/gap-scan", methods=["GET"])
def lstm_gap_scan():
    """
    Warning Gap Predictor (enhanced with ClinicalTrials.gov):
    Uses LSTM trend slope to determine if a drug has a rising adverse event
    signal but NO FDA Boxed Warning. Now also checks ClinicalTrials.gov to
    see if the signal is being actively monitored — if not, the risk is higher.
    Returns an enriched risk classification (HIGH / MODERATE / LOW / MINIMAL).
    """
    if not LSTM_AVAILABLE:
        return jsonify({"error": "lstm_model.py not loaded"}), 500
    drug = request.args.get('drug', 'Metformin')
    try:
        # 1. Get LSTM-based gap scan result
        result = scan_warning_gap(drug)
        if "error" in result:
            return jsonify(result)

        # 2. Enrich with ClinicalTrials monitoring data
        ct_data = fetch_trial_monitoring(drug)
        being_monitored = ct_data["being_monitored"]
        active_studies = ct_data["active_studies"]
        phase34_studies = ct_data["phase34_studies"]

        # 3. Upgrade risk level when NOT being monitored in trials
        original_risk = result["risk_level"]
        rising = result["rising_signal"]
        has_warning = result["has_boxed_warning"]

        if rising and not has_warning and not being_monitored:
            # Worst case: rising signal, no warning, AND no clinical trial watching it
            upgraded_risk = "HIGH"
            ct_note = (
                f"⚠ CRITICAL GAP: Rising FAERS signal AND no active ClinicalTrials monitoring "
                f"for '{drug}'. Neither FDA labels nor research community has flagged this risk."
            )
        elif rising and not has_warning and being_monitored:
            # Signal is rising, no warning — but at least trials are watching it
            upgraded_risk = "MODERATE"
            ct_note = (
                f"📋 {active_studies} active ClinicalTrials stud{'y' if active_studies==1 else 'ies'} "
                f"for '{drug}' ({phase34_studies} Phase 3/4). Rising signal is under trial observation, "
                f"but no FDA Boxed Warning yet issued."
            )
        elif not rising and not has_warning and not being_monitored:
            # Stable, no warning, no trials — genuinely low concern
            upgraded_risk = "MINIMAL"
            ct_note = f"No active ClinicalTrials found for '{drug}'. Stable signal with no regulatory concern."
        else:
            upgraded_risk = original_risk
            if being_monitored:
                ct_note = (
                    f"✅ {active_studies} active ClinicalTrials stud{'y' if active_studies==1 else 'ies'} "
                    f"monitoring '{drug}' ({phase34_studies} Phase 3/4). Signal is being tracked."
                )
            else:
                ct_note = f"No active ClinicalTrials studies found for '{drug}'."

        result["risk_level"] = upgraded_risk
        result["clinicaltrials"] = {
            "active_studies": active_studies,
            "phase34_studies": phase34_studies,
            "being_monitored": being_monitored,
            "note": ct_note
        }

        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Entry point ───────────────────────────────────────────────────────────────

# ── Route: GET /api/boxed-warning/violations/all ─────────────────────────────
@app.route("/api/boxed-warning/violations/all", methods=["GET"])
def get_all_violations_route():
    """
    Return all violation records stored in the local SQLite database.
    A violation = post-warning FAERS reports for a warned adverse event.
    """
    try:
        limit = int(request.args.get('limit', 200))
        rows = get_all_violations(limit=limit)
        return jsonify({"count": len(rows), "violations": rows})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Route: GET /api/boxed-warning/violations/<drug_name> ────────────────────
@app.route("/api/boxed-warning/violations/<drug_name>", methods=["GET"])
def get_violations_for_drug(drug_name):
    """
    Detects boxed-warning violations for a drug:
      1. Fetches the warning's effective_time from the FDA label API.
      2. For each adverse event that matches the boxed warning text,
         counts FAERS reports BEFORE and AFTER the warning date.
      3. Post-warning reports = violations (prescriptions continued
         despite the black box flag).
      4. Each violation is stored in SQLite for persistent tracking.
    Returns both the freshly computed violations and the full DB log.
    """
    FDA_BASE   = "https://api.fda.gov/drug/event.json"
    LABEL_BASE = "https://api.fda.gov/drug/label.json"

    try:
        # 1. Get the boxed warning text AND effective_time
        label_url = (f'{LABEL_BASE}?search=openfda.generic_name:"{drug_name}"'
                     f'+AND+_exists_:boxed_warning&limit=1')
        label_res = http_requests.get(label_url, timeout=10).json()

        if "results" not in label_res or not label_res["results"]:
            # fallback: brand name search
            label_url2 = (f'{LABEL_BASE}?search=openfda.brand_name:"{drug_name}"'
                          f'+AND+_exists_:boxed_warning&limit=1')
            label_res = http_requests.get(label_url2, timeout=10).json()

        if "results" not in label_res or not label_res["results"]:
            return jsonify({
                "drug": drug_name,
                "has_boxed_warning": False,
                "violations": [],
                "stored_violations": get_violations(drug_name),
                "message": "No boxed warning found for this drug."
            })

        label        = label_res["results"][0]
        warning_text = " ".join(label.get("boxed_warning", [""]))
        # effective_time format: YYYYMMDD
        effective_time_raw = label.get("effective_time", "")
        warning_date = None
        if effective_time_raw and len(effective_time_raw) == 8:
            warning_date = f"{effective_time_raw[:4]}-{effective_time_raw[4:6]}-{effective_time_raw[6:]}"

        # 2. Get top 25 adverse events for this drug
        events_url = (f'{FDA_BASE}?search=patient.drug.openfda.generic_name:"{drug_name}"'
                      f'&count=patient.reaction.reactionmeddrapt.exact&limit=25')
        events_res = http_requests.get(events_url, timeout=10).json()
        events = events_res.get("results", [])

        if not events:
            events_url2 = (f'{FDA_BASE}?search=patient.drug.medicinalproduct:"{drug_name}"'
                           f'&count=patient.reaction.reactionmeddrapt.exact&limit=25')
            events_res2 = http_requests.get(events_url2, timeout=10).json()
            events = events_res2.get("results", [])

        # 3. Get total FAERS reports
        total_url = (f'{FDA_BASE}?search=patient.drug.openfda.generic_name:"{drug_name}"'
                     f'&limit=1')
        total_res = http_requests.get(total_url, timeout=10).json()
        total_reports = total_res.get("meta", {}).get("results", {}).get("total", 0)
        if total_reports == 0:
            total_url2 = (f'{FDA_BASE}?search=patient.drug.medicinalproduct:"{drug_name}"'
                          f'&limit=1')
            total_res2 = http_requests.get(total_url2, timeout=10).json()
            total_reports = total_res2.get("meta", {}).get("results", {}).get("total", 0)

        warning_lower = warning_text.lower()
        violations_found = []

        for ev in events:
            term       = ev["term"]
            total_count = ev["count"]
            term_lower = term.lower().replace("_", " ")

            # Check if this event appears in the boxed warning
            keywords   = [w for w in term_lower.split() if len(w) >= 4]
            is_warned  = any(kw in warning_lower for kw in keywords)
            if not is_warned:
                continue

            percentage = round(total_count / total_reports * 100, 2) if total_reports > 0 else 0

            # 4. If we have a warning date, split FAERS counts into pre/post.
            # openFDA receivedate filter syntax: [YYYYMMDD+TO+YYYYMMDD]
            post_count = 0
            pre_count  = 0
            if warning_date:
                warn_dt_compact = warning_date.replace("-", "")
                # Post-warning count (FAERS reports received AFTER warning)
                post_url = (f'{FDA_BASE}?search=patient.drug.openfda.generic_name:"{drug_name}"'
                            f'+AND+patient.reaction.reactionmeddrapt:"{term}"'
                            f'+AND+receivedate:[{warn_dt_compact}+TO+99991231]&limit=1')
                post_res = http_requests.get(post_url, timeout=10).json()
                post_count = post_res.get("meta", {}).get("results", {}).get("total", 0)

                # Fallback: try medicinalproduct field
                if post_count == 0:
                    post_url2 = (f'{FDA_BASE}?search=patient.drug.medicinalproduct:"{drug_name}"'
                                 f'+AND+patient.reaction.reactionmeddrapt:"{term}"'
                                 f'+AND+receivedate:[{warn_dt_compact}+TO+99991231]&limit=1')
                    post_res2 = http_requests.get(post_url2, timeout=10).json()
                    post_count = post_res2.get("meta", {}).get("results", {}).get("total", 0)

                pre_count = max(0, total_count - post_count)
            else:
                # No date available: attribute all reports as post-warning
                post_count = total_count
                pre_count  = 0

            # Snip the warning text around the matched keyword
            matched_kw = next((kw for kw in keywords if kw in warning_lower), "")
            snippet = ""
            if matched_kw:
                idx = warning_lower.find(matched_kw)
                start = max(0, idx - 60)
                end   = min(len(warning_text), idx + 120)
                snippet = warning_text[start:end].strip()

            violations_found.append({
                "term":             term,
                "total_count":      total_count,
                "post_count":       post_count,
                "pre_count":        pre_count,
                "percentage":       percentage,
                "warning_date":     warning_date or "unknown",
                "snippet":          snippet[:200] if snippet else "",
            })

            # 5. Persist to SQLite
            insert_violation(
                drug                    = drug_name,
                adverse_event           = term,
                faers_count_post_warning= post_count,
                faers_count_pre_warning = pre_count,
                percentage_of_total     = percentage,
                warning_effective_date  = warning_date,
                warning_text_snippet    = snippet[:200] if snippet else None,
                detection_method        = "post_warning_faers"
            )

        # Sort by post-warning count descending
        violations_found.sort(key=lambda x: x["post_count"], reverse=True)

        return jsonify({
            "drug":                 drug_name,
            "has_boxed_warning":    True,
            "warning_date":         warning_date or "unknown",
            "total_faers_reports":  total_reports,
            "violations_detected":  len(violations_found),
            "violations":           violations_found,
            "stored_violations":    get_violations(drug_name),
            "message":              (
                f"{len(violations_found)} warned adverse events found with post-warning "
                f"FAERS reports for '{drug_name}'. These represent continued prescriptions "
                f"despite the FDA Black Box Warning (effective {warning_date or 'date unknown'})."
            )
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Route: GET /api/boxed-warning/timeline/<drug_name> ──────────────────────
@app.route("/api/boxed-warning/timeline/<drug_name>", methods=["GET"])
def get_warning_timeline(drug_name):
    """
    Before & After analysis:
    - Fetches yearly FAERS report counts for this drug.
    - Gets the boxed warning effective_time from the FDA label.
    - Returns pre-warning and post-warning yearly counts with slopes.
    Slope computed via simple linear regression on the count series.
    """
    FDA_BASE   = "https://api.fda.gov/drug/event.json"
    LABEL_BASE = "https://api.fda.gov/drug/label.json"

    try:
        # 1. Get warning date
        label_url = (f'{LABEL_BASE}?search=openfda.generic_name:"{drug_name}"'
                     f'+AND+_exists_:boxed_warning&limit=1')
        label_res = http_requests.get(label_url, timeout=10).json()
        if "results" not in label_res or not label_res["results"]:
            label_url2 = (f'{LABEL_BASE}?search=openfda.brand_name:"{drug_name}"'
                          f'+AND+_exists_:boxed_warning&limit=1')
            label_res = http_requests.get(label_url2, timeout=10).json()

        warning_year = None
        if "results" in label_res and label_res["results"]:
            et = label_res["results"][0].get("effective_time", "")
            if et and len(et) >= 4:
                warning_year = int(et[:4])

        # 2. Count FAERS reports by year (receivedate aggregated)
        yearly_url = (f'{FDA_BASE}?search=patient.drug.openfda.generic_name:"{drug_name}"'
                      f'&count=receivedate')
        yr_res = http_requests.get(yearly_url, timeout=12).json()
        raw = yr_res.get("results", [])

        if not raw:
            yearly_url2 = (f'{FDA_BASE}?search=patient.drug.medicinalproduct:"{drug_name}"'
                           f'&count=receivedate')
            yr_res2 = http_requests.get(yearly_url2, timeout=12).json()
            raw = yr_res2.get("results", [])

        # Aggregate into yearly buckets
        year_counts = {}
        for entry in raw:
            time_str = str(entry.get("time", ""))
            if len(time_str) >= 4:
                yr = int(time_str[:4])
                if 1990 <= yr <= 2025:   # sanity clamp
                    year_counts[yr] = year_counts.get(yr, 0) + entry.get("count", 0)

        if not year_counts:
            return jsonify({
                "drug": drug_name,
                "error": "No time-series data available from openFDA for this drug."
            })

        years  = sorted(year_counts.keys())
        counts = [year_counts[y] for y in years]

        # Simple linear regression helper
        def slope(xs, ys):
            n = len(xs)
            if n < 2: return 0.0
            x_mean = sum(xs) / n
            y_mean = sum(ys) / n
            num = sum((xs[i] - x_mean) * (ys[i] - y_mean) for i in range(n))
            den = sum((xs[i] - x_mean) ** 2 for i in range(n))
            return round(num / den, 2) if den != 0 else 0.0

        warning_index = None
        pre_slope     = None
        post_slope    = None

        if warning_year and warning_year in years:
            warning_index = years.index(warning_year)
        elif warning_year:
            # Find nearest year after the warning
            for i, y in enumerate(years):
                if y >= warning_year:
                    warning_index = i
                    break

        if warning_index is not None and warning_index > 1:
            pre_years   = years[:warning_index]
            pre_counts  = counts[:warning_index]
            post_years  = years[warning_index:]
            post_counts = counts[warning_index:]
            pre_slope   = slope(list(range(len(pre_years))),  pre_counts)
            post_slope  = slope(list(range(len(post_years))), post_counts)

        return jsonify({
            "drug":           drug_name,
            "labels":         [str(y) for y in years],
            "counts":         counts,
            "warning_year":   warning_year,
            "warning_index":  warning_index,
            "pre_slope":      pre_slope,
            "post_slope":     post_slope,
            "interpretation": (
                "Post-warning slope is lower than pre-warning — warning reduced prescribing."
                if (pre_slope is not None and post_slope is not None and post_slope < pre_slope)
                else (
                    "Post-warning slope remains elevated — warning has not substantially reduced prescribing."
                    if (pre_slope is not None and post_slope is not None)
                    else "Insufficient data for slope comparison."
                )
            )
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Route: GET /api/boxed-warning/bias-analysis/<drug_name> ─────────────────
@app.route("/api/boxed-warning/bias-analysis/<drug_name>", methods=["GET"])
def get_bias_analysis(drug_name):
    """
    Weber Effect + Notoriety Bias analysis:
    - Fetches FAERS report counts per year.
    - Detects the Weber peak: AE reporting spikes in years 1-2 after
      a boxed warning is issued (media-driven hyper-reporting), then declines.
    - Returns quarterly/yearly series + whether the Weber pattern is detected.
    """
    FDA_BASE   = "https://api.fda.gov/drug/event.json"
    LABEL_BASE = "https://api.fda.gov/drug/label.json"

    try:
        # 1. Get warning year
        label_url = (f'{LABEL_BASE}?search=openfda.generic_name:"{drug_name}"'
                     f'+AND+_exists_:boxed_warning&limit=1')
        label_res = http_requests.get(label_url, timeout=10).json()
        if "results" not in label_res or not label_res["results"]:
            label_url2 = (f'{LABEL_BASE}?search=openfda.brand_name:"{drug_name}"'
                          f'+AND+_exists_:boxed_warning&limit=1')
            label_res = http_requests.get(label_url2, timeout=10).json()

        warning_year = None
        if "results" in label_res and label_res["results"]:
            et = label_res["results"][0].get("effective_time", "")
            if et and len(et) >= 4:
                warning_year = int(et[:4])

        # 2. Get FAERS counts by year
        yearly_url = (f'{FDA_BASE}?search=patient.drug.openfda.generic_name:"{drug_name}"'
                      f'&count=receivedate')
        yr_res = http_requests.get(yearly_url, timeout=12).json()
        raw = yr_res.get("results", [])
        if not raw:
            yearly_url2 = (f'{FDA_BASE}?search=patient.drug.medicinalproduct:"{drug_name}"'
                           f'&count=receivedate')
            yr_res2 = http_requests.get(yearly_url2, timeout=12).json()
            raw = yr_res2.get("results", [])

        year_counts = {}
        for entry in raw:
            time_str = str(entry.get("time", ""))
            if len(time_str) >= 4:
                yr = int(time_str[:4])
                if 1990 <= yr <= 2025:
                    year_counts[yr] = year_counts.get(yr, 0) + entry.get("count", 0)

        if not year_counts:
            return jsonify({
                "drug": drug_name, "warning_year": warning_year,
                "weber_peak_detected": False,
                "error": "No time-series FAERS data available."
            })

        years  = sorted(year_counts.keys())
        counts = [year_counts[y] for y in years]

        # 3. Detect Weber pattern: peak in Y+0/Y+1 after warning then decline
        weber_detected = False
        peak_year      = None
        notoriety_note = ""

        if warning_year:
            # Find the max-reporting year within [warning_year, warning_year+2]
            window_years  = [y for y in years if warning_year <= y <= warning_year + 2]
            window_counts = [year_counts[y] for y in window_years]

            # Check years after the window
            after_years  = [y for y in years if y > warning_year + 2]
            after_counts = [year_counts[y] for y in after_years]

            if window_counts and after_counts:
                window_peak = max(window_counts)
                after_avg   = sum(after_counts) / len(after_counts)
                # Weber = peak in window is at least 30% higher than post-window average
                if window_peak > after_avg * 1.3:
                    weber_detected = True
                    peak_year = window_years[window_counts.index(window_peak)]
                    notoriety_note = (
                        f"Weber Effect detected: FAERS reports peaked in {peak_year} "
                        f"({window_peak:,} reports), {round((window_peak/after_avg - 1)*100)}% "
                        f"above post-window average ({int(after_avg):,}/yr). "
                        f"This post-warning spike reflects hyper-reporting driven by media "
                        f"coverage of the boxed warning (Notoriety Bias), not necessarily "
                        f"a true increase in adverse event incidence."
                    )
                else:
                    notoriety_note = (
                        f"No clear Weber peak detected for '{drug_name}'. "
                        f"Reporting rate appears relatively stable post-warning."
                    )
            else:
                notoriety_note = "Insufficient post-warning data to assess Weber Effect."
        else:
            notoriety_note = "Warning date unavailable — cannot assess Weber Effect."

        return jsonify({
            "drug":                drug_name,
            "labels":              [str(y) for y in years],
            "counts":              counts,
            "warning_year":        warning_year,
            "weber_peak_detected": weber_detected,
            "peak_year":           peak_year,
            "notoriety_note":      notoriety_note,
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Entry point ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
