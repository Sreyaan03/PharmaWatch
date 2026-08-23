"""
lstm_model.py — Real PyTorch LSTM for PharmaWatch adverse event forecasting.
Trains on openFDA historical daily report counts per drug.

Three use cases for Boxed Warning integration:
  1. Timeline Analysis   — show if a warning bent the curve (actual vs predicted)
  2. Warning Gap         — flag drugs with rising signals but no boxed warning
  3. Early Signal        — forecast 3-6 months ahead to predict emerging risks
"""
import torch
import torch.nn as nn
import numpy as np
import requests as original_requests

import os
from dotenv import load_dotenv

load_dotenv()
# Patch requests.get to automatically include openFDA API key and support cache layer
FDA_API_KEY = os.getenv("FDA_API_KEY")
class PatchedRequests:
    @staticmethod
    def get(url, **kwargs):
        if "api.fda.gov" in url and "api_key" not in url:
            connector = "&" if "?" in url else "?"
            url = f"{url}{connector}api_key={FDA_API_KEY}"
        
        # Check database cache for FDA responses
        if "api.fda.gov" in url:
            try:
                from database import get_cached_fda_response
                cached = get_cached_fda_response(url)
                if cached is not None:
                    class MockResponse:
                        def __init__(self, json_data):
                            self.json_data = json_data
                            self.status_code = 200
                        def json(self):
                            return self.json_data
                    return MockResponse(cached)
            except Exception as e:
                print(f"[CACHE ERROR] {e}")

        resp = original_requests.get(url, **kwargs)

        # Cache successful openFDA responses
        if "api.fda.gov" in url and resp.status_code == 200:
            try:
                from database import cache_fda_response
                cache_fda_response(url, resp.json())
            except Exception as e:
                print(f"[CACHE SAVE ERROR] {e}")

        return resp

requests = PatchedRequests()
import os
import json
from datetime import datetime

MODEL_DIR = os.path.join(os.path.dirname(__file__), "trained_models")
os.makedirs(MODEL_DIR, exist_ok=True)

LABEL_API = "https://api.fda.gov/drug/label.json"
FAERS_API = "https://api.fda.gov/drug/event.json"


# ── LSTM Architecture ────────────────────────────────────────────────────────
class ADEForecaster(nn.Module):
    def __init__(self, input_size=1, hidden_size=64, num_layers=2, output_size=1):
        super().__init__()
        self.hidden_size = hidden_size
        self.num_layers = num_layers
        self.lstm = nn.LSTM(input_size, hidden_size, num_layers,
                            batch_first=True, dropout=0.2)
        self.fc = nn.Linear(hidden_size, output_size)

    def forward(self, x):
        h0 = torch.zeros(self.num_layers, x.size(0), self.hidden_size)
        c0 = torch.zeros(self.num_layers, x.size(0), self.hidden_size)
        out, _ = self.lstm(x, (h0, c0))
        out = self.fc(out[:, -1, :])
        return out


# ── Data Fetching ────────────────────────────────────────────────────────────
def fetch_fda_timeseries(drug, limit=365):
    """
    Fetch daily adverse event counts from openFDA for a drug.
    Returns list of (date_str, count) tuples sorted by date.
    """
    url = (f'{FAERS_API}?search=patient.drug.openfda.generic_name:"{drug}"'
           f'&count=receivedate&limit={limit}')
    res = requests.get(url, timeout=15).json()
    results = res.get("results", [])

    if not results:
        # Fallback: try medicinalproduct field
        url2 = (f'{FAERS_API}?search=patient.drug.medicinalproduct:"{drug}"'
                f'&count=receivedate&limit={limit}')
        res = requests.get(url2, timeout=15).json()
        results = res.get("results", [])

    # Sort by date chronologically
    parsed = []
    for r in results:
        try:
            d = r["time"]  # openFDA returns "time" for receivedate count
            parsed.append((d, r["count"]))
        except KeyError:
            # Some records use different key format
            parsed.append((str(len(parsed)), r["count"]))

    parsed.sort(key=lambda x: x[0])
    return parsed


