# backend/services/signals.py
# Business logic for backend/routes/signals.py (no Flask imports).

import json
import traceback
import uuid

from database import (get_cached_signals, upsert_signal, insert_drug_event)
from services.identity import IdentityService
from reddit_scraper import scrape_all, process_posts_with_ner
from ml.ocr import get_easyocr_reader
from services.text_mining import MINING_TASKS
from clients.http import http_requests
from core.config import WATCHLIST
from ml.ner import run_ner
import logging
log = logging.getLogger(__name__)

_IDENTITY = None


def _identity():
    """Process-wide identity service (loaded data first, then RxNav with an on-disk cache)."""
    global _IDENTITY
    if _IDENTITY is None:
        from clients.rxnav import RxNavClient
        from faers.identity_build import cache_path
        _IDENTITY = IdentityService(rxnav=RxNavClient(cache_path=cache_path()))
    return _IDENTITY


def _local_metrics(drug, event):
    """Disproportionality from the loaded FAERS data, or None when it is not available.

    With the identity tables present the drug is resolved to RxNorm ingredient(s) and the event
    to a Preferred Term first; a name that cannot be resolved is reported as such, with zero
    counts, rather than being matched loosely. Databases built before the identity tables existed
    use the older name matching.
    """
    from faers import analytics
    from faers.disproportionality import compute_metrics

    if not analytics.identity_ready():
        return analytics.compute_prr_duckdb(drug, event)

    ident = _identity()
    try:
        d = ident.resolve_drug(drug)
    except Exception as exc:                     # RxNav unreachable: unresolved, not guessed
        log.warning(f"[IDENTITY] drug lookup failed for {drug!r}: {exc}")
        d = {"resolved": False, "ingredients": [], "match": None, "source": None, "suggestion": None}
    e = ident.resolve_event(event)

    identity = {
        "drug": {"input": drug, "resolved": d["resolved"], "ingredients": d["ingredients"],
                 "source": d.get("source"), "suggestion": d.get("suggestion")},
        "event": {"input": event, "resolved": e["resolved"], "pt": e["pt"], "match": e["match"],
                  "note": e["note"], "candidates": e["candidates"]},
    }
    if not (d["resolved"] and e["resolved"]):
        res = compute_metrics(0, 0, 0, 0)
        res.update(source="faers_local", identity=identity,
                   unresolved=[k for k in ("drug", "event") if not identity[k]["resolved"]])
        return res

    res = analytics.compute_prr_ingredients([i["rxcui"] for i in d["ingredients"]], e["pt"])
    if res is not None:
        res["identity"] = identity
    return res


def compute_disproportionality_helper(drug, event, source=None):
    """Calculates PRR, ROR, BCPNN from the local FAERS data if available, otherwise openFDA data.

    The openFDA path is a labelled fallback ('source': 'openfda', 'fallback': True): it matches
    free-text product names, so its counts are approximate and are not comparable with the local ones.
    """
    # 1. Try the local FAERS database (if source is not explicitly openfda)
    if source != "openfda":
        try:
            duck_res = _local_metrics(drug, event)
            if duck_res is not None:
                duck_res["source"] = "faers_local"
                return duck_res
            # If explicit duckdb source requested but no data found, return what we got or None
            if source == "duckdb":
                return duck_res
        except Exception as e:
            log.error(f"[DUCKDB ERROR] Failed to query local database: {e}")
            if source == "duckdb":
                return None

    # 2. Fall back to openFDA
    FDA_BASE = "https://api.fda.gov/drug/event.json"
    try:
        def fetch_count(query_str):
            res = http_requests.get(FDA_BASE, params={"search": query_str, "limit": 1}, timeout=10)
            if res.status_code == 404:
                return 0
            res.raise_for_status()
            data = res.json()
            if "meta" in data and "results" in data["meta"]:
                return data["meta"]["results"]["total"]
            return 0

        # drugcharacterization:1 = suspect drug; concomitant drugs are not counted as exposure
        suspect = f'patient.drug.medicinalproduct:"{drug}" AND patient.drug.drugcharacterization:1'
        a = fetch_count(f'{suspect} AND patient.reaction.reactionmeddrapt:"{event}"')
        ab = fetch_count(suspect)
        ac = fetch_count(f'patient.reaction.reactionmeddrapt:"{event}"')
        total = fetch_count('_exists_:patient')

        b = max(ab - a, 0)
        c = max(ac - a, 0)
        d = max(total - a - b - c, 0)

        from faers.disproportionality import compute_metrics
        res = compute_metrics(a, b, c, d)
        res["source"] = "openfda"
        res["fallback"] = True
        res["note"] = "openFDA live query: free-text product match, approximate; not the loaded FAERS data"
        return res
    except Exception as e:
        log.error(f"[DISPROPORTIONALITY ERROR] {drug} + {event}: {e}")
        return None

