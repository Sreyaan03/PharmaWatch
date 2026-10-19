"""
forecast.py — monthly reporting-share forecast for a drug.

What is forecast is the drug's share of all reports received in a month (percent), not its raw
count, so growth and seasonality that every drug shares in reporting volume cancel out. The
series is split chronologically:

    train       everything before the last 24 months (the LSTM is fitted here only)
    validation  the 12 months before the last 12 (calibrates the forecast interval)
    test        the last 12 months (model selection and reported error)

Every prediction is one step ahead from earlier actual values. The LSTM is shown only when its
test error is below that of the seasonal-naive baseline (the same month a year earlier);
otherwise the baseline is shown and says so. A month is flagged only when its actual share is
above the forecast interval.
"""

import io
import json
import logging
import re
from datetime import datetime, timezone

import numpy as np
import torch
import torch.nn as nn

from core import storage

log = logging.getLogger(__name__)

SEQ_LENGTH = 12          # months of history the LSTM sees
HOLDOUT = 12             # months in each of the validation and test windows
MIN_MONTHS = 36          # baseline needs a year of history plus validation and test windows
MIN_MONTHS_LSTM = 48     # at least 12 training sequences before the validation window
Z_95 = 1.96
MIN_CONSECUTIVE = 3      # months in a row above the interval before it is reported as a run


# ── LSTM architecture ────────────────────────────────────────────────────────
class ADEForecaster(nn.Module):
    def __init__(self, input_size=1, hidden_size=32, num_layers=1, output_size=1):
        super().__init__()
        self.hidden_size = hidden_size
        self.num_layers = num_layers
        self.lstm = nn.LSTM(input_size, hidden_size, num_layers, batch_first=True)
        self.fc = nn.Linear(hidden_size, output_size)

    def forward(self, x):
        h0 = torch.zeros(self.num_layers, x.size(0), self.hidden_size)
        c0 = torch.zeros(self.num_layers, x.size(0), self.hidden_size)
        out, _ = self.lstm(x, (h0, c0))
        return self.fc(out[:, -1, :])


def create_sequences(data, seq_length=SEQ_LENGTH):
    """Sliding windows: seq_length months in, the next month out."""
    xs, ys = [], []
    for i in range(len(data) - seq_length):
        xs.append(data[i:i + seq_length])
        ys.append(data[i + seq_length])
    return np.array(xs), np.array(ys)


# ── evaluation (pure; no torch needed) ───────────────────────────────────────
def _mae(pairs):
    return float(np.mean([abs(a - p) for a, p in pairs])) if pairs else None


def _rmse(pairs):
    return float(np.sqrt(np.mean([(a - p) ** 2 for a, p in pairs]))) if pairs else None


def evaluate(months, share, totals, lstm_predict=None):
    """Compare the baseline and (if given) the LSTM on the test window and flag deviations.

    months       'YYYYMM' strings, ascending
    share        percent of all reports that month
    totals       all reports that month (gives the sampling noise of a share)
    lstm_predict callable(t) -> predicted share at index t from the actual months before it
    """
    n = len(share)
    if n < MIN_MONTHS:
        raise ValueError(f"need at least {MIN_MONTHS} months, have {n}")
    val = range(n - 2 * HOLDOUT, n - HOLDOUT)
    test = range(n - HOLDOUT, n)

    def baseline(t):
        return share[t - 12]

    base_val = _rmse([(share[t], baseline(t)) for t in val])
    base_test = _mae([(share[t], baseline(t)) for t in test])

    lstm_test = lstm_val = None
    if lstm_predict is not None:
        lstm_val = _rmse([(share[t], lstm_predict(t)) for t in val])
        lstm_test = _mae([(share[t], lstm_predict(t)) for t in test])

    use_lstm = lstm_test is not None and lstm_test < base_test
    predict = lstm_predict if use_lstm else baseline
    val_rmse = lstm_val if use_lstm else base_val
    model_error = lstm_test if use_lstm else base_test

    expected, lower, upper, above = {}, {}, {}, {}
    for t in test:
        p = max(0.0, float(predict(t)))
        # sampling noise of a share of this month's reports, in percentage points
        binomial_sd = 100.0 * np.sqrt(max(p / 100.0 * (1 - p / 100.0), 0.0) / max(totals[t], 1))
        half = Z_95 * max(val_rmse, binomial_sd)
        expected[t], lower[t], upper[t] = p, max(0.0, p - half), p + half
        above[t] = share[t] > upper[t]

    run = 0
    for t in reversed(list(test)):
        if not above[t]:
            break
        run += 1

    pad = [None] * (n - HOLDOUT)
    return {
        "labels": [f"{m[:4]}-{m[4:6]}" for m in months],
        "actual": [round(float(v), 6) for v in share],
        "baseline": pad + [round(float(expected[t]), 6) for t in test],
        "lower": pad + [round(float(lower[t]), 6) for t in test],
        "upper": pad + [round(float(upper[t]), 6) for t in test],
        "unit": "% of all reports received that month",
        "model_used": "lstm" if use_lstm else "seasonal_naive",
        "baseline_error": round(base_test, 6),
        "lstm_error": None if lstm_test is None else round(lstm_test, 6),
        "model_error": round(model_error, 6),
        "error_metric": "mean absolute error, percentage points, last 12 months",
        "interval": {"z": Z_95, "validation_rmse": round(val_rmse, 6),
                     "method": "max(validation RMSE, binomial noise of the month's report total)"},
        "holdout_months": HOLDOUT,
        "evaluated_months": [f"{months[t][:4]}-{months[t][4:6]}" for t in test],
        "above_interval": bool(above[n - 1]),
        "consecutive_months_above": run,
        "run_reported": run >= MIN_CONSECUTIVE,
    }


