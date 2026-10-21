# backend/core/config.py
# The one place the backend reads its environment: every address, path, credential and limit is
# a Setting below, with a type, a single-machine default and a description. Moving a component to
# another machine (or a managed service) means changing settings, never code.
#
#   from core import config
#   config.get("KAFKA_BOOTSTRAP_SERVERS")
#
#   python -m core.config            # regenerate .env.example from this file
#
# Values are read at call time, so a .env loaded after import is honoured. A test fails if any
# other backend module reads os.environ or hard-codes a host, port or drive path.

import os
import sys
from dataclasses import dataclass

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT_DIR = os.path.dirname(BACKEND_DIR)
SRC_DIR = os.path.join(ROOT_DIR, "src")
REPO_DATA_DIR = os.path.join(ROOT_DIR, "data")           # small, versioned files (DDI splits)
SPLITS_DIR = os.path.join(REPO_DATA_DIR, "splits")
MODEL_DIR = os.path.join(BACKEND_DIR, "trained_models")
REPORTS_DIR = os.path.join(ROOT_DIR, "reports")
ENV_FILE = os.path.join(BACKEND_DIR, ".env")
ENV_EXAMPLE = os.path.join(ROOT_DIR, ".env.example")


@dataclass(frozen=True)
class Setting:
    name: str
    default: object
    kind: type
    group: str
    description: str
    secret: bool = False


SETTINGS = [
    # ── application ──
    Setting("HOST", "0.0.0.0", str, "app", "Interface the development server listens on"),
    Setting("PORT", 5000, int, "app", "Port the development server listens on"),
    Setting("ALLOWED_ORIGINS", "http://localhost:5000,http://127.0.0.1:5000", str, "app",
            "Comma-separated origins allowed by CORS"),
    Setting("ADMIN_API_KEY", "", str, "app", "Key for admin endpoints (X-Admin-Key header); empty disables them", secret=True),
    Setting("DATA_DIR", "", str, "app", "Large data (FAERS database, raw files, caches); empty means <repo>/data"),
    Setting("RATE_LIMIT_STORAGE_URI", "memory://", str, "app",
            "Where rate-limit counters live: memory:// on one machine, redis://host:6379 when several web copies share the limit"),
    # ── MySQL ──
    Setting("MYSQL_HOST", "localhost", str, "mysql", "MySQL host"),
    Setting("MYSQL_PORT", 3306, int, "mysql", "MySQL port"),
    Setting("MYSQL_USER", "root", str, "mysql", "MySQL user"),
    Setting("MYSQL_PASSWORD", "", str, "mysql", "MySQL password", secret=True),
    Setting("MYSQL_DATABASE", "pharmawatch", str, "mysql", "MySQL database name"),
    Setting("MYSQL_MAX_CONNECTIONS", 20, int, "mysql", "Connection pool size per process"),
    Setting("MYSQL_MIN_CACHED", 2, int, "mysql", "Idle connections kept open per process"),
    Setting("MYSQL_MAX_CACHED", 10, int, "mysql", "Most idle connections kept per process"),
    # ── stream, batch and serving ──
    Setting("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092", str, "bigdata", "Kafka brokers, comma-separated"),
    Setting("HBASE_HOST", "127.0.0.1", str, "bigdata", "HBase Thrift server host"),
    Setting("HBASE_PORT", 9090, int, "bigdata", "HBase Thrift server port"),
    Setting("SPARK_MASTER", "local[*]", str, "bigdata", "Spark master: local[*] on one machine, spark://host:7077 for a cluster"),
    Setting("FAERS_AUTO_REFRESH", False, bool, "bigdata", "Load a newly announced FAERS quarter without waiting for an admin"),
    # ── storage ──
    Setting("STORAGE_BACKEND", "local", str, "storage", "local (DATA_DIR) or s3 (any S3-compatible store: MinIO, S3, ...)"),
    Setting("S3_ENDPOINT_URL", "", str, "storage", "S3 endpoint; empty for AWS, e.g. http://minio:9000 for MinIO"),
    Setting("S3_BUCKET", "pharmawatch", str, "storage", "Bucket holding every storage area"),
    Setting("S3_REGION", "us-east-1", str, "storage", "S3 region"),
    Setting("S3_ACCESS_KEY_ID", "", str, "storage", "S3 access key", secret=True),
    Setting("S3_SECRET_ACCESS_KEY", "", str, "storage", "S3 secret key", secret=True),
    Setting("STORAGE_CACHE_DIR", "", str, "storage", "Local copies of stored files (models); empty means DATA_DIR/cache"),
    # ── background jobs ──
    Setting("QUEUE_MODE", "inline", str, "jobs", "inline (threads in the web process) or kafka (separate worker processes)"),
    Setting("JOB_THREADS", 2, int, "jobs", "Threads per process that run jobs"),
    Setting("JOB_LEASE_SECONDS", 120, int, "jobs", "How long a running job is held without a heartbeat before another worker may take it"),
    Setting("JOB_TOPIC_PARTITIONS", 6, int, "jobs", "Partitions per job topic: the most workers that can share one kind of job"),
    Setting("TORCH_THREADS", 0, int, "jobs",
            "CPU threads per worker for OCR and models; 0 = one per core. With several workers on one machine use cores / workers"),
    # ── external services ──
    Setting("FDA_API_KEY", "", str, "external", "openFDA API key", secret=True),
    Setting("NCBI_API_KEY", "", str, "external", "NCBI E-utilities API key", secret=True),
    Setting("ANTHROPIC_API_KEY", "", str, "external", "Enables the optional cloud prescription extractor", secret=True),
    Setting("RX_EXTRACTOR", "auto", str, "external", "Prescription extractor: auto, local or cloud"),
    Setting("RX_VISION_MODEL", "claude-opus-5-5", str, "external", "Model used by the cloud extractor"),
    Setting("SRLC_USER_AGENT",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            str, "external", "Browser User-Agent for fetching FDA SrLC label-change pages (validation/fetch_srlc.py)"),
    Setting("REDDIT_CLIENT_ID", "", str, "external", "Reddit API client id for exploratory social listening; empty disables it", secret=True),
    Setting("REDDIT_CLIENT_SECRET", "", str, "external", "Reddit API client secret; empty disables social listening", secret=True),
    Setting("REDDIT_USER_AGENT", "PharmaWatch/1.0 (research use)", str, "external", "User-Agent sent to the Reddit API"),
]
_BY_NAME = {s.name: s for s in SETTINGS}
_TRUE = {"1", "true", "yes", "on"}
_env_loaded = False


