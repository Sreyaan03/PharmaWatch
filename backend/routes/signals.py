# backend/routes/signals.py
# ─────────────────────────────────────────────────────────────────────────────
# Signals routes: NER mining, disproportionality scoring, Reddit scraping, HBase
# ─────────────────────────────────────────────────────────────────────────────

import os
import uuid
import json
import time
import traceback
import subprocess
import numpy as np
from flask import Blueprint, request, jsonify, Response

from database import (get_cached_signals, upsert_signal, insert_drug_event,
                      insert_violation)
from reddit_scraper import scrape_all, process_posts_with_ner
from helpers.http_client import http_requests
from helpers.models import (WATCHLIST, MINING_TASKS, get_easyocr_reader, run_ner)

signals_bp = Blueprint("signals", __name__)

def compute_disproportionality_helper(drug, event, source=None):
    """Calculates PRR, ROR, BCPNN from DuckDB if available, otherwise openFDA data."""
    # 1. Try DuckDB (if source is not explicitly openfda)
    if source != "openfda":
        try:
            from faers.analytics import compute_prr_duckdb
            duck_res = compute_prr_duckdb(drug, event)
            if duck_res is not None and duck_res.get("a", 0) > 0:
                duck_res["source"] = "faers_local"
                return duck_res
            # If explicit duckdb source requested but no data found, return what we got or None
            if source == "duckdb":
                return duck_res
        except Exception as e:
            print(f"[DUCKDB ERROR] Failed to query local database: {e}")
            if source == "duckdb":
                return None

    # 2. Fall back to openFDA
    FDA_BASE = "https://api.fda.gov/drug/event.json"
    try:
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

        if (a + b) == 0 or (c + d) == 0 or c == 0 or (a + c) == 0 or total == 0:
            return None

        prr = (a / (a + b)) / (c / (c + d))

        denominator_ror = b * c
        ror = (a * d) / denominator_ror if denominator_ror != 0 else 0.0

        numerator_ic = a * total
        denominator_ic = (a + b) * (a + c)
        bcpnn_ic = 0.0
        if denominator_ic != 0 and numerator_ic > 0:
            ratio = numerator_ic / denominator_ic
            if ratio > 0:
                bcpnn_ic = np.log2(ratio)

        flags = 0
        if prr > 2.0 and a >= 3:
            flags += 1
        if ror > 2.0 and a >= 3:
            flags += 1
        if bcpnn_ic > 1.5:
            flags += 1

        if flags >= 2 or prr > 4.0 or ror > 4.0:
            severity = "critical"
        elif flags == 1 or prr > 2.0 or ror > 2.0:
            severity = "high"
        else:
            severity = "moderate"

        return {
            "a": a, "b": b, "c": c, "d": d,
            "prr": round(float(prr), 4),
            "ror": round(float(ror), 4),
            "bcpnn_ic": round(float(bcpnn_ic), 4),
            "n_reports": a,
            "severity": severity,
            "source": "openfda"
        }
    except Exception as e:
        print(f"[DISPROPORTIONALITY ERROR] {drug} + {event}: {e}")
        return None

@signals_bp.route("/api/ner", methods=["POST"])
def ner_endpoint():
    """Expects JSON body: { 'text': '...' }. Returns named entities."""
    data = request.get_json(silent=True)
    if not data or "text" not in data:
        return jsonify({"error": "Request body must be JSON with a 'text' field."}), 400

    text = data["text"].strip()
    if not text:
        return jsonify({"error": "'text' field cannot be empty."}), 400

    try:
        entities = run_ner(text)

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

