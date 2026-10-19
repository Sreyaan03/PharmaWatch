#!/usr/bin/env python3
"""
ingest/producer.py — PharmaWatch Multi-Source Kafka Producer

Polls 4 real data sources and publishes JSON messages to Kafka topics:

  faers-release     ← a FAERS quarter newer than the loaded database is out (checked daily)
  pubmed-stream     ← PubMed new drug-safety papers (via NCBI E-utilities)
  clinical-trials   ← ClinicalTrials.gov active study updates
  fda-alerts        ← FDA MedWatch Safety Alerts RSS feed

Run:  python -m ingest.producer   (from the backend directory)
Env:  KAFKA_BOOTSTRAP_SERVERS, FDA_API_KEY, NCBI_API_KEY (all from .env)
"""

import sys
import json
import time
import logging
import datetime
import requests
from kafka import KafkaProducer
from kafka.errors import KafkaError

from core import config

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [PRODUCER] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger("producer")

# ── Drug watchlist — topics are produced for these drugs ─────────────────────
WATCHLIST = [
    "metformin", "warfarin", "aspirin", "ozempic", "semaglutide",
    "lisinopril", "atorvastatin", "amoxicillin", "ibuprofen",
    "pantoprazole", "montelukast", "sertraline", "clozapine",
    "clopidogrel", "losartan", "metoprolol", "amlodipine"
]

# ── Kafka Producer setup ──────────────────────────────────────────────────────
def create_producer(retries=10, delay=5):
    """Create Kafka producer with retry logic (Kafka takes ~30s to start)."""
    for attempt in range(1, retries + 1):
        try:
            producer = KafkaProducer(
                bootstrap_servers=config.get("KAFKA_BOOTSTRAP_SERVERS"),
                value_serializer=lambda v: json.dumps(v).encode("utf-8"),
                key_serializer=lambda k: k.encode("utf-8") if k else None,
                acks="all",              # wait for leader + all replicas
                retries=3,
                max_block_ms=10000,
            )
            log.info(f"✅ Connected to Kafka at {config.get('KAFKA_BOOTSTRAP_SERVERS')}")
            return producer
        except KafkaError:
            log.warning(f"Kafka not ready (attempt {attempt}/{retries}), retrying in {delay}s...")
            time.sleep(delay)
    log.error("❌ Could not connect to Kafka after all retries. Exiting.")
    sys.exit(1)


