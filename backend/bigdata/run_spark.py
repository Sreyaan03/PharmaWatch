# backend/bigdata/run_spark.py
# Runs the Spark all-pairs job (bigdata/spark_signals.py) where SPARK_MASTER says, records its
# manifest in the lake, and announces the result on the `signals-updated` Kafka topic.
#
#   python -m bigdata.run_spark                       # SPARK_MASTER=local[*]: a throwaway Spark container
#   python -m bigdata.run_spark --compose-project pwscale
#                                                     # SPARK_MASTER=spark://...: submitted inside the scaled profile
#
# Local mode mounts the lake folder into the official Spark image, so this machine needs Docker,
# not Java. Cluster mode submits from the Spark master container; the lake is then read from the
# S3-compatible store (s3a://), which every Spark worker can reach.

import argparse
import json
import os
import subprocess
import sys
import time

from core import config, storage
from faers.registry import data_version

SPARK_IMAGE = "apache/spark:3.5.3"
JOB_DIR = os.path.dirname(os.path.abspath(__file__))
RESULTS = "results/signals_by_quarter"
HADOOP_AWS = "org.apache.hadoop:hadoop-aws:3.3.4,org.apache.spark:spark-hadoop-cloud_2.12:3.5.3"   # Spark 3.5's Hadoop


def _s3a_conf():
    return ["--packages", HADOOP_AWS,
            "--conf", f"spark.hadoop.fs.s3a.endpoint={config.get('S3_ENDPOINT_URL')}",
            "--conf", f"spark.hadoop.fs.s3a.access.key={config.get('S3_ACCESS_KEY_ID')}",
            "--conf", f"spark.hadoop.fs.s3a.secret.key={config.get('S3_SECRET_ACCESS_KEY')}",
            "--conf", "spark.hadoop.fs.s3a.path.style.access=true",
            "--conf", "spark.hadoop.fs.s3a.connection.ssl.enabled=false",
            "--conf", "spark.jars.ivy=/tmp/.ivy2",
            # S3 has no rename: the magic committer uploads results in place of renaming them; it keeps its
            # pending state in S3, so it works when the driver and executors are on different machines
            "--conf", "spark.hadoop.fs.s3a.committer.name=magic",
            "--conf", "spark.hadoop.fs.s3a.committer.magic.enabled=true",
            "--conf", "spark.hadoop.fs.s3a.directory.marker.retention=keep",
            "--conf", "spark.hadoop.fs.s3a.retry.limit=3",
            "--conf", "spark.hadoop.fs.s3a.attempts.maximum=3",
            "--conf", "spark.sql.sources.commitProtocolClass=org.apache.spark.internal.io.cloud.PathOutputCommitProtocol",
            "--conf", "spark.sql.parquet.output.committer.class=org.apache.spark.internal.io.cloud.BindingParquetOutputCommitter"]


def command(run_id, version, driver_memory="5g", compose_project=None):
    """The docker command line for this run."""
    master = config.get("SPARK_MASTER")
    job_args = ["--run-id", run_id, "--data-version", version]
    if master.startswith("local"):
        if storage.backend().name != "local":
            raise RuntimeError("local Spark reads a local lake; with STORAGE_BACKEND=s3 use a Spark cluster")
        lake = storage.backend().root("lake")
        os.makedirs(lake, exist_ok=True)
        storage.delete_prefix("lake", RESULTS)        # the job appends to an empty folder
        return ["docker", "run", "--rm", "-v", f"{lake}:/lake", "-v", f"{JOB_DIR}:/job:ro", SPARK_IMAGE,
                "/opt/spark/bin/spark-submit", "--master", master, "--driver-memory", driver_memory,
                "/job/spark_signals.py", "--lake", "/lake", "--manifest-out", "/lake/.spark_manifest.json",
                *job_args]
    if not compose_project:
        raise RuntimeError(f"SPARK_MASTER={master}: pass --compose-project (or submit from your cluster's edge node)")
    lake = storage.uri("lake")
    storage.delete_prefix("lake", RESULTS)            # the job appends to an empty folder
    return ["docker", "compose", "-p", compose_project, "exec", "-T", "spark-master",
            "/opt/spark/bin/spark-submit", "--master", master, *(_s3a_conf() if lake.startswith("s3a://") else []),
            "--executor-memory", "1g", "/job/spark_signals.py", "--lake", lake, *job_args]


def publish_signals_updated(manifest):
    """Announce the new results; returns False (and says why) when Kafka is not reachable."""
    try:
        from kafka import KafkaProducer
        producer = KafkaProducer(bootstrap_servers=config.get("KAFKA_BOOTSTRAP_SERVERS"),
                                 value_serializer=lambda v: json.dumps(v).encode("utf-8"),
                                 key_serializer=lambda k: k.encode("utf-8"), max_block_ms=5000)
        producer.send("signals-updated", key=manifest["run_id"], value={
            "run_id": manifest["run_id"], "quarters": manifest["quarters"], "latest_quarter": manifest["quarters"][-1],
            "data_version": manifest["data_version"], "results": f"lake/{RESULTS}"}).get(timeout=10)
        producer.close()
        return True
    except Exception as exc:
        print(f"[-] signals-updated not published ({type(exc).__name__}: {exc})", file=sys.stderr)
        return False


def run(compose_project=None, driver_memory="5g", publish=True, log=print):
    run_id = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    version = data_version()
    cmd = command(run_id, version, driver_memory, compose_project)
    log(f"[*] Spark run {run_id} on {config.get('SPARK_MASTER')}")
    started = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True)
    wall = round(time.time() - started, 1)
    line = next((l for l in proc.stdout.splitlines() if l.startswith("MANIFEST ")), None)
    if proc.returncode != 0 or line is None:
        text = proc.stderr or proc.stdout
        at = text.rfind("Traceback (most recent call last)")
        tail = text[at:at + 4000] if at >= 0 else "\n".join(text.splitlines()[-25:])
        raise RuntimeError(f"Spark job failed (exit {proc.returncode}):\n{tail}")
    manifest = json.loads(line[len("MANIFEST "):])
    manifest["wall_seconds_including_startup"] = wall
    manifest["published"] = publish and publish_signals_updated(manifest)
    storage.put("lake", f"{RESULTS}/_manifest.json", json.dumps(manifest, indent=2).encode())
    log(f"[+] {manifest['rows']:,} pair-quarter rows for {len(manifest['quarters'])} quarters "
        f"in {manifest['seconds']} s of Spark ({wall} s with start-up); latest quarter: {manifest['latest_quarter_classes']}")
    return manifest


def read_manifest():
    raw = storage.get("lake", f"{RESULTS}/_manifest.json")
    return json.loads(raw) if raw else None


def main(argv=None):
    ap = argparse.ArgumentParser(description="Run the Spark all-pairs job.")
    ap.add_argument("--compose-project", help="scaled profile project name when SPARK_MASTER is a cluster")
    ap.add_argument("--driver-memory", default="5g")
    ap.add_argument("--no-publish", action="store_true", help="do not announce on Kafka")
    args = ap.parse_args(argv)
    config.load_env()
    try:
        run(args.compose_project, args.driver_memory, publish=not args.no_publish)
    except RuntimeError as exc:
        print(f"[-] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