# ── training ─────────────────────────────────────────────────────────────────
def _slug(drug):
    return re.sub(r"[^a-z0-9]+", "_", (drug or "").lower()).strip("_") or "drug"


def _keys(drug):
    """Storage keys (area 'models') of a drug's forecaster weights and metadata."""
    base = f"lstm_share_{_slug(drug)}"
    return base + ".pt", base + "_meta.json"


def _predictor(model, share, scale):
    def f(t):
        window = np.array(share[t - SEQ_LENGTH:t], dtype=np.float32) / scale
        with torch.no_grad():
            out = model(torch.FloatTensor(window).view(1, -1, 1)).item()
        return max(0.0, out * scale)
    return f


def train_model(drug, months, share, totals, epochs=100, lr=0.01, seed=0):
    """Fit the LSTM on the training window only. Returns (info, error_string)."""
    n = len(share)
    if n < MIN_MONTHS_LSTM:
        return None, (f"Not enough months to train: {n} available, at least {MIN_MONTHS_LSTM} "
                      f"needed (12 months of history, 12 sequences, and 24 held out).")
    torch.manual_seed(seed)
    train_end = n - 2 * HOLDOUT
    train = np.array(share[:train_end], dtype=np.float32)
    scale = float(train.max()) or 1.0
    X, y = create_sequences(train / scale, SEQ_LENGTH)
    X_t = torch.FloatTensor(X).unsqueeze(-1)
    y_t = torch.FloatTensor(y).unsqueeze(-1)

    model = ADEForecaster()
    criterion = nn.MSELoss()
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    model.train()
    for _ in range(epochs):
        loss = criterion(model(X_t), y_t)
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()
    model.eval()

    result = evaluate(months, share, totals, _predictor(model, share, scale))
    weights_key, meta_key = _keys(drug)
    buffer = io.BytesIO()
    torch.save(model.state_dict(), buffer)
    storage.put("models", weights_key, buffer.getvalue())
    storage.put("models", meta_key, json.dumps({
            "drug": drug, "scale": scale, "seq_length": SEQ_LENGTH,
            "months": n, "first_month": months[0], "last_month": months[-1],
            "train_end_month": months[train_end - 1], "epochs": epochs,
            "lstm_error": result["lstm_error"], "baseline_error": result["baseline_error"],
            "model_used": result["model_used"],
            "trained_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }).encode("utf-8"))
    log.info(f"[FORECAST] {drug}: LSTM error {result['lstm_error']} vs baseline "
             f"{result['baseline_error']} -> {result['model_used']}")
    return result, None


# ── inference ────────────────────────────────────────────────────────────────
def predict(drug, months, share, totals):
    """Forecast evaluation for the series, using a trained LSTM only if it matches the data.

    A model trained on different data (other months loaded) would have seen the validation or
    test window, so it is reported as stale and the baseline is used.
    """
    weights_key, meta_key = _keys(drug)
    lstm_predict, status = None, "not_trained"
    meta_bytes = storage.get("models", meta_key) if len(share) >= MIN_MONTHS_LSTM else None
    weights = storage.local_path("models", weights_key) if meta_bytes else None
    if len(share) < MIN_MONTHS_LSTM:
        status = "insufficient_data"
    elif meta_bytes and weights:
        meta = json.loads(meta_bytes)
        if (meta.get("months"), meta.get("first_month"), meta.get("last_month")) == \
                (len(share), months[0], months[-1]):
            model = ADEForecaster()
            model.load_state_dict(torch.load(weights, weights_only=True))
            model.eval()
            lstm_predict, status = _predictor(model, share, meta["scale"]), "trained"
        else:
            status = "stale"
    result = evaluate(months, share, totals, lstm_predict)
    result["lstm_status"] = status
    result["trained_on_months"] = len(share) - 2 * HOLDOUT if status == "trained" else None
    return result


def reporting_scan(result):
    """Describe whether the latest reporting share is above its forecast interval."""
    run = result["consecutive_months_above"]
    model = "LSTM" if result["model_used"] == "lstm" else "seasonal-naive baseline"
    if result["run_reported"]:
        message = (f"The drug's share of reports was above the {model} forecast interval for "
                   f"{run} consecutive months, up to {result['labels'][-1]}. This describes a "
                   f"change in reporting share; it does not show a change in risk.")
    elif result["above_interval"]:
        message = (f"The latest month ({result['labels'][-1]}) is above the {model} forecast "
                   f"interval, for {run} month{'s' if run != 1 else ''} so far; "
                   f"fewer than {MIN_CONSECUTIVE} in a row.")
    else:
        message = (f"The latest month ({result['labels'][-1]}) is within the {model} forecast "
                   f"interval.")
    return {
        "above_interval": result["above_interval"],
        "consecutive_months_above": run,
        "run_reported": result["run_reported"],
        "model_used": result["model_used"],
        "message": message,
    }
