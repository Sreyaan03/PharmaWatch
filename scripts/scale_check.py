"""
Checks the scaled profile (docker/docker-compose.scale.yml) from outside, through the load
balancer, the way a user and an operator would see it, and optionally measures how throughput
changes with the number of copies.

    docker compose -p pwscale -f docker/docker-compose.scale.yml up -d --build
    python scripts/scale_check.py                 # checks; writes reports/validation/scaling.json
    python scripts/scale_check.py --measure       # also scaling measurements (takes ~20 minutes)

Checks:
  load_balancing      requests through Caddy are answered by more than one web copy
  job_on_a_worker     a prescription uploaded through the balancer is run by a worker container,
                      not by the web copy that took it, and its result is readable from any copy
  worker_killed       a worker killed in the middle of a job: another worker finishes it
  shared_storage      uploads are gone from shared storage after the job; models are there
  spark_cluster       the Spark master sees every worker
  spark_job_on_cluster  the all-pairs job on the cluster (lake in S3) equals the same job in local mode
  readiness           a copy reports not-ready when a required dependency (Kafka) is stopped

Measurements are made on one machine. They show that the scale-out mechanism works and how it
behaves here; they are not cluster speedups.
"""

import argparse
import json
import os
import platform
import statistics
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMPOSE = ["docker", "compose", "-p", "pwscale", "-f", os.path.join(ROOT, "docker", "docker-compose.scale.yml")]
BASE = f"http://127.0.0.1:{os.environ.get('SCALE_HTTP_PORT', '8088')}"
SPARK_UI = "http://127.0.0.1:18080/json/"
FIXTURES = os.path.join(ROOT, "backend", "tests", "fixtures", "prescriptions")
OUT = os.path.join(ROOT, "reports", "validation", "scaling.json")
MYSQL_PASSWORD = os.environ.get("MYSQL_ROOT_PASSWORD", "root")


def compose(*args, check=True, capture=True):
    out = subprocess.run(COMPOSE + list(args), capture_output=capture, text=True, cwd=ROOT)
    if check and out.returncode != 0:
        raise RuntimeError(f"docker compose {' '.join(args)} failed: {out.stderr[-800:]}")
    return out.stdout


def sql(query):
    out = compose("exec", "-T", "mysql", "mysql", "-uroot", f"-p{MYSQL_PASSWORD}", "pharmawatch", "-N", "-B", "-e", query)
    return [line.split("\t") for line in out.strip().splitlines() if line.strip()]


def in_web(code):
    return compose("exec", "-T", "web", "python", "-c", code).strip().splitlines()[-1]


def wait_ready(timeout=420):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if requests.get(f"{BASE}/health/ready", timeout=5).status_code == 200:
                return True
        except requests.RequestException:
            pass
        time.sleep(3)
    raise TimeoutError("the scaled stack did not become ready through Caddy")


def replicas(service):
    return len([l for l in compose("ps", "-q", service).splitlines() if l.strip()])


def upload(path_or_text, name="rx.txt"):
    data = open(path_or_text, "rb").read() if os.path.exists(path_or_text) else path_or_text.encode()
    r = requests.post(f"{BASE}/api/prescription", files={"file": (name, data)}, timeout=30)
    r.raise_for_status()
    return r.json()["job_id"], r.headers.get("X-Instance")


def wait_job(job_id, timeout=600):
    end, seen = time.time() + timeout, set()
    while time.time() < end:
        r = requests.get(f"{BASE}/api/prescription/{job_id}", timeout=15)
        seen.add(r.headers.get("X-Instance"))
        body = r.json()
        if body.get("status") in ("done", "failed"):
            return body, seen
        time.sleep(1)
    raise TimeoutError(f"job {job_id} did not finish")


def lease_owner(job_id):
    rows = sql(f"SELECT status, lease_owner, attempts FROM jobs WHERE id = '{job_id}'")
    return rows[0] if rows else None


# ── checks ───────────────────────────────────────────────────────────────────
def check_load_balancing():
    seen = {requests.get(f"{BASE}/health/ready", timeout=10).headers.get("X-Instance") for _ in range(30)}
    want = min(2, replicas("web"))
    return len(seen) >= want, {"web_copies": replicas("web"), "copies_that_answered": sorted(seen)}


