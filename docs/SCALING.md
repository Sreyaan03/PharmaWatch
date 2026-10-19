# Scaling PharmaWatch

PharmaWatch runs on one machine by default. Everything that would tie it to that machine sits behind a setting, so scaling out means changing settings and copy counts, not code. This guide covers:

- what each setting switches;
- how to move each component to its own machine or a managed service;
- what does not scale freely;
- how the scale-out behaviour is checked and measured.

All settings are defined in `backend/core/config.py`, and `.env.example` is generated from it (`python -m core.config` in `backend/`). A test fails if any other module reads the environment or hard-codes a host, port or drive path.

## The four switches

| Setting | One machine (default) | Scaled out |
|---|---|---|
| `QUEUE_MODE` | `inline`: jobs run on threads inside the web process | `kafka`: jobs go to `jobs.<kind>` topics; `python -m workers.run` processes run them |
| `STORAGE_BACKEND` | `local`: uploads, lake, models and reports on local disk | `s3`: one bucket on MinIO, S3 or any S3-compatible store (`S3_ENDPOINT_URL`, `S3_BUCKET`, keys) |
| `RATE_LIMIT_STORAGE_URI` | `memory://` | `redis://host:6379`, so all web copies share one limit |
| `SPARK_MASTER` | `local[*]` | `spark://host:7077`, or a managed Spark service |

Each component's address is a setting too: `MYSQL_HOST`, `KAFKA_BOOTSTRAP_SERVERS`, `HBASE_HOST`, `S3_ENDPOINT_URL`.

## The reference scaled deployment

`docker/docker-compose.scale.yml` runs, on one machine:

- 2 web copies behind Caddy (round robin);
- 2 workers;
- an S3-compatible object store (SeaweedFS), Redis, Kafka and MySQL;
- a Spark master with 2 workers.

Each count is one variable:

```
WEB_REPLICAS=3 WORKER_REPLICAS=4 SPARK_WORKERS=2 \
  docker compose -p pwscale -f docker/docker-compose.scale.yml up -d --build
python scripts/scale_check.py            # checks; add --measure for throughput measurements
docker compose -p pwscale -f docker/docker-compose.scale.yml down -v
```

The FAERS database is mounted read-only from `SCALE_FAERS_DB`. The default is the small fixture in the image; for realistic data use a sample made with `python -m faers.sample_db --percent 5 --out <file>`. The checks below were run on a 5% sample (360,384 cases).

It shares nothing with the production stack: it has its own project, network and volumes, and no fixed container names. Only Caddy (`127.0.0.1:8088`, `SCALE_HTTP_PORT`) and the Spark UI (`127.0.0.1:18080`) are published.

`scripts/scale_check.py` checks the stack from outside, through the load balancer:

- requests are answered by more than one web copy;
- an upload taken by one web copy is run by a worker container and readable from any copy;
- a worker killed in the middle of a job does not lose it: another worker finishes it;
- uploads are removed from shared storage after the job, and the model files are in the store;
- the Spark master sees every worker;
- a copy reports not-ready (503) when a required dependency is stopped, while still reporting live.

Results go to `reports/validation/scaling.json`.

## How jobs survive a dead worker

A job runs only after a worker **claims** it: one `UPDATE` on the `jobs` row sets it to running with the worker's name and a **lease** (`JOB_LEASE_SECONDS`). The worker renews the lease while it works.

If the worker dies, the lease runs out. Each worker's sweeper puts expired jobs back in the queue (`jobs.recover_interrupted`), and another worker claims them.

A Kafka message delivered twice finds the job already claimed or finished, so no job runs twice. A job interrupted `MAX_ATTEMPTS` (3) times is failed with that reason instead of being retried forever.

The worker commits its Kafka offset only after the job has finished. A late result from a worker that lost its lease is ignored.

## Moving each component