def get_boxed_warning_date(drug):
    """
    Attempt to get the effective_time of the earliest boxed warning label
    for this drug. Returns date string like '20150101' or None.
    """
    url = (f'{LABEL_API}?search=openfda.generic_name:"{drug}"'
           f'+AND+_exists_:boxed_warning&sort=effective_time:asc&limit=1')
    try:
        res = requests.get(url, timeout=10).json()
        if "results" in res and res["results"]:
            return res["results"][0].get("effective_time", None)
    except Exception:
        pass
    return None


# ── Sequence Creation ────────────────────────────────────────────────────────
def create_sequences(data, seq_length=30):
    """Sliding window sequences for LSTM training."""
    xs, ys = [], []
    for i in range(len(data) - seq_length):
        xs.append(data[i:i + seq_length])
        ys.append(data[i + seq_length])
    return np.array(xs), np.array(ys)


# ── Training ─────────────────────────────────────────────────────────────────
def train_model(drug, epochs=100, seq_length=30, lr=0.001):
    """
    Train a real LSTM on openFDA data for a specific drug.
    Returns (model, error_string). error_string is None on success.
    """
    print(f"[LSTM] Fetching openFDA data for '{drug}'...")
    raw_pairs = fetch_fda_timeseries(drug, limit=365)
    raw_counts = [p[1] for p in raw_pairs]
    raw_dates = [p[0] for p in raw_pairs]

    if len(raw_counts) < seq_length + 10:
        return None, f"Not enough data points ({len(raw_counts)}) from openFDA for '{drug}'"

    # Normalize to [0, 1]
    data = np.array(raw_counts, dtype=np.float32)
    data_min, data_max = data.min(), data.max()
    data_range = data_max - data_min if data_max != data_min else 1.0
    normalized = (data - data_min) / data_range

    X, y = create_sequences(normalized, seq_length)
    X_tensor = torch.FloatTensor(X).unsqueeze(-1)   # [batch, seq, 1]
    y_tensor = torch.FloatTensor(y).unsqueeze(-1)

    # 80/20 chronological split
    split = int(len(X_tensor) * 0.8)
    X_train, X_test = X_tensor[:split], X_tensor[split:]
    y_train, y_test = y_tensor[:split], y_tensor[split:]

    lstm_model = ADEForecaster()
    criterion = nn.MSELoss()
    optimizer = torch.optim.Adam(lstm_model.parameters(), lr=lr)

    print(f"[LSTM] Training on {len(X_train)} sequences for {epochs} epochs...")
    lstm_model.train()
    for epoch in range(epochs):
        output = lstm_model(X_train)
        loss = criterion(output, y_train)
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()
        if (epoch + 1) % 20 == 0:
            print(f"  [LSTM] {drug} epoch {epoch+1}/{epochs}, loss={loss.item():.6f}")

    # Evaluate on test set
    lstm_model.eval()
    with torch.no_grad():
        test_output = lstm_model(X_test)
        test_loss = criterion(test_output, y_test).item()
    print(f"[LSTM] Test MSE loss: {test_loss:.6f}")

    # Save model weights + metadata
    save_path = os.path.join(MODEL_DIR, f"lstm_{drug.lower()}.pt")
    meta_path = os.path.join(MODEL_DIR, f"lstm_{drug.lower()}_meta.json")
    torch.save(lstm_model.state_dict(), save_path)

    warning_date = get_boxed_warning_date(drug)

    with open(meta_path, "w") as f:
        json.dump({
            "drug": drug,
            "min": float(data_min),
            "max": float(data_max),
            "seq_length": seq_length,
            "data_points": len(raw_counts),
            "test_mse": round(test_loss, 6),
            "trained_at": datetime.utcnow().isoformat(),
            "dates": raw_dates,
            "boxed_warning_date": warning_date
        }, f)

    print(f"[LSTM] Model saved → {save_path}")
    return lstm_model, None


