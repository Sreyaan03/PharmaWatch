# backend/routes/interactions.py
# ─────────────────────────────────────────────────────────────────────────────
# Interactions routes: GNN prediction, D3 network visualization, polypharmacy
# ─────────────────────────────────────────────────────────────────────────────

from itertools import combinations
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Blueprint, request, jsonify

from database import upsert_prediction, get_recent_predictions
from helpers.external_apis import (
    _resolve_drug, _run_gnn_pair, _query_faers_coprescription,
    _query_openfda_label_interaction, _hbase_connect
)
from helpers.http_client import http_requests

interactions_bp = Blueprint("interactions", __name__)

@interactions_bp.route("/api/graph", methods=["GET"])
def get_interaction_graph():
    """Fetches REAL co-prescribed drug data from openFDA for the selected drug."""
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

@interactions_bp.route("/api/graph/detailed", methods=["GET"])
def get_detailed_graph():
    """Enhanced graph: co-prescription + FDA label warnings."""
    target_drug = request.args.get('drug', 'Metformin')
    FDA_BASE = "https://api.fda.gov/drug/event.json"
    LABEL_BASE = "https://api.fda.gov/drug/label.json"

    try:
        url = f'{FDA_BASE}?search=patient.drug.medicinalproduct:"{target_drug}"&count=patient.drug.medicinalproduct.exact&limit=15'
        fda_data = http_requests.get(url).json()

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

@interactions_bp.route("/api/graph/twosides", methods=["GET"])
def get_twosides_graph():
    """Fetches known interactions from HBase (TWOSIDES dataset)."""
    raw = request.args.get("drug", "").strip()
    if not raw:
        return jsonify({"error": "Provide ?drug= parameter"}), 400

    try:
        resolved = _resolve_drug(raw)
        if resolved is None:
            return jsonify({"found": False, "input": raw}), 200
        drug_name = resolved["name"].upper()

        conn = _hbase_connect()
        table = conn.table('interactions')
        
        prefix = f"{drug_name}_"
        rows = table.scan(row_prefix=prefix.encode())
        
        nodes = []
        links = []
        
        nodes.append({"id": drug_name, "group": 1})
        added = {drug_name}
        
        for key, data in rows:
            row_str = key.decode()
            parts = row_str.split("_")
            if len(parts) == 2:
                other = parts[1]
                side_effect = data.get(b'info:side_effect', b'').decode().strip()
                severity = data.get(b'info:severity', b'high').decode().strip()
                
                if severity == "critical" or severity == "high":
                    group = 2
                elif severity == "moderate":
                    group = 3
                else:
                    group = 4
                    
                if other not in added:
                    nodes.append({"id": other, "group": group})
                    added.add(other)
                    
                links.append({
                    "source": drug_name,
                    "target": other,
                    "side_effect": side_effect,
                    "severity": severity,
                    "value": 3 if severity in ("critical", "high") else 1
                })
                
        conn.close()
        return jsonify({"nodes": nodes, "links": links, "found": len(links) > 0})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@interactions_bp.route("/api/graph/predict", methods=["POST"])