_SIGNAL_NETWORK_CACHE = {}

def ner_endpoint(data):
    """Expects JSON body: { 'text': '...' }. Returns named entities."""
    if not data or "text" not in data:
        return {"error": "Request body must be JSON with a 'text' field."}, 400

    text = data["text"].strip()
    if not text:
        return {"error": "'text' field cannot be empty."}, 400

    try:
        entities = run_ner(text)

        drugs_found = [e for e in entities if e["label"] in ("Medication", "Drug")]
        events_found = [e for e in entities if e["label"] in ("Disease_disorder", "Sign_symptom")]
        for d in drugs_found:
            for ev in events_found:
                insert_drug_event(d["token"], ev["token"], source="biobert",
                                  confidence=min(d["score"], ev["score"]),
                                  raw_text=text[:500])

        return {"entities": entities}, 200
    except Exception as exc:
        return {"error": str(exc)}, 500

def _data_version():
    from faers.registry import data_version
    return data_version()


def get_signals(source, refresh):
    """Returns calculated disproportionality signals for the curated watchlist."""
    signals = []
    version = _data_version()
    cached_list = get_cached_signals(100)
    # a cached row is only valid for the data version it was computed from
    cached_map = {} if refresh else {
        (s["drug"].lower(), s["event"].lower()): s
        for s in cached_list if s.get("data_version") == version
    }

    for drug, event in WATCHLIST:
        pair_key = (drug.lower(), event.lower())
        # Only use cache if no explicit source override is requested
        if pair_key in cached_map and not source:
            signals.append(cached_map[pair_key])
        else:
            metrics = compute_disproportionality_helper(drug, event, source=source)
            if metrics:
                metric_source = metrics.get("source", "openfda")
                upsert_signal(drug, event, metrics, source=metric_source, data_version=version)
                signal_data = metrics.copy()
                signal_data.update({"drug": drug, "event": event, "source": metric_source, "data_version": version})
                signals.append(signal_data)

    watchlist_keys = {(d.lower(), e.lower()) for d, e in WATCHLIST}
    for s in cached_list:
        pair_key = (s["drug"].lower(), s["event"].lower())
        if pair_key not in watchlist_keys:
            signals.append(s)

    return {"signals": signals, "total": len(signals)}, 200

def get_signal_network(drug, event):
    """Generates 1-hop D3 force-directed network data for drug + event."""
    if not drug or not event:
        return {"error": "Parameters 'drug' and 'event' are required."}, 400

    cache_key = (drug.lower(), event.lower())
    if cache_key in _SIGNAL_NETWORK_CACHE:
        return _SIGNAL_NETWORK_CACHE[cache_key], 200

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
        log.error(f"[NETWORK ERROR] {drug} + {event}: {e}")
        pass

    # Strict referential integrity: every link must connect nodes that actually exist in the node list
    node_ids = {n["id"] for n in nodes}
    clean_links = [l for l in links if l.get("source") in node_ids and l.get("target") in node_ids]

    payload = {"nodes": nodes, "links": clean_links}
    if len(_SIGNAL_NETWORK_CACHE) >= 250:
        _SIGNAL_NETWORK_CACHE.pop(next(iter(_SIGNAL_NETWORK_CACHE)), None)
    _SIGNAL_NETWORK_CACHE[cache_key] = payload
    return payload, 200