def check_job_on_a_worker():
    job_id, took = upload("Tab Warfarin 5 mg OD\nTab Aspirin 75 mg OD\nTab Metformin 500 mg BD")
    body, readers = wait_job(job_id, timeout=180)
    status, owner, attempts = lease_owner(job_id)
    web_names = {h for h in readers if h} | {took}
    ok = (body["status"] == "done" and len(body["extraction"]["lines"]) == 3
          and owner.split(":")[0] not in web_names)
    return ok, {"job": job_id, "taken_by_web_copy": took, "run_by": owner, "status_read_from": sorted(readers),
                "lines": len(body.get("extraction", {}).get("lines", []))}


def check_worker_killed():
    before = replicas("worker")
    image = os.path.join(FIXTURES, "rx_001.png")
    job_id, _ = upload(image, "rx.png")
    end, owner = time.time() + 120, None
    while time.time() < end:
        row = lease_owner(job_id)
        if row and row[0] == "running" and row[1] not in ("NULL", ""):
            owner = row[1]
            break
        time.sleep(0.3)
    if owner is None:
        return False, {"job": job_id, "error": "the job never reached a worker"}
    victim = owner.split(":")[0]                      # the container's hostname is its short id
    subprocess.run(["docker", "kill", victim], capture_output=True)
    killed_at = time.time()
    body, _ = wait_job(job_id, timeout=600)
    recovered_in = time.time() - killed_at
    status, final_owner, attempts = lease_owner(job_id)
    compose("up", "-d", "--scale", f"worker={before}", "worker")       # replace the killed worker
    ok = body["status"] == "done" and final_owner != owner
    return ok, {"job": job_id, "killed_worker": owner, "finished_by": final_owner, "attempts": int(attempts),
                "seconds_from_kill_to_done": round(recovered_in, 1),
                "lease_seconds": int(os.environ.get("JOB_LEASE_SECONDS", "20"))}


def check_shared_storage():
    left = in_web("from core import storage; print(len(storage.list_keys('uploads')))")
    models = in_web("from core import storage; print(','.join(storage.list_keys('models')))").split(",")
    ok = left == "0" and "ddi_manifest.json" in models
    return ok, {"uploads_left_in_store": int(left), "backend": "s3 (SeaweedFS)",
                "model_files_in_store": len(models), "deployed_manifest_in_store": "ddi_manifest.json" in models}


def check_spark_cluster():
    want = replicas("spark-worker")
    try:
        info = requests.get(SPARK_UI, timeout=10).json()
    except (requests.RequestException, ValueError) as exc:
        return False, {"error": str(exc)}
    alive = info.get("aliveworkers", 0)
    return alive == want, {"workers_registered": alive, "workers_started": want, "cores": info.get("cores")}


S3A = ["--packages", "org.apache.hadoop:hadoop-aws:3.3.4,org.apache.spark:spark-hadoop-cloud_2.12:3.5.3",
       "--conf", "spark.hadoop.fs.s3a.endpoint=http://objectstore:8333",
       "--conf", "spark.hadoop.fs.s3a.access.key=pwstore",
       "--conf", "spark.hadoop.fs.s3a.secret.key=pwstore-local-only",
       "--conf", "spark.hadoop.fs.s3a.path.style.access=true",
       "--conf", "spark.hadoop.fs.s3a.connection.ssl.enabled=false",
       "--conf", "spark.hadoop.fs.s3a.committer.name=magic",                # S3 has no rename: tasks upload, the job commits
       "--conf", "spark.hadoop.fs.s3a.committer.magic.enabled=true",
       "--conf", "spark.hadoop.fs.s3a.directory.marker.retention=keep",     # never delete directory markers
       "--conf", "spark.hadoop.fs.s3a.retry.limit=3",                       # fail in seconds, not minutes
       "--conf", "spark.hadoop.fs.s3a.attempts.maximum=3",
       "--conf", "spark.sql.sources.commitProtocolClass=org.apache.spark.internal.io.cloud.PathOutputCommitProtocol",
       "--conf", "spark.sql.parquet.output.committer.class=org.apache.spark.internal.io.cloud.BindingParquetOutputCommitter",
       "--conf", "spark.jars.ivy=/tmp/.ivy2"]
