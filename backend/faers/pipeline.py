# backend/faers/pipeline.py
# Bringing a newly released FAERS quarter into the database: download, parse, rebuild the
# database from every parsed quarter, rebuild the identity tables.
#
# A `faers-release` Kafka message records that a quarter is available (faers_releases table). The
# refresh itself always runs as a job, started by an admin or, when FAERS_AUTO_REFRESH is on, by
# the consumer; never inline in the consumer loop. The data version changes with the new quarter,
# so every cached result computed on the old data stops being served.


from core import config, jobs
from db.mysql import execute, query, query_one
from faers import downloader, identity_build, loader, parser
from faers.registry import list_quarters

JOB_KIND = "faers_refresh"
JOB_TTL_MINUTES = 7 * 24 * 60


def normalize_quarter(label):
    """'2026Q3', '2026q3' -> '2026q3'; raises ValueError for anything else."""
    year, qtr = downloader.parse_label(str(label))
    return downloader.quarter_label(year, qtr)


def loaded_quarters(db_file=None):
    return {q["quarter"] for q in list_quarters(db_file)}


def auto_refresh_enabled():
    return config.get("FAERS_AUTO_REFRESH")


def refresh(quarter, report=print):
    """Download, parse and load `quarter`, then rebuild identities. Returns a summary dict."""
    quarter = normalize_quarter(quarter)
    year, qtr = downloader.parse_label(quarter)
    report(f"downloading {quarter}")
    zip_path = downloader.download_quarter(year, qtr, log=report)
    report(f"parsing {quarter}")
    parser.parse_quarter(zip_path, log=report)
    report("rebuilding the database from all parsed quarters")
    summary = loader.load_database(log=report)
    report("rebuilding drug identities")
    code = identity_build.main([])
    if code:
        raise RuntimeError(f"identity build failed (exit {code}); the database has {quarter} without identities")
    return {"quarter": quarter, "load": summary}


def _run_refresh_job(job, report):
    quarter = job["meta"]["quarter"]
    try:
        result = refresh(quarter, report=report)
    except Exception:
        execute("UPDATE faers_releases SET status = 'failed' WHERE quarter = %s", (quarter,))
        raise
    execute("UPDATE faers_releases SET status = 'loaded' WHERE quarter = %s", (quarter,))
    return result


def register_job():
    jobs.register(JOB_KIND, _run_refresh_job)


def record_release(quarter, db_file=None):
    """Note that `quarter` is released. Returns 'already_loaded', 'recorded' or the existing status."""
    quarter = normalize_quarter(quarter)
    if quarter in loaded_quarters(db_file):
        return "already_loaded"
    row = query_one("SELECT status FROM faers_releases WHERE quarter = %s", (quarter,))
    if row:
        return row["status"]
    execute("INSERT INTO faers_releases (quarter, status) VALUES (%s, 'available') "
            "ON DUPLICATE KEY UPDATE quarter = quarter", (quarter,))
    return "recorded"


def start_refresh(quarter, created_by=None):
    """Start the refresh job for an announced quarter. Returns the job id, or None if one is running."""
    quarter = normalize_quarter(quarter)
    row = query_one("SELECT status, job_id FROM faers_releases WHERE quarter = %s", (quarter,))
    if row and row["status"] == "refreshing":
        return None
    register_job()
    job_id = jobs.submit(JOB_KIND, meta={"quarter": quarter}, ttl_minutes=JOB_TTL_MINUTES, created_by=created_by)
    execute("INSERT INTO faers_releases (quarter, status, job_id) VALUES (%s, 'refreshing', %s) "
            "ON DUPLICATE KEY UPDATE status = 'refreshing', job_id = VALUES(job_id)", (quarter, job_id))
    return job_id


def pending_releases(db_file=None):
    """Announced quarters not yet in the database, newest first."""
    loaded = loaded_quarters(db_file)
    rows = query("SELECT quarter, status, job_id, detected_at FROM faers_releases ORDER BY quarter DESC")
    return [dict(r, detected_at=str(r["detected_at"])) for r in rows if r["quarter"] not in loaded]

