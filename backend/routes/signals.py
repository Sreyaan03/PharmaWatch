# backend/routes/signals.py
# HTTP layer only: parse the request, call the service, return JSON.


from core.auth import require_admin
from flask import Blueprint, request, jsonify, Response
from services import signals as svc

signals_bp = Blueprint("signals", __name__)

@signals_bp.route("/api/ner", methods=["POST"])
def ner_endpoint():
    """Expects JSON body: { 'text': '...' }. Returns named entities."""
    data = request.get_json(silent=True)
    payload, status = svc.ner_endpoint(data)
    return jsonify(payload), status


@signals_bp.route("/api/signals", methods=["GET"])
def get_signals():
    """Returns calculated disproportionality signals for the curated watchlist."""
    source = request.args.get("source")
    refresh = request.args.get("refresh", "").lower() == "true"
    payload, status = svc.get_signals(source, refresh)
    return jsonify(payload), status


@signals_bp.route("/api/signals/network", methods=["GET"])
def get_signal_network():
    """Generates 1-hop D3 force-directed network data for drug + event."""
    drug = request.args.get('drug', '').strip()
    event = request.args.get('event', '').strip()
    payload, status = svc.get_signal_network(drug, event)
    return jsonify(payload), status


@signals_bp.route("/api/system/bigdata-status", methods=["GET"])
def bigdata_status():
    payload, status = svc.bigdata_status()
    return jsonify(payload), status


@signals_bp.route("/api/prr", methods=["GET"])
def get_prr():
    """Computes the PRR formula dynamically for a specific drug+event pair."""
    drug  = request.args.get('drug',  'Metformin')
    event = request.args.get('event', 'Nausea')
    source = request.args.get('source')
    payload, status = svc.get_prr(drug, event, source)
    return jsonify(payload), status


@signals_bp.route("/api/signals/ner-mine", methods=["POST"])
def register_ner_task():
    """Accepts text or files (PDF/TXT), registers a task, and returns task_id."""
    text = request.form.get("text", "")
    uploaded_file = request.files.get("file")
    payload, status = svc.register_ner_task(text, uploaded_file)
    return jsonify(payload), status


@signals_bp.route("/api/signals/ner-mine/stream/<task_id>", methods=["GET"])
def stream_ner_task(task_id):
    """Streams OCR, BioBERT NER, and FAERS disproportionality progress via SSE."""
    events, error = svc.stream_ner_task(task_id)
    if error is not None:
        payload, status = error
        return jsonify(payload), status
    return Response(events, mimetype="text/event-stream")


@signals_bp.route("/api/reddit/scrape", methods=["POST"])
@require_admin
def trigger_scrape():
    payload, status = svc.trigger_scrape()
    return jsonify(payload), status


@signals_bp.route("/api/reddit/process", methods=["POST"])
@require_admin
def trigger_processing():
    payload, status = svc.trigger_processing()
    return jsonify(payload), status