LAKE = "s3a://pharmawatch/lake"


def _spark_submit(master, *job_args):
    out = compose("exec", "-T", "spark-master", "/opt/spark/bin/spark-submit", "--master", master, *S3A,
                  "--executor-memory", "1g", "/job/spark_signals.py", "--lake", LAKE, *job_args)
    line = next((l for l in out.splitlines() if l.startswith("MANIFEST ")), None)
    if line is None:
        raise RuntimeError("the Spark job printed no manifest")
    return json.loads(line[len("MANIFEST "):])


def check_spark_job_on_cluster():
    """The batch job on the Spark cluster gives exactly what it gives in local mode, on the lake in S3."""
    compose("exec", "-T", "web", "python", "-m", "faers.export_lake")
    # Clear earlier results through the app's storage layer, so Spark's overwrite has nothing to
    # delete (S3-compatible stores differ in how they handle S3A's directory deletes).
    in_web("from core import storage; [storage.delete_prefix('lake', p) for p in "
           "('results/signals_local', 'results/signals_by_quarter')]; print('cleared')")
    local = _spark_submit("local[2]", "--out-name", "signals_local", "--run-id", "scale-local")
    cluster = _spark_submit("spark://spark-master:7077", "--run-id", "scale-cluster",
                            "--compare-with", f"{LAKE}/results/signals_local")
    cmp_ = cluster.get("comparison", {})
    ok = cluster["rows"] > 0 and cmp_.get("rows_in_only_one") == 0 and cmp_.get("rows_differing") == 0
    return ok, {"rows": cluster["rows"], "quarters": len(cluster["quarters"]),
                "local_seconds": local["seconds"], "cluster_seconds": cluster["seconds"],
                "cluster_master": cluster["master"], "comparison": cmp_,
                "data": in_web("from faers.registry import data_version; print(data_version())")}


def check_readiness_follows_dependencies():
    compose("stop", "kafka")
    try:
        time.sleep(3)
        code = int(in_web("import urllib.request, urllib.error\n"
                          "try:\n    print(urllib.request.urlopen('http://localhost:5000/health/ready').status)\n"
                          "except urllib.error.HTTPError as e:\n    print(e.code)"))
        live = int(in_web("import urllib.request; print(urllib.request.urlopen('http://localhost:5000/health/live').status)"))
    finally:
        compose("start", "kafka")
    wait_ready()
    return code == 503 and live == 200, {"ready_with_kafka_stopped": code, "live_with_kafka_stopped": live}