@signals_bp.route("/api/signals", methods=["GET"])
def get_signals():
    """Returns calculated disproportionality signals for the curated watchlist."""
    source = request.args.get("source")
    refresh = request.args.get("refresh", "").lower() == "true"
    signals = []
    cached_list = get_cached_signals(100)
    cached_map = {} if refresh else {(s["drug"].lower(), s["event"].lower()): s for s in cached_list}

    for drug, event in WATCHLIST:
        pair_key = (drug.lower(), event.lower())
        # Only use cache if no explicit source override is requested
        if pair_key in cached_map and not source:
            signals.append(cached_map[pair_key])
        else:
            metrics = compute_disproportionality_helper(drug, event, source=source)
            if metrics:
                metric_source = metrics.get("source", "openfda")
                upsert_signal(drug, event, metrics["a"], metrics["b"], metrics["c"], metrics["d"],
                              metrics["prr"], metrics["ror"], metrics["bcpnn_ic"], metrics["n_reports"],
                              metrics["severity"], source=metric_source)
                signals.append({
                    "drug": drug,
                    "event": event,
                    "a": metrics["a"], "b": metrics["b"], "c": metrics["c"], "d": metrics["d"],
                    "prr": metrics["prr"],
                    "ror": metrics["ror"],
                    "bcpnn_ic": metrics["bcpnn_ic"],
                    "n_reports": metrics["n_reports"],
                    "severity": metrics["severity"],
                    "source": metric_source
                })

    watchlist_keys = {(d.lower(), e.lower()) for d, e in WATCHLIST}
    for s in cached_list:
        pair_key = (s["drug"].lower(), s["event"].lower())
        if pair_key not in watchlist_keys:
            signals.append(s)

    return jsonify({"signals": signals, "total": len(signals)})

@signals_bp.route("/api/signals/ner-mine", methods=["POST"])
def register_ner_task():
    """Accepts text or files (PDF/TXT), registers a task, and returns task_id."""
    task_id = str(uuid.uuid4())
    text = request.form.get("text", "")
    uploaded_file = request.files.get("file")
    
    file_bytes = None
    filename = None
    if uploaded_file:
        file_bytes = uploaded_file.read()
        filename = uploaded_file.filename
        
    MINING_TASKS[task_id] = {
        "text": text,
        "file_bytes": file_bytes,
        "filename": filename,
        "status": "queued"
    }
    return jsonify({"task_id": task_id})