def load_env():
    """Load backend/.env once (existing environment variables win)."""
    global _env_loaded
    if _env_loaded:
        return
    _env_loaded = True
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    if os.path.exists(ENV_FILE):
        load_dotenv(ENV_FILE, override=False)


def raw(name):
    """The environment value of a setting as a string, or None when unset."""
    if name not in _BY_NAME:
        raise KeyError(f"unknown setting {name!r}; add it to core/config.py SETTINGS")
    load_env()
    value = os.environ.get(name)
    return None if value is None or value.strip() == "" else value.strip()


def get(name):
    """A setting's value, converted to its type; the default when unset."""
    s = _BY_NAME.get(name)
    value = raw(name)
    if value is None:
        return s.default
    if s.kind is bool:
        return value.lower() in _TRUE
    if s.kind is int:
        return int(value)
    return value


def env_example():
    """Text of .env.example: every setting, grouped, with its description and default."""
    lines = ["# PharmaWatch settings. Generated from backend/core/config.py by `python -m core.config`;",
             "# edit that file, not this one. Copy to backend/.env and fill in what you need.", ""]
    group = None
    for s in SETTINGS:
        if s.group != group:
            group = s.group
            lines += ["", f"# ── {group} ──"]
        default = "" if s.secret else (str(s.default).lower() if s.kind is bool else str(s.default))
        lines.append(f"# {s.description}")
        lines.append(f"{s.name}={default}")
    return "\n".join(lines).replace("\n\n\n", "\n\n") + "\n"


def data_dir():
    """Directory for the large FAERS files and databases.

    Taken from the DATA_DIR setting (for example C:/PharmaWatchData, or /app/data in a container)
    and falling back to <repo>/data. Read at call time so a .env loaded after import is honoured.
    """
    return get("DATA_DIR") or REPO_DATA_DIR


# Curated drug-event pairs shown by default in the signal table.
# Events are MedDRA Preferred Terms as they appear in FAERS (checked by test_identity).
WATCHLIST = [
    ("Warfarin", "Subdural haematoma"),
    ("Ciprofloxacin", "Tendon Rupture"),
    ("Metformin", "Lactic Acidosis"),
    ("Amiodarone", "Pulmonary Toxicity"),
    ("Clozapine", "Agranulocytosis"),
    ("Methotrexate", "Hepatotoxicity"),
    ("Simvastatin", "Rhabdomyolysis"),
    ("Isotretinoin", "Depression"),
    ("Lithium", "Nephropathy toxic"),
    ("Valproate", "Hepatotoxicity"),
    ("Haloperidol", "Electrocardiogram QT prolonged"),
    ("Oxycodone", "Respiratory Depression"),
    ("Paclitaxel", "Neuropathy peripheral"),
    ("Amlodipine", "Oedema peripheral"),
    ("Atorvastatin", "Myalgia")
]


if __name__ == "__main__":
    with open(ENV_EXAMPLE, "w", encoding="utf-8", newline="\n") as f:
        f.write(env_example())
    print(f"wrote {ENV_EXAMPLE} ({len(SETTINGS)} settings)", file=sys.stderr)
