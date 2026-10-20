# backend/routes/surveillance.py
# HTTP layer for surveillance: parse the request, call the service, return JSON.

from flask import Blueprint, jsonify, request

from core.auth import require_admin
from services import surveillance as svc

surveillance_bp = Blueprint("surveillance", __name__)


@surveillance_bp.route("/api/surveillance/alerts", methods=["GET"])
def alerts():
    """The alert feed. Filter by kind, status, drug or quarter."""
    payload, status = svc.list_alerts(
        kind=request.args.get("kind"), status=request.args.get("status"),
        drug=(request.args.get("drug") or "").strip() or None, quarter=request.args.get("quarter"),
        limit=min(int(request.args.get("limit", 200)), 500))
    return jsonify(payload), status


@surveillance_bp.route("/api/surveillance/alerts/<int:alert_id>/status", methods=["POST"])
def set_alert_status(alert_id):
    """Mark an alert reviewed, dismissed or escalated."""
    body = request.get_json(silent=True) or {}
    payload, status = svc.set_status(alert_id, (body.get("status") or "").strip(),
                                     reviewer=(body.get("reviewer") or "").strip() or None)
    return jsonify(payload), status


@surveillance_bp.route("/api/surveillance/summary", methods=["GET"])
def summary():
    """Alert counts by kind and status, and the last Spark run and label check."""
    from bigdata.run_spark import read_manifest
    from services.signal_history import data_version
    run = read_manifest() or {}
    return jsonify({
        "counts": svc.counts(),
        "last_spark_run": {"run_id": run.get("run_id"), "latest_quarter": (run.get("quarters") or [None])[-1],
                           "quarters": len(run.get("quarters", []))},
        "data_version": data_version(),
        "note": svc.POSSIBLE_SIGNAL,
    }), 200


@surveillance_bp.route("/api/surveillance/watchlist", methods=["GET"])
def watchlist():
    payload, status = svc.list_watchlist()
    return jsonify(payload), status


@surveillance_bp.route("/api/surveillance/watchlist", methods=["POST"])
def watchlist_add():
    body = request.get_json(silent=True) or {}
    drug = (body.get("drug") or "").strip()
    if not drug:
        return jsonify({"error": "drug is required"}), 400
    payload, status = svc.add_to_watchlist(drug)
    return jsonify(payload), status


@surveillance_bp.route("/api/surveillance/watchlist/<rxcui>", methods=["DELETE"])
def watchlist_remove(rxcui):
    payload, status = svc.remove_from_watchlist(rxcui)
    return jsonify(payload), status


@surveillance_bp.route("/api/surveillance/replay/start", methods=["POST"])
def replay_start():
    """Begin an isolated replay over the loaded quarters. Writes only to the replay store."""
    body = request.get_json(silent=True) or {}
    payload, status = svc.start_replay(from_quarter=(body.get("from") or "").strip() or None,
                                       to_quarter=(body.get("to") or "").strip() or None)
    return jsonify(payload), status


@surveillance_bp.route("/api/surveillance/replay/<run_id>/step", methods=["POST"])
def replay_step(run_id):
    """Compute one historical quarter of a replay (the browser timeline drives this per tick)."""
    body = request.get_json(silent=True) or {}
    payload, status = svc.replay_step(run_id, (body.get("quarter") or "").strip())
    return jsonify(payload), status


@surveillance_bp.route("/api/surveillance/replay/<run_id>", methods=["GET"])
def replay_status(run_id):
    """A replay's status, per-quarter counts, and (with ?quarter= or ?upto=) its alerts."""
    payload, status = svc.replay_status(run_id)
    q, upto = request.args.get("quarter"), request.args.get("upto")
    if status == 200 and (q or upto):
        feed, _ = svc.list_replay_alerts(run_id, quarter=q, upto=upto,
                                         limit=min(int(request.args.get("limit", 200)), 500))
        payload["alerts"] = feed.get("alerts", [])
    return jsonify(payload), status


@surveillance_bp.route("/api/admin/surveillance/detect", methods=["POST"])
@require_admin
def detect():
    """Run the new-signal detector for a quarter now (normally the consumer does this on
    signals-updated). Admin only."""
    body = request.get_json(silent=True) or {}
    result = svc.detect_new_signals(quarter=(body.get("quarter") or "").strip() or None)
    return jsonify(result), 200