| Component | To move it | Settings | Limits |
|---|---|---|---|
| Web app | Run more copies of the same image behind any load balancer. Use `/health/ready` for routing (503 until MySQL, storage, the FAERS database and, in kafka mode, Kafka are reachable) and `/health/live` for restarts. | none beyond the shared ones | Each copy loads the models (about 1–2 GB of RAM). |
| Workers | Run `python -m workers.run` anywhere that can reach Kafka, MySQL and storage. Use `--kinds` to give machines different job types (for example OCR-only machines). | `QUEUE_MODE=kafka`, `JOB_THREADS`, `JOB_LEASE_SECONDS` | Up to `JOB_TOPIC_PARTITIONS` (6) workers share one job kind; raise it, and add partitions to existing topics, for more. |
| Storage | Point at S3, MinIO, SeaweedFS or another S3-compatible service. Run `python -m core.storage seed-models` once to copy the shipped models into the bucket. | `STORAGE_BACKEND=s3`, `S3_*` | Code that needs a real file path gets a cached local copy (`STORAGE_CACHE_DIR`), refreshed when the object changes. |
| MySQL | Use a managed MySQL 8 instance; run `python -m db.migrate` against it. | `MYSQL_*` | One primary takes every write (jobs, caches, alerts). For heavy use, add a read replica for the dashboards or move caches to Redis. The pool size is per process (`MYSQL_MAX_CONNECTIONS`), so total connections = copies × pool size. |
| Kafka | Use a managed Kafka or a multi-broker cluster. | `KAFKA_BOOTSTRAP_SERVERS` | Raise topic replication from 1 when there is more than one broker. |
| Spark | Use a standalone cluster or a managed Spark service that reads the lake from the store (`s3a://`); `python -m bigdata.run_spark` submits the job. | `SPARK_MASTER` | On S3 the job uses the S3A magic committer (no renames) and appends to a results folder the runner has emptied, because S3-compatible stores differ on directory deletes. On one machine DuckDB runs the same job faster (27 s vs 125 s); Spark is the part that moves to a cluster unchanged. |
| HBase | Point at a distributed HBase; reload with `python -m bigdata.hbase_history` after each Spark run. | `HBASE_HOST`, `HBASE_PORT` | It is optional. The app answers from the Parquet results when HBase is down (about 2 s instead of 0.1 s). The single-node container loads 651,603 rows in about 100 s; a cluster should pre-split regions by row-key prefix. |
| FAERS database (DuckDB) | Ship a read-only copy with each web copy and worker (image layer or read-only volume). | `DATA_DIR` | DuckDB is a single-file, single-writer engine: it stays the per-copy interactive engine and does not scale out. Heavy all-data work belongs to Spark. A refresh writes a new file that is then distributed. |
| Rate limits | Run Redis, or a managed Redis. | `RATE_LIMIT_STORAGE_URI` | With `memory://` each copy counts separately. |

## Kubernetes

The same images work unchanged:

- one Deployment each for `web` and `worker`;
- configuration from a ConfigMap, credentials (`MYSQL_PASSWORD`, `S3_*`, API keys) from Secrets;
- `/health/ready` as the readiness probe and `/health/live` as the liveness probe;
- a Job for `python -m db.migrate` and `python -m core.storage seed-models`;
- the FAERS database from a read-only volume or baked into the image.

Spark, Kafka, MySQL and object storage are best used as managed services or their own operators.

## State audit

Every module-level value that persists between requests was checked: either it is shared (database or storage), or it is a per-process cache that is safe to duplicate because it is read-only or can always be rebuilt. `tests/test_statelessness.py` fails if a new one appears without an entry here.