def bigdata_status():
    from clients.hbase import _hbase_connect
    try:
        conn = _hbase_connect()
        if conn is None:
            return {"status": "not_deployed", "hbase_ready": False, "message": "HBase not installed — Kafka pipeline active"}, 200
        tables = conn.tables()
        conn.close()
        return {
            "status": "online",
            "hbase_ready": True,
            "tables": [t.decode('utf-8', errors='ignore') if isinstance(t, bytes) else str(t) for t in tables],
            "message": "HBase Thrift connection OK"
        }, 200
    except Exception as e:
        return {"status": "offline", "hbase_ready": False, "message": str(e)}, 200

def get_prr(drug, event, source):
    """Computes the PRR formula dynamically for a specific drug+event pair."""
    metrics = compute_disproportionality_helper(drug, event, source=source)
    if not metrics:
        return {"error": "Failed to compute disproportionality or no data found"}, 500

    body = {
        "drug": drug,
        "event": event,
        "a": metrics["a"], "b": metrics["b"], "c": metrics["c"], "d": metrics["d"],
        "prr": metrics["prr"],
        "is_signal": metrics["prr"] > 2.0 and metrics["a"] >= 3,
        "source": metrics.get("source", "openfda")
    }
    for key in ("identity", "unresolved", "fallback", "note"):
        if key in metrics:
            body[key] = metrics[key]
    return body, 200


def register_ner_task(text, uploaded_file):
    """Accepts text or files (PDF/TXT), registers a task, and returns task_id."""
    task_id = str(uuid.uuid4())
    
    file_bytes = None
    filename = None
    if uploaded_file:
        filename = uploaded_file.filename or ""
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        allowed_extensions = {"pdf", "txt", "png", "jpg", "jpeg"}
        if ext and ext not in allowed_extensions:
            return {"error": f"Unsupported file format '.{ext}'. Allowed formats: {', '.join(sorted(allowed_extensions))}"}, 400
            
        file_bytes = uploaded_file.read()
        if len(file_bytes) > 10 * 1024 * 1024:
            return {"error": "File exceeds maximum upload size (10 MB)"}, 413
        
    MINING_TASKS[task_id] = {
        "text": text,
        "file_bytes": file_bytes,
        "filename": filename,
        "status": "queued"
    }
    return {"task_id": task_id}, 200


def stream_ner_task(task_id):
    """Streams OCR, BioBERT NER, and FAERS disproportionality progress via SSE."""
    if task_id not in MINING_TASKS:
        return None, ({"error": "Task not found"}, 404)
        
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
                    upsert_signal(drug, event, metrics, source="ner_clinical")
                    insert_drug_event(drug, event, source="biobert", confidence=p["confidence"], raw_text=text[:500])
                    signal_data = metrics.copy()
                    signal_data.update({"drug": drug, "event": event, "source": "ner_clinical"})
                    computed_signals.append(signal_data)
                    
            yield f"event: done\ndata: {json.dumps({'signals': computed_signals, 'entities': entities})}\n\n"
            
        except Exception as e:
            traceback.print_exc()
            yield f"event: error\ndata: {str(e)}\n\n"
        finally:
            MINING_TASKS.pop(task_id, None)
            
    return generator(), None


def trigger_scrape():
    count = scrape_all(limit_per_sub=25)
    return {"status": "ok", "posts_scraped": count}, 200


def trigger_processing():
    process_posts_with_ner(run_ner)
    return {"status": "ok"}, 200