def publish(producer, topic, key, payload):
    """Send one message and log it."""
    try:
        future = producer.send(topic, key=key, value=payload)
        future.get(timeout=10)
        log.info(f"→ [{topic}] {key}: {str(payload)[:80]}...")
    except Exception as e:
        log.error(f"Failed to publish to {topic}: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# SOURCE 1: FAERS quarterly releases
# Topic: faers-release
# Once a day: is a quarter newer than the newest loaded one on the FDA site? The message only
# announces it; the consumer records it and the refresh runs as a job. No statistics are sent.
# ══════════════════════════════════════════════════════════════════════════════
def check_faers_release(latest_released=None, loaded=None):
    """The newest released quarter label if it is not loaded yet, else None."""
    from faers import downloader
    from faers.pipeline import loaded_quarters
    year, qtr = (latest_released or downloader.latest_released)(1)[-1]
    label = downloader.quarter_label(year, qtr)
    have = loaded if loaded is not None else loaded_quarters()
    return None if label in have else label


def poll_faers_release(producer):
    log.info("=== Checking for a new FAERS quarter ===")
    try:
        label = check_faers_release()
    except Exception as e:
        log.error(f"FAERS release check failed: {e}")
        return
    if label is None:
        log.info("FAERS: the newest released quarter is already loaded")
        return
    publish(producer, "faers-release", label, {
        "source": "fda_faers",
        "quarter": label,
        "timestamp": datetime.datetime.utcnow().isoformat(),
    })


# ══════════════════════════════════════════════════════════════════════════════
# SOURCE 2: PubMed / NCBI — new drug safety papers
# Topic: pubmed-stream
# Polls hourly, searches for papers published in the last 7 days
# ══════════════════════════════════════════════════════════════════════════════
def poll_pubmed(producer):
    log.info("=== Polling PubMed NCBI ===")
    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    api_suffix = f"&api_key={config.get('NCBI_API_KEY')}" if config.get("NCBI_API_KEY") else ""
    published = 0

    # Search terms — focus on drug safety, pharmacovigilance, adverse events
    queries = [
        "drug adverse event pharmacovigilance[Title/Abstract]",
        "adverse drug reaction FAERS[Title/Abstract]",
        "drug interaction safety signal[Title/Abstract]",
    ]

    thirty_days_ago = (datetime.datetime.utcnow() - datetime.timedelta(days=30)).strftime("%Y/%m/%d")
    today = datetime.datetime.utcnow().strftime("%Y/%m/%d")

    for query in queries:
        try:
            # Step 1: search for IDs
            search_url = (
                f"{base}/esearch.fcgi?db=pubmed"
                f"&term={requests.utils.quote(query)}"
                f"&mindate={thirty_days_ago}&maxdate={today}"
                f"&datetype=pdat&retmax=25&retmode=json{api_suffix}"
            )
            search_resp = requests.get(search_url, timeout=10)
            if search_resp.status_code != 200:
                continue
            ids = search_resp.json().get("esearchresult", {}).get("idlist", [])
            if not ids:
                continue

            # Step 2: fetch summaries for found IDs
            ids_str = ",".join(ids)
            summary_url = (
                f"{base}/esummary.fcgi?db=pubmed"
                f"&id={ids_str}&retmode=json{api_suffix}"
            )
            summary_resp = requests.get(summary_url, timeout=10)
            if summary_resp.status_code != 200:
                continue
            result = summary_resp.json().get("result", {})

            for pmid in ids:
                article = result.get(pmid, {})
                title   = article.get("title", "")
                journal = article.get("fulljournalname", "")
                pubdate = article.get("pubdate", "")
                if not title:
                    continue

                message = {
                    "source":    "pubmed",
                    "pmid":      pmid,
                    "title":     title,
                    "journal":   journal,
                    "pubdate":   pubdate,
                    "query":     query,
                    "timestamp": datetime.datetime.utcnow().isoformat(),
                }
                publish(producer, "pubmed-stream", pmid, message)
                published += 1

            time.sleep(0.3)   # NCBI polite rate limiting
        except Exception as e:
            log.error(f"PubMed error for query '{query}': {e}")

    log.info(f"PubMed poll done — {published} messages published")


# ══════════════════════════════════════════════════════════════════════════════
# SOURCE 3: ClinicalTrials.gov — active study updates for watched drugs
# Topic: clinical-trials
# Polls every hour
# ══════════════════════════════════════════════════════════════════════════════
def poll_clinical_trials(producer):
    log.info("=== Polling ClinicalTrials.gov ===")
    base = "https://clinicaltrials.gov/api/v2/studies"
    published = 0

    for drug in WATCHLIST:   # query all drugs in watchlist
        try:
            params = {
                "query.intr": drug,
                "filter.overallStatus": "RECRUITING,ACTIVE_NOT_RECRUITING",
                "pageSize": 10,
                "format": "json",
            }
            resp = requests.get(base, params=params, timeout=10)
            if resp.status_code != 200:
                continue
            studies = resp.json().get("studies", [])

            for study in studies:
                proto  = study.get("protocolSection", {})
                id_mod = proto.get("identificationModule", {})
                status = proto.get("statusModule", {})
                design = proto.get("designModule", {})

                nct_id    = id_mod.get("nctId", "")
                title     = id_mod.get("briefTitle", "")
                phase     = design.get("phases", ["N/A"])
                status_v  = status.get("overallStatus", "")

                if not nct_id:
                    continue

                message = {
                    "source":     "clinical_trials",
                    "drug":       drug,
                    "nct_id":     nct_id,
                    "title":      title,
                    "phase":      phase,
                    "status":     status_v,
                    "timestamp":  datetime.datetime.utcnow().isoformat(),
                }
                publish(producer, "clinical-trials", nct_id, message)
                published += 1

            time.sleep(0.5)
        except Exception as e:
            log.error(f"ClinicalTrials error for {drug}: {e}")

    log.info(f"ClinicalTrials poll done — {published} messages published")


# ══════════════════════════════════════════════════════════════════════════════
# SOURCE 4: FDA MedWatch Safety Alerts RSS feed
# Topic: fda-alerts
# Polls every 30 minutes — real FDA safety communications
# ══════════════════════════════════════════════════════════════════════════════
def poll_fda_alerts(producer):
    """Poll FDA drug enforcement (recalls/safety) via openFDA API."""
    log.info("=== Polling FDA Safety Alerts (openFDA enforcement) ===")
    base = "https://api.fda.gov/drug/enforcement.json"
    api_suffix = f"&api_key={config.get('FDA_API_KEY')}" if config.get("FDA_API_KEY") else ""
    published = 0

    # Fetch the 20 most recent drug enforcement actions (recalls, market withdrawals)
    try:
        url = f"{base}?sort=report_date:desc&limit=20{api_suffix}"
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            results = resp.json().get("results", [])
            for item in results:
                recall_num  = item.get("recall_number", "")
                title       = item.get("reason_for_recall", "")[:200]
                product     = item.get("product_description", "")[:200]
                status      = item.get("status", "")
                report_date = item.get("report_date", "")
                recall_init = item.get("recalling_firm", "")
                class_num   = item.get("classification", "")

                if not recall_num:
                    continue

                message = {
                    "source":      "fda_enforcement",
                    "recall_num":  recall_num,
                    "title":       title,
                    "product":     product,
                    "status":      status,
                    "report_date": report_date,
                    "firm":        recall_init,
                    "class":       class_num,
                    "timestamp":   datetime.datetime.utcnow().isoformat(),
                }
                publish(producer, "fda-alerts", recall_num, message)
                published += 1
        else:
            log.warning(f"FDA enforcement API returned {resp.status_code}")
    except Exception as e:
        log.error(f"FDA enforcement API error: {e}")

    log.info(f"FDA alerts poll done — {published} messages published")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN LOOP — each source runs on its own interval
# ══════════════════════════════════════════════════════════════════════════════
def main():
    log.info("🚀 PharmaWatch Kafka Producer starting up...")
    producer = create_producer()

    # Track last-run timestamps for each source
    last_release = 0   # once a day
    last_pubmed  = 0   # every 60 minutes
    last_trials  = 0   # every 60 minutes
    last_rss     = 0   # every 30 minutes

    RELEASE_INTERVAL = 24 * 60 * 60
    PUBMED_INTERVAL  = 60 * 60
    TRIALS_INTERVAL  = 60 * 60
    RSS_INTERVAL     = 20 * 60   # every 20 minutes (was 30)

    log.info("✅ Producer running. Topics: faers-release | pubmed-stream | clinical-trials | fda-alerts")

    while True:
        now = time.time()

        if now - last_release >= RELEASE_INTERVAL:
            poll_faers_release(producer)
            last_release = time.time()

        if now - last_rss >= RSS_INTERVAL:
            poll_fda_alerts(producer)
            last_rss = time.time()

        if now - last_trials >= TRIALS_INTERVAL:
            poll_clinical_trials(producer)
            last_trials = time.time()

        if now - last_pubmed >= PUBMED_INTERVAL:
            poll_pubmed(producer)
            last_pubmed = time.time()

        producer.flush()
        time.sleep(30)   # check intervals every 30 seconds


if __name__ == "__main__":
    main()