@signals_bp.route("/api/signals/ner-mine/stream/<task_id>", methods=["GET"])
def stream_ner_task(task_id):
    """Streams OCR, BioBERT NER, and FAERS disproportionality progress via SSE."""
    if task_id not in MINING_TASKS:
        return jsonify({"error": "Task not found"}), 404
        
    task = MINING_TASKS[task_id]
    
    def generator():
        try:
            text = task["text"]
            file_bytes = task["file_bytes"]
            filename = task["filename"]
            
            if file_bytes and filename and filename.lower().endswith(".pdf"):
                yield "event: status\ndata: OCR: Rasterizing PDF pages...\n\n"
                import fitz
                doc = fitz.open(stream=file_bytes, filetype="pdf")
                total_pages = len(doc)
                extracted_text = ""
                
                reader = get_easyocr_reader()
                for idx, page in enumerate(doc):
                    page_num = idx + 1
                    yield f"event: status\ndata: OCR: Transcribing page {page_num}/{total_pages}...\n\n"
                    pix = page.get_pixmap(dpi=150)
                    img_bytes = pix.tobytes("png")
                    ocr_results = reader.readtext(img_bytes)
                    page_text = " ".join([res[1] for res in ocr_results])
                    extracted_text += page_text + "\n"
                text = extracted_text
            elif file_bytes:
                text = file_bytes.decode("utf-8", errors="ignore")
                
            text = text.strip()
            if not text:
                yield "event: error\ndata: No readable text found in upload.\n\n"
                return
                
            yield "event: status\ndata: NER: Mining text with BioBERT model...\n\n"
            entities = run_ner(text)
            
            drugs_found = [e for e in entities if e["label"] in ("Medication", "Drug")]
            events_found = [e for e in entities if e["label"] in ("Disease_disorder", "Sign_symptom")]
            
            drugs = {}
            for d in drugs_found:
                drugs[d["token"].lower()] = d
            events = {}
            for ev in events_found:
                events[ev["token"].lower()] = ev
                
            pairs = []
            for d_name, d_ent in drugs.items():
                for ev_name, ev_ent in events.items():
                    pairs.append({
                        "drug": d_ent["token"],
                        "event": ev_ent["token"],
                        "confidence": round(d_ent["score"] * ev_ent["score"], 4)
                    })
                    
            pairs.sort(key=lambda x: x["confidence"], reverse=True)
            pairs = pairs[:5]
            
            if not pairs:
                yield "event: status\ndata: No drug-event associations found in text.\n\n"
                yield f"event: done\ndata: {json.dumps({'signals': [], 'entities': entities})}\n\n"
                return
                
            computed_signals = []
            for idx, p in enumerate(pairs):
                pair_num = idx + 1
                drug = p["drug"]
                event = p["event"]
                yield f"event: status\ndata: FAERS: Scoring pair {pair_num}/{len(pairs)} ({drug} + {event})...\n\n"
                
                metrics = compute_disproportionality_helper(drug, event)
                if metrics:
                    upsert_signal(drug, event, metrics["a"], metrics["b"], metrics["c"], metrics["d"],
                                  metrics["prr"], metrics["ror"], metrics["bcpnn_ic"], metrics["n_reports"],
                                  metrics["severity"], source="ner_clinical")
                    insert_drug_event(drug, event, source="biobert", confidence=p["confidence"], raw_text=text[:500])
                    computed_signals.append({
                        "drug": drug,
                        "event": event,
                        "a": metrics["a"], "b": metrics["b"], "c": metrics["c"], "d": metrics["d"],
                        "prr": metrics["prr"],
                        "ror": metrics["ror"],
                        "bcpnn_ic": metrics["bcpnn_ic"],
                        "n_reports": metrics["n_reports"],
                        "severity": metrics["severity"],
                        "source": "ner_clinical"
                    })
                    
            yield f"event: done\ndata: {json.dumps({'signals': computed_signals, 'entities': entities})}\n\n"
            
        except Exception as e:
            traceback.print_exc()
            yield f"event: error\ndata: {str(e)}\n\n"
        finally:
            MINING_TASKS.pop(task_id, None)
            
    return Response(generator(), mimetype="text/event-stream")

@signals_bp.route("/api/signals/network", methods=["GET"])
def get_signal_network():
    """Generates 1-hop D3 force-directed network data for drug + event."""
    drug = request.args.get('drug', '').strip()
    event = request.args.get('event', '').strip()
    if not drug or not event:
        return jsonify({"error": "Parameters 'drug' and 'event' are required."}), 400

    FDA_BASE = "https://api.fda.gov/drug/event.json"
    nodes = []
    links = []
    added_nodes = set()
    
    nodes.append({"id": drug, "label": drug, "type": "drug", "val": 30})
    added_nodes.add(drug.lower())
    nodes.append({"id": event, "label": event, "type": "event", "val": 25})
    added_nodes.add(event.lower())
    links.append({"source": drug, "target": event, "value": 5, "type": "primary"})
    
    try:
        url_co_drugs = f"{FDA_BASE}?search=patient.drug.medicinalproduct:\"{drug}\"+AND+patient.reaction.reactionmeddrapt:\"{event}\"&count=patient.drug.medicinalproduct.exact&limit=6"
        res_co_drugs = http_requests.get(url_co_drugs).json()
        co_drugs_list = res_co_drugs.get("results", [])
        
        co_drugs = []
        for item in co_drugs_list:
            name = item.get("term", "")
            count = item.get("count", 0)
            if name and name.lower() != drug.lower() and name.lower() not in added_nodes:
                co_drugs.append({"name": name, "count": count})
        co_drugs = co_drugs[:5]
        for cd in co_drugs:
            cd_name = cd["name"]
            nodes.append({"id": cd_name, "label": cd_name, "type": "co_drug", "val": 15})
            added_nodes.add(cd_name.lower())
            links.append({"source": drug, "target": cd_name, "value": cd["count"], "type": "co_drug"})

        url_co_events = f"{FDA_BASE}?search=patient.drug.medicinalproduct:\"{drug}\"&count=patient.reaction.reactionmeddrapt.exact&limit=6"
        res_co_events = http_requests.get(url_co_events).json()
        co_events_list = res_co_events.get("results", [])
        
        co_events = []
        for item in co_events_list:
            name = item.get("term", "")
            count = item.get("count", 0)
            if name and name.lower() != event.lower() and name.lower() not in added_nodes:
                co_events.append({"name": name, "count": count})
        co_events = co_events[:5]
        for ce in co_events:
            ce_name = ce["name"]
            nodes.append({"id": ce_name, "label": ce_name, "type": "co_event", "val": 10})
            added_nodes.add(ce_name.lower())
            links.append({"source": drug, "target": ce_name, "value": ce["count"], "type": "co_event"})

        for cd in co_drugs:
            cd_name = cd["name"]
            url_cross = f"{FDA_BASE}?search=patient.drug.medicinalproduct:\"{cd_name}\"+AND+patient.reaction.reactionmeddrapt:\"{event}\"&limit=1"
            res_cross = http_requests.get(url_cross).json()
            cross_count = res_cross.get("meta", {}).get("results", {}).get("total", 0)
            if cross_count > 0:
                links.append({"source": cd_name, "target": event, "value": cross_count, "type": "cross_link"})
    except Exception as e:
        print(f"[NETWORK ERROR] {drug} + {event}: {e}")
        pass
        
    return jsonify({"nodes": nodes, "links": links})