def predict_interaction():
    """Runs GNN inference on a drug pair."""
    body = request.get_json(silent=True) or {}

    smiles_a = body.get("smiles_a") or body.get("composition", "")
    smiles_b = body.get("smiles_b") or body.get("target_composition", "")
    drug_a   = body.get("drug_a", "")
    drug_b   = body.get("drug_b", "")

    resolved_a = resolved_b = None

    if drug_a and not smiles_a:
        resolved_a = _resolve_drug(drug_a)
        if resolved_a:
            smiles_a = resolved_a.get("smiles", "")
    if drug_b and not smiles_b:
        resolved_b = _resolve_drug(drug_b)
        if resolved_b:
            smiles_b = resolved_b.get("smiles", "")

    if smiles_a and not resolved_a:
        resolved_a = _resolve_drug(smiles_a)
    if smiles_b and not resolved_b:
        resolved_b = _resolve_drug(smiles_b)

    if not smiles_a or not smiles_b:
        return jsonify({"error": "Provide smiles_a+smiles_b or drug_a+drug_b"}), 400

    name_a = (resolved_a or {}).get("name") or drug_a or smiles_a[:20]
    name_b = (resolved_b or {}).get("name") or drug_b or smiles_b[:20]
    cid_a  = str((resolved_a or {}).get("cid", ""))
    cid_b  = str((resolved_b or {}).get("cid", ""))

    try:
        is_harmful, confidence = _run_gnn_pair(smiles_a, smiles_b)

        upsert_prediction(
            drug_a=name_a, drug_b=name_b,
            is_harmful=is_harmful, confidence=confidence,
            smiles_a=smiles_a, smiles_b=smiles_b,
            cid_a=cid_a, cid_b=cid_b,
            source="gnn"
        )

        return jsonify({
            "drug_a":      name_a,
            "drug_b":      name_b,
            "is_harmful":  is_harmful,
            "confidence":  confidence,
            "prediction":  "Harmful Interaction Detected" if is_harmful else "No Significant Interaction",
            "predicted_interactions": ["High risk — avoid combination"] if is_harmful else ["Safe to combine"],
            "source":      "gnn"
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@interactions_bp.route("/api/interactions/resolve", methods=["GET"])
def resolve_drug_endpoint():
    """Resolve a drug name or SMILES string."""
    raw = (request.args.get("drug") or request.args.get("input") or "").strip()
    if not raw:
        return jsonify({"error": "Provide ?drug= or ?input= parameter"}), 400

    if _resolve_drug(raw) is not None:
        resolved = _resolve_drug(raw)
        return jsonify({"found": True, **resolved})
    else:
        return jsonify({"found": False, "input": raw}), 200

@interactions_bp.route("/api/interactions/polypharmacy", methods=["POST"])
def polypharmacy_analysis():
    """Accepts up to 5 drugs, runs GNN on combinations and fetches details."""
    body  = request.get_json(silent=True) or {}
    drugs = body.get("drugs", [])

    if not drugs or len(drugs) < 2:
        return jsonify({"error": "Provide at least 2 drugs"}), 400
    if len(drugs) > 5:
        return jsonify({"error": "Maximum 5 drugs allowed"}), 400

    def _resolve_one(idx_drug):
        idx, d = idx_drug
        r = _resolve_drug(d.strip())
        return idx, d.strip(), r

    resolved_drugs = [None] * len(drugs)
    errors_resolve = []

    with ThreadPoolExecutor(max_workers=min(len(drugs), 5)) as ex:
        futures = {ex.submit(_resolve_one, (i, d)): i for i, d in enumerate(drugs)}
        for fut in as_completed(futures):
            idx, raw, result = fut.result()
            if result is None:
                errors_resolve.append(raw)
            else:
                if result.get("input_type") == "name":
                    result["name"] = raw.title()
                resolved_drugs[idx] = result

    if errors_resolve:
        return jsonify({"error": f"Could not resolve: {', '.join(errors_resolve)}"}), 404

    pairs  = []
    errors = []

    for (i, ra), (j, rb) in combinations(enumerate(resolved_drugs), 2):
        if not ra.get("smiles") or not rb.get("smiles"):
            errors.append(f"No SMILES for {ra['name']} or {rb['name']}")
            continue
        try:
            is_harmful, confidence = _run_gnn_pair(ra["smiles"], rb["smiles"])

            upsert_prediction(
                drug_a=ra["name"], drug_b=rb["name"],
                is_harmful=is_harmful, confidence=confidence,
                smiles_a=ra["smiles"], smiles_b=rb["smiles"],
                cid_a=str(ra.get("cid", "")), cid_b=str(rb.get("cid", "")),
                source="gnn"
            )

            side_effects = []
            if is_harmful:
                try:
                    conn = _hbase_connect()
                    table = conn.table('interactions')
                    for key_candidate in [
                        f"{ra['name'].upper()}_{rb['name'].upper()}".encode(),
                        f"{rb['name'].upper()}_{ra['name'].upper()}".encode()
                    ]:
                        row = table.row(key_candidate)
                        if row:
                            se = row.get(b'info:side_effect', b'').decode().strip()
                            if se:
                                side_effects.append(se)
                            break
                    conn.close()
                except Exception:
                    pass

            faers_reports = _query_faers_coprescription(ra["name"], rb["name"])
            label_check = _query_openfda_label_interaction(ra["name"], rb["name"])
            if not label_check["documented"]:
                label_check = _query_openfda_label_interaction(rb["name"], ra["name"])

            if label_check["documented"]:
                data_source = "fda_label"
            elif faers_reports >= 100:
                data_source = "faers_signal"
            elif faers_reports >= 10:
                data_source = "faers_weak"
            else:
                data_source = "gnn_novel"

            pairs.append({
                "drug_a":           ra["name"],
                "drug_b":           rb["name"],
                "is_harmful":       is_harmful,
                "confidence":       confidence,
                "side_effects":     side_effects,
                "faers_reports":    faers_reports,
                "label_documented": label_check["documented"],
                "label_severity":   label_check["severity"],
                "label_snippet":    label_check["snippet"],
                "label_found_in":   label_check["found_in"],
                "data_source":      data_source,
                "index_a":          i,
                "index_b":          j
            })
        except Exception as e:
            errors.append(f"{ra['name']} + {rb['name']}: {e}")

    nodes = [
        {
            "id":    r["name"],
            "cid":   str(r.get("cid", "")),
            "group": 1,
            "harmful_count": sum(
                1 for p in pairs
                if p["is_harmful"] and (p["drug_a"] == r["name"] or p["drug_b"] == r["name"])
            )
        }
        for r in resolved_drugs
    ]

    links = [
        {
            "source":       p["drug_a"],
            "target":       p["drug_b"],
            "is_harmful":   p["is_harmful"],
            "confidence":   p["confidence"],
            "side_effects": p.get("side_effects", []),
            "faers_reports": p.get("faers_reports", 0),
            "data_source":  p.get("data_source", "gnn_novel"),
            "value":        2 if p["is_harmful"] else 1
        }
        for p in pairs
    ]

    return jsonify({
        "drugs":  [r["name"] for r in resolved_drugs],
        "pairs":  pairs,
        "errors": errors,
        "graph":  {"nodes": nodes, "links": links}
    })

@interactions_bp.route("/api/interactions/uncharted", methods=["GET"])
def uncharted_interactions():
    """Priority #2 — Uncharted Interactions panel."""
    drugs_param = request.args.get("drugs", "")
    min_reports = int(request.args.get("min_reports", 50))

    drug_names = [d.strip().title() for d in drugs_param.split(",") if d.strip()]
    if len(drug_names) < 2:
        return jsonify({"error": "Provide at least 2 drugs as comma-separated ?drugs= param"}), 400

    results = []

    for name_a, name_b in combinations(drug_names, 2):
        faers_count = _query_faers_coprescription(name_a, name_b)
        if faers_count < min_reports:
            results.append({
                "drug_a":           name_a,
                "drug_b":           name_b,
                "faers_reports":    faers_count,
                "in_twosides":      False,
                "label_documented": False,
                "label_severity":   "unknown",
                "label_snippet":    "",
                "is_uncharted":     False,
                "signal_strength":  "none"
            })
            continue

        in_twosides = False
        try:
            conn = _hbase_connect()
            table = conn.table("interactions")
            for key in [
                f"{name_a.upper()}_{name_b.upper()}".encode(),
                f"{name_b.upper()}_{name_a.upper()}".encode()
            ]:
                if table.row(key):
                    in_twosides = True
                    break
            conn.close()
        except Exception:
            pass

        label_check = _query_openfda_label_interaction(name_a, name_b)
        if not label_check["documented"]:
            label_check = _query_openfda_label_interaction(name_b, name_a)

        is_uncharted = not in_twosides and not label_check["documented"]

        results.append({
            "drug_a":           name_a,
            "drug_b":           name_b,
            "faers_reports":    faers_count,
            "in_twosides":      in_twosides,
            "label_documented": label_check["documented"],
            "label_severity":   label_check["severity"],
            "label_snippet":    label_check["snippet"],
            "is_uncharted":     is_uncharted,
            "signal_strength":  (
                "strong"   if faers_count >= 500  else
                "moderate" if faers_count >= 100  else
                "weak"
            )
        })

    results.sort(key=lambda x: (-x["is_uncharted"], -x["faers_reports"]))

    return jsonify({
        "drugs":           drug_names,
        "min_reports":     min_reports,
        "pairs":           results,
        "uncharted_count": sum(1 for r in results if r["is_uncharted"]),
        "total_pairs":     len(results)
    })

@interactions_bp.route("/api/interactions/recent", methods=["GET"])
def recent_predictions():
    """Return the last N GNN predictions stored in SQLite."""
    limit = min(int(request.args.get("limit", 20)), 100)
    return jsonify({"predictions": get_recent_predictions(limit)})