# ── Inference ─────────────────────────────────────────────────────────────────
def predict(drug, seq_length=30):
    """
    Load the trained model and return actual vs LSTM-predicted baseline.
    Also marks where the FDA boxed warning was added on the timeline.
    """
    save_path = os.path.join(MODEL_DIR, f"lstm_{drug.lower()}.pt")
    meta_path = os.path.join(MODEL_DIR, f"lstm_{drug.lower()}_meta.json")

    if not os.path.exists(save_path):
        return None, f"Model not trained yet — call POST /api/lstm/train?drug={drug} first"

    with open(meta_path) as f:
        meta = json.load(f)

    raw_pairs = fetch_fda_timeseries(drug, limit=365)
    raw_counts = [p[1] for p in raw_pairs]
    raw_dates  = [p[0] for p in raw_pairs]

    if len(raw_counts) < seq_length:
        return None, "Not enough recent data from openFDA"

    data_min = meta["min"]
    data_max = meta["max"]
    data_range = data_max - data_min if data_max != data_min else 1.0

    data = np.array(raw_counts, dtype=np.float32)
    normalized = (data - data_min) / data_range

    lstm_model = ADEForecaster()
    lstm_model.load_state_dict(torch.load(save_path, weights_only=True))
    lstm_model.eval()

    # Generate LSTM predictions on sliding windows
    baseline = []
    with torch.no_grad():
        for i in range(seq_length, len(normalized)):
            seq = torch.FloatTensor(normalized[i - seq_length:i]).unsqueeze(0).unsqueeze(-1)
            pred = lstm_model(seq).item()
            # Denormalize back to original scale
            baseline.append(round(float(pred * data_range + data_min), 1))

    actual = raw_counts[seq_length:]
    labels = raw_dates[seq_length:]

    # ── Boxed Warning Timeline Marker ────────────────────────────────────────
    # Find which index on the chart corresponds to the warning effective date
    warning_date = meta.get("boxed_warning_date")
    warning_index = None
    if warning_date:
        for idx, d in enumerate(labels):
            if str(d) >= str(warning_date):
                warning_index = idx
                break

    # ── Warning Gap Detection ─────────────────────────────────────────────────
    # Calculate the slope of the last 30 predictions.
    # A strongly positive slope on a drug WITHOUT a boxed warning = emerging risk.
    rising_signal = False
    slope = 0.0
    if len(baseline) >= 30:
        recent = baseline[-30:]
        x = np.arange(len(recent))
        slope = float(np.polyfit(x, recent, 1)[0])
        rising_signal = slope > (data_max * 0.005)  # Rising > 0.5% of max per day

    return {
        "drug": drug,
        "labels": labels,
        "actual": actual,
        "baseline": baseline,
        "trained_on": meta["data_points"],
        "test_mse": meta.get("test_mse"),
        "boxed_warning_date": warning_date,
        "boxed_warning_index": warning_index,
        "rising_signal": rising_signal,
        "trend_slope": round(slope, 4)
    }, None


# ── Warning Gap Scanner ───────────────────────────────────────────────────────
def scan_warning_gap(drug):
    """
    Use the LSTM trend slope to determine if a drug has a rising signal
    but no boxed warning — i.e. it MIGHT need one in the future.
    Returns a risk classification.
    """
    save_path = os.path.join(MODEL_DIR, f"lstm_{drug.lower()}.pt")
    if not os.path.exists(save_path):
        return {"error": f"No trained model for '{drug}'. Train first."}

    result, error = predict(drug)
    if error:
        return {"error": error}

    has_warning = result["boxed_warning_date"] is not None
    rising = result["rising_signal"]
    slope = result["trend_slope"]

    if rising and not has_warning:
        risk = "HIGH"
        message = (f"LSTM detects a rising adverse event trend (slope={slope}) "
                   f"but '{drug}' has NO FDA Boxed Warning. This may be an emerging risk.")
    elif rising and has_warning:
        risk = "MODERATE"
        message = (f"LSTM detects a rising trend despite an existing Boxed Warning. "
                   f"The warning may not be changing clinical practice for '{drug}'.")
    elif not rising and has_warning:
        risk = "LOW"
        message = (f"Adverse event reports are stable/declining. "
                   f"The Boxed Warning appears to be effective for '{drug}'.")
    else:
        risk = "MINIMAL"
        message = f"No significant rising trend detected for '{drug}'. No current concern."

    return {
        "drug": drug,
        "has_boxed_warning": has_warning,
        "boxed_warning_date": result["boxed_warning_date"],
        "rising_signal": rising,
        "trend_slope": slope,
        "risk_level": risk,
        "message": message
    }