@signals_bp.route("/api/reddit/scrape", methods=["POST"])
def trigger_scrape():
    count = scrape_all(limit_per_sub=25)
    return jsonify({"status": "ok", "posts_scraped": count})

@signals_bp.route("/api/reddit/process", methods=["POST"])
def trigger_processing():
    process_posts_with_ner(run_ner)
    return jsonify({"status": "ok"})

@signals_bp.route("/api/system/start-bigdata", methods=["POST"])
def start_bigdata():
    """Starts the hbase-server Docker container and checks connections on 9090."""
    import happybase
    try:
        result = subprocess.run(
            ["docker", "start", "hbase-server"],
            capture_output=True, text=True, timeout=15
        )
    except Exception as e:
        return jsonify({"status": "error", "message": f"docker start failed: {e}"}), 500

    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            conn = happybase.Connection('127.0.0.1', port=9090, timeout=2000)
            conn.open()
            conn.tables()
            conn.close()
            return jsonify({"status": "ready", "message": "HBase is online"})
        except Exception:
            time.sleep(2)
    return jsonify({"status": "timeout", "message": "HBase Thrift ready timeout."}), 202

@signals_bp.route("/api/system/bigdata-status", methods=["GET"])
def bigdata_status():
    from helpers.external_apis import _hbase_connect
    try:
        conn = _hbase_connect()
        conn.tables()
        conn.close()
        return jsonify({"status": "online", "message": "HBase Thrift connection OK"})
    except Exception as e:
        return jsonify({"status": "offline", "message": str(e)})

@signals_bp.route("/api/prr", methods=["GET"])
def get_prr():
    """Computes the PRR formula dynamically for a specific drug+event pair."""
    drug  = request.args.get('drug',  'Metformin')
    event = request.args.get('event', 'Nausea')
    source = request.args.get('source')

    metrics = compute_disproportionality_helper(drug, event, source=source)
    if not metrics:
        return jsonify({"error": "Failed to compute disproportionality or no data found"}), 500

    return jsonify({
        "drug": drug,
        "event": event,
        "a": metrics["a"], "b": metrics["b"], "c": metrics["c"], "d": metrics["d"],
        "prr": metrics["prr"],
        "is_signal": metrics["prr"] > 2.0 and metrics["a"] >= 3,
        "source": metrics.get("source", "openfda")
    })
