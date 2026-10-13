# backend/services/forecasting.py
# Business logic for backend/routes/ml.py (no Flask imports).
#
# The forecast is of a drug's share of all reports received each month, from the loaded FAERS
# data (see ml/forecast.py). Without enough months of data it says so; it never pads a short
# series or substitutes counts from elsewhere.

import logging

from clients.clinicaltrials import fetch_trial_monitoring

log = logging.getLogger(__name__)

try:
    from ml import forecast as ml_forecast
    LSTM_AVAILABLE = True
except ImportError:
    ml_forecast = None
    LSTM_AVAILABLE = False


def _no_data(drug, message, **extra):
    return {"drug": drug, "source": "no_data", "message": message, "labels": [], "actual": [],
            "baseline": [], "lower": [], "upper": [], "model_used": None, "fallback": False,
            **extra}


def _series(drug):
    """(series dict, None) for the drug, or (None, no-data payload) when it cannot be built."""
    from faers import analytics
    from services.signals import resolve_drug

    ctx = resolve_drug(drug)
    if not ctx["ready"]:
        return None, _no_data(drug, "No FAERS data with drug identity is loaded.")
    if not ctx["ok"]:
        return None, _no_data(drug, ctx["note"] or "The drug name could not be resolved.",
                              unresolved=ctx["unresolved"], identity=ctx["identity"])
    series = analytics.get_monthly_share(ctx["rxcuis"])
    if series is None or not series["months"]:
        return None, _no_data(drug, "No monthly reports are available for this drug.",
                              identity=ctx["identity"])
    minimum = ml_forecast.MIN_MONTHS if ml_forecast else 36
    if len(series["months"]) < minimum:
        return None, _no_data(
            drug, f"{len(series['months'])} month(s) of reports are loaded; at least {minimum} are "
                  f"needed for a forecast with a held-out test period.",
            months_available=len(series["months"]), identity=ctx["identity"])
    series["identity"] = ctx["identity"]
    return series, None


def get_lstm_forecast(drug):
    """Reporting-share forecast with the baseline and the LSTM (when trained and better)."""
    if not LSTM_AVAILABLE:
        return {"error": "forecast module not loaded"}, 500
    series, no_data = _series(drug)
    if no_data:
        return no_data, 200
    try:
        result = ml_forecast.predict(drug, series["months"], series["share"], series["total"])
    except Exception as e:
        log.warning(f"[FORECAST] {drug}: {e}")
        return {"error": str(e)}, 500
    result.update(drug=drug, source="faers_local", fallback=False, identity=series["identity"],
                  trained_on=len(series["months"]),
                  note=("The seasonal-naive baseline (the same month a year earlier) is shown; "
                        "the LSTM is shown only when it has lower error on the last 12 months."))
    return result, 200


def train_lstm(drug, epochs_raw):
    """Train the LSTM on the drug's monthly reporting share (training window only)."""
    if not LSTM_AVAILABLE:
        return {"error": "forecast module not loaded"}, 500
    try:
        epochs = max(1, min(int(epochs_raw), 200))   # capped to bound the cost of a request
    except (TypeError, ValueError):
        return {"error": "epochs must be an integer"}, 400
    series, no_data = _series(drug)
    if no_data:
        return {"error": no_data["message"]}, 400
    try:
        result, error = ml_forecast.train_model(drug, series["months"], series["share"],
                                                series["total"], epochs=epochs)
    except Exception as e:
        return {"error": str(e)}, 500
    if error:
        return {"error": error}, 400
    return {"status": "ok", "drug": drug,
            "lstm_error": result["lstm_error"], "baseline_error": result["baseline_error"],
            "model_used": result["model_used"],
            "message": (f"LSTM trained for '{drug}'. Error on the last 12 months: LSTM "
                        f"{result['lstm_error']} vs seasonal-naive {result['baseline_error']} "
                        f"percentage points; "
                        f"{'the LSTM is used' if result['model_used'] == 'lstm' else 'the baseline is used'}.")}, 200


def lstm_gap_scan(drug):
    """Is the drug's latest reporting share above its forecast interval, with registered trials."""
    if not LSTM_AVAILABLE:
        return {"error": "forecast module not loaded"}, 500
    series, no_data = _series(drug)
    if no_data:
        return {"drug": drug, "error": no_data["message"], "source": "no_data"}, 200
    try:
        result = ml_forecast.predict(drug, series["months"], series["share"], series["total"])
        scan = ml_forecast.reporting_scan(result)
        scan.update(drug=drug, source="faers_local",
                    baseline_error=result["baseline_error"], model_error=result["model_error"])

        ct = fetch_trial_monitoring(drug)
        active = ct["active_studies"]
        scan["clinicaltrials"] = {
            "active_studies": active,
            "phase34_studies": ct["phase34_studies"],
            "being_monitored": ct["being_monitored"],
            "note": (f"{active} active registered stud{'y' if active == 1 else 'ies'} list '{drug}' "
                     f"({ct['phase34_studies']} Phase 3/4)." if active
                     else f"No active registered studies list '{drug}'."),
        }
        return scan, 200
    except Exception as e:
        return {"error": str(e)}, 500