# ── measurements ─────────────────────────────────────────────────────────────
def measure_workers(counts=(1, 2, 4), jobs=24, share_cores=False):
    """OCR throughput against worker count. share_cores: TORCH_THREADS = CPUs / workers (else library default)."""
    images = sorted(f for f in os.listdir(FIXTURES) if f.endswith((".png", ".jpg")))
    cpus = int(subprocess.run(["docker", "info", "--format", "{{.NCPU}}"], capture_output=True, text=True).stdout.strip() or 1)
    results = []
    for n in counts:
        threads = max(1, cpus // n) if share_cores else 0
        os.environ["TORCH_THREADS"] = str(threads)
        compose("up", "-d", "--force-recreate", "--scale", f"worker={n}", "worker")
        time.sleep(20)                                  # consumer group rebalance
        started = time.time()
        ids = [upload(os.path.join(FIXTURES, images[i % len(images)]), "rx" + os.path.splitext(images[i % len(images)])[1])[0]
               for i in range(jobs)]
        for job_id in ids:
            wait_job(job_id, timeout=1800)
        seconds = time.time() - started
        results.append({"workers": n, "torch_threads_per_worker": threads or "default (one per core)",
                        "jobs": jobs, "seconds": round(seconds, 1), "jobs_per_minute": round(jobs / seconds * 60, 2)})
        print(f"[measure] {n} worker(s), threads {threads or 'default'}: {jobs} OCR jobs in {seconds:.0f} s", flush=True)
    os.environ.pop("TORCH_THREADS", None)
    compose("up", "-d", "--force-recreate", "--scale", "worker=2", "worker")
    return results


def _load(path, seconds, concurrency):
    latencies, errors, stop = [], [0], time.time() + seconds
    lock = threading.Lock()

    def loop():
        s = requests.Session()
        while time.time() < stop:
            t = time.time()
            try:
                ok = s.get(f"{BASE}{path}", timeout=30).status_code < 500
            except requests.RequestException:
                ok = False
            with lock:
                if ok:
                    latencies.append(time.time() - t)
                else:
                    errors[0] += 1
    threads = [threading.Thread(target=loop) for _ in range(concurrency)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    latencies.sort()
    return {"requests": len(latencies), "errors": errors[0], "per_second": round(len(latencies) / seconds, 1),
            "p50_ms": round(statistics.median(latencies) * 1000, 1) if latencies else None,
            "p95_ms": round(latencies[int(len(latencies) * 0.95) - 1] * 1000, 1) if latencies else None}


def measure_web(counts=(1, 3), path="/api/dashboard/top-events?limit=7", seconds=30, concurrency=16):
    results = []
    for n in counts:
        compose("up", "-d", "--no-recreate", "--scale", f"web={n}", "web")
        time.sleep(45)                                  # start, become healthy, Caddy re-reads DNS
        wait_ready()
        _load(path, 5, concurrency)                     # warm-up
        r = _load(path, seconds, concurrency)
        results.append({"web_copies": n, "path": path, "concurrency": concurrency, "seconds": seconds, **r})
        print(f"[measure] {n} web copy(ies): {r['per_second']} req/s, p95 {r['p95_ms']} ms", flush=True)
    compose("up", "-d", "--no-recreate", "--scale", "web=2", "web")
    return results


def machine():
    try:
        import psutil
        ram = round(psutil.virtual_memory().total / 1e9, 1)
    except ImportError:
        ram = None
    return {"os": platform.platform(), "cpus": os.cpu_count(), "ram_gb": ram}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--measure", action="store_true", help="also measure throughput against copy counts")
    ap.add_argument("--only", nargs="+", help="run only these checks (earlier results for the others are kept)")
    ap.add_argument("--measure-workers-shared-cores", action="store_true",
                    help="only re-measure workers with TORCH_THREADS = CPUs / workers, keeping other results")
    args = ap.parse_args(argv)
    wait_ready()

    checks = {}
    for name, fn in [("load_balancing", check_load_balancing), ("job_on_a_worker", check_job_on_a_worker),
                     ("worker_killed", check_worker_killed),
                     ("shared_storage", check_shared_storage),
                     ("spark_cluster", check_spark_cluster),
                     ("spark_job_on_cluster", check_spark_job_on_cluster),
                     ("readiness", check_readiness_follows_dependencies)]:
        if args.only and name not in args.only:
            continue
        try:
            ok, detail = fn()
        except Exception as exc:
            ok, detail = False, {"error": f"{type(exc).__name__}: {exc}"}
        checks[name] = {"passed": bool(ok), **detail}
        print(f"{'PASS' if ok else 'FAIL'} {name}  {json.dumps(detail)[:300]}", flush=True)

    if args.only and os.path.exists(OUT):               # keep earlier results of the checks not rerun
        previous = json.load(open(OUT, encoding="utf-8")).get("checks", {})
        checks = {**{k: v for k, v in previous.items() if k not in checks}, **checks}
    report = {"run_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "machine": machine(),
              "profile": "docker/docker-compose.scale.yml", "checks": checks,
              "note": "Measured on one machine: this shows the scale-out mechanism works; cluster speedups will differ."}
    earlier = json.load(open(OUT, encoding="utf-8")).get("measurements") or {} if os.path.exists(OUT) else {}
    if args.measure:
        report["measurements"] = {"workers_ocr_jobs": measure_workers(), "web_copies_load": measure_web()}
    else:                                              # keep earlier measurements when only checking
        report["measurements"] = earlier
    if args.measure_workers_shared_cores:
        report["measurements"]["workers_ocr_jobs_shared_cores"] = measure_workers(share_cores=True)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        json.dump(report, f, indent=2)
        f.write("\n")
    failed = [n for n, c in checks.items() if not c["passed"]]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed; wrote {OUT}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
