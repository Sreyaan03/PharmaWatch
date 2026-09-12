#!/usr/bin/env python3
"""
kafka_producer.py — PharmaWatch Multi-Source Kafka Producer

Polls 4 real data sources and publishes JSON messages to Kafka topics:

  faers-raw         ← openFDA FAERS adverse event reports
  pubmed-stream     ← PubMed new drug-safety papers (via NCBI E-utilities)
  clinical-trials   ← ClinicalTrials.gov active study updates
  fda-alerts        ← FDA MedWatch Safety Alerts RSS feed

Run:  python scripts/kafka_producer.py
Env:  KAFKA_BOOTSTRAP_SERVERS, FDA_API_KEY, NCBI_API_KEY (all from .env)
"""

import os
import sys
import json
import time
import logging
import datetime
import requests
import feedparser
from kafka import KafkaProducer
from kafka.errors import NoBrokersAvailable
from dotenv import load_dotenv

# ── Load environment ──────────────────────────────────────────────────────────
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "backend", ".env"))

FDA_API_KEY  = os.getenv("FDA_API_KEY", "")
NCBI_API_KEY = os.getenv("NCBI_API_KEY", "")
KAFKA_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")

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
                bootstrap_servers=KAFKA_SERVERS,
                value_serializer=lambda v: json.dumps(v).encode("utf-8"),
                key_serializer=lambda k: k.encode("utf-8") if k else None,
                acks="all",              # wait for leader + all replicas
                retries=3,
                max_block_ms=10000,
            )
            log.info(f"✅ Connected to Kafka at {KAFKA_SERVERS}")
            return producer
        except NoBrokersAvailable:
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
# SOURCE 1: openFDA FAERS — adverse event reports
# Topic: faers-raw
# Polls every 10 minutes, fetches top 20 latest reports per watched drug
# ══════════════════════════════════════════════════════════════════════════════
def poll_faers(producer):
    log.info("=== Polling openFDA FAERS ===")
    base = "https://api.fda.gov/drug/event.json"
    api_suffix = f"&api_key={FDA_API_KEY}" if FDA_API_KEY else ""
    published = 0

    for drug in WATCHLIST:
        try:
            url = (
                f"{base}?search=patient.drug.medicinalproduct:\"{drug}\""
                f"&count=patient.reaction.reactionmeddrapt.exact"
                f"&limit=10{api_suffix}"
            )
            resp = requests.get(url, timeout=10)
            if resp.status_code != 200:
                continue
            data = resp.json()
            results = data.get("results", [])

            for item in results:
                event   = item.get("term", "").lower()
                count   = item.get("count", 0)
                if not event or count < 1:
                    continue

                message = {
                    "source":    "faers",
                    "drug":      drug,
                    "event":     event,
                    "count":     count,
                    "timestamp": datetime.datetime.utcnow().isoformat(),
                }
                publish(producer, "faers-raw", drug, message)
                published += 1

            time.sleep(0.5)   # be polite to openFDA rate limits
        except Exception as e:
            log.error(f"FAERS error for {drug}: {e}")

    log.info(f"FAERS poll done — {published} messages published")


# ══════════════════════════════════════════════════════════════════════════════
# SOURCE 2: PubMed / NCBI — new drug safety papers
# Topic: pubmed-stream
# Polls hourly, searches for papers published in the last 7 days
# ══════════════════════════════════════════════════════════════════════════════
def poll_pubmed(producer):
    log.info("=== Polling PubMed NCBI ===")
    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    api_suffix = f"&api_key={NCBI_API_KEY}" if NCBI_API_KEY else ""
    published = 0

    # Search terms — focus on drug safety, pharmacovigilance, adverse events
    queries = [
        "drug adverse event pharmacovigilance[Title/Abstract]",
        "adverse drug reaction FAERS[Title/Abstract]",
        "drug interaction safety signal[Title/Abstract]",
    ]

    seven_days_ago = (datetime.datetime.utcnow() - datetime.timedelta(days=7)).strftime("%Y/%m/%d")
    today = datetime.datetime.utcnow().strftime("%Y/%m/%d")

    for query in queries:
        try:
            # Step 1: search for IDs
            search_url = (
                f"{base}/esearch.fcgi?db=pubmed"
                f"&term={requests.utils.quote(query)}"
                f"&mindate={seven_days_ago}&maxdate={today}"
                f"&datetype=pdat&retmax=10&retmode=json{api_suffix}"
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

    for drug in WATCHLIST[:8]:   # limit to 8 drugs per cycle to avoid flooding
        try:
            params = {
                "query.intr": drug,
                "filter.overallStatus": "RECRUITING,ACTIVE_NOT_RECRUITING",
                "pageSize": 5,
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
def poll_fda_rss(producer):
    log.info("=== Polling FDA Safety Alerts RSS ===")
    feeds = [
        "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/medwatch-safety-alerts/rss.xml",
        "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/recalls-market-withdrawals-and-safety-alerts/rss.xml",
    ]
    published = 0

    for feed_url in feeds:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:10]:   # last 10 entries per feed
                title   = entry.get("title", "")
                link    = entry.get("link", "")
                summary = entry.get("summary", "")[:500]
                pub     = entry.get("published", "")

                if not title:
                    continue

                # Generate a stable key from the link URL
                key = link.split("/")[-1] or title[:40]

                message = {
                    "source":    "fda_rss",
                    "title":     title,
                    "link":      link,
                    "summary":   summary,
                    "published": pub,
                    "feed_url":  feed_url,
                    "timestamp": datetime.datetime.utcnow().isoformat(),
                }
                publish(producer, "fda-alerts", key, message)
                published += 1

        except Exception as e:
            log.error(f"FDA RSS error for {feed_url}: {e}")

    log.info(f"FDA RSS poll done — {published} messages published")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN LOOP — each source runs on its own interval
# ══════════════════════════════════════════════════════════════════════════════
def main():
    log.info("🚀 PharmaWatch Kafka Producer starting up...")
    producer = create_producer()

    # Track last-run timestamps for each source
    last_faers   = 0   # every 10 minutes
    last_pubmed  = 0   # every 60 minutes
    last_trials  = 0   # every 60 minutes
    last_rss     = 0   # every 30 minutes

    FAERS_INTERVAL   = 10 * 60
    PUBMED_INTERVAL  = 60 * 60
    TRIALS_INTERVAL  = 60 * 60
    RSS_INTERVAL     = 30 * 60

    log.info("✅ Producer running. Topics: faers-raw | pubmed-stream | clinical-trials | fda-alerts")

    while True:
        now = time.time()

        if now - last_faers >= FAERS_INTERVAL:
            poll_faers(producer)
            last_faers = time.time()

        if now - last_rss >= RSS_INTERVAL:
            poll_fda_rss(producer)
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
