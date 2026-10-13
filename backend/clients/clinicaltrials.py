# backend/clients/clinicaltrials.py
# ClinicalTrials.gov safety-monitoring lookup.

from clients.http import http_requests

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