| Where | What | Decision |
|---|---|---|
| `core/jobs.py` `_handlers` | Job kind → handler function | Code, not data: every process registers the same handlers at import. |
| `core/queue.py` `_pool` | Inline thread pool | Per process by design (inline mode is one machine). |
| `core/queue.py` `_producer` | Kafka producer connection | Per-process connection. |
| `core/storage.py` `_backend` | Storage client | Per-process client; files are in the shared store. |
| `core/storage.py` `_backend_key` | Settings the client was built with | Per process. |
| `ingest/consumer.py` `_index` | Drug and term names for linking | Read-only, rebuilt from the FAERS database; safe to duplicate. |
| `ml/ddi/inference.py` `_manifest` | Deployment manifest | Read-only, from storage; safe to duplicate. |
| `ml/ddi/inference.py` `_scorer` | Loaded interaction model | Read-only, from storage; safe to duplicate (memory per copy). |
| `services/evidence.py` `_identity` | Identity lookup service | Read-only, over the FAERS database; safe to duplicate. |
| `services/signal_history.py` `_identity` | Identity lookup service | Read-only, over the FAERS database; safe to duplicate. |
| `services/provenance.py` `_version_cache` | Data version, 60 s | Read-only; at most 60 s stale per copy. |
| `services/prescription/extract.py` `_lexicon_cache` | Drug-name dictionary | Read-only, keyed on the database file's timestamp; safe to duplicate. |

Other per-copy caches, safe to duplicate:

- `ml/ner.py` `ner_pipeline` and `ml/ocr.py` `easyocr_reader`: loaded models, read-only.
- `clients/hbase.py`: circuit-breaker timestamps. Each copy backs off on its own.
- `http_cache/`: the openFDA response cache (requests-cache, SQLite). Each copy fetches and caches separately. That costs extra API calls, not wrong results.
- The RxNav name cache (`DATA_DIR/rxnav_cache.jsonl`): append-only lookups. Each copy caches its own.

Shared state (correct across copies):

- jobs, job uploads, results;
- signal, temporal and demographic caches;
- alerts and evidence links;
- forecast models (storage);
- rate-limit counters (Redis, when configured).

## Measured scaling

Measured on one laptop (12 CPUs; Docker's VM had 12 CPUs and 8 GB RAM; Windows Balanced power plan) with `scripts/scale_check.py --measure --measure-workers-shared-cores`. The latest results are in `reports/validation/scaling.json` and on the Methods & Validation page. These numbers show the mechanism works and how it behaves on one machine. They are not cluster speedups: here every copy competes for the same cores and memory.

**Workers: 24 OCR prescription extractions** (latest run):

| Workers | Default threads (one per core each) | `TORCH_THREADS` = cores ÷ workers |
|---|---|---|
| 1 | 143 s (10.1 jobs/min) | 150 s (9.6 jobs/min) |
| 2 | 106 s (13.6 jobs/min) | 110 s (13.1 jobs/min) |
| 4 | 123 s (11.7 jobs/min) | **88 s (16.4 jobs/min)** |

With default threads, each worker's PyTorch starts a thread per core. Four workers then run about 48 threads on 12 cores and spend time competing, and four become slower than two. Sharing the cores out (`TORCH_THREADS=3` for 4 workers on 12 cores) gives about 1.7 times the throughput of one worker. An earlier, noisier run showed the same pattern: 4 workers on 3 threads was its best result as well. **When several workers share a machine, set `TORCH_THREADS` to cores ÷ workers.** On separate machines leave it at 0.

**Web copies: 16 concurrent clients for 30 s on `/api/dashboard/top-events`, no errors in any run:**

| Run | 1 copy | 3 copies |
|---|---|---|
| Latest | 602 req/s, p95 36 ms | 919 req/s, p95 26 ms |
| Earlier | 206 req/s, p95 96 ms | 741 req/s, p95 36 ms |
| Earlier, while the machine was running slow | 137 req/s, p95 150 ms | 204 req/s, p95 163 ms |

More copies gave more throughput in every run, but the absolute numbers vary by up to 4 times between runs on this laptop. The two earlier runs were also made before a fix to the readiness probe, which then took 4–8 s because of a DNS lookup for an absent service, and each copy's health check held one of its request threads that long. Read these as "adding copies helps", not as capacity figures.

**Worker killed mid-job:** the job finished on another worker 45–60 s after the kill in five runs, with a 20-second lease (lease expiry, the next sweep, a rebalance, then the job itself). One run took 11 minutes while the whole machine was running about twice as slowly as usual. The cause was not established, because the logs were lost when the workers were recreated. The first run, made before the OCR models were built into the image, took 201 s; about a minute of that was downloading the models.
