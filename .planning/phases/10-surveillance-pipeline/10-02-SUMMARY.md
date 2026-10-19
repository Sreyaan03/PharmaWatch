# 10-02 Summary: scale-ready

Scaling out is now a settings change. The single-machine setup works as before: the same defaults, nothing extra to run.

## Done
- **One configuration surface.** `core/config.py` defines 36 settings (type, default, description, secret flag), and `.env.example` is generated from it. Every module now reads through it. A test fails on any `os.getenv` or `os.environ` outside that file, on any `dotenv` import, and on any hard-coded host, port or drive path; 7 hard-coded addresses were removed. The migration now runs as `python -m db.migrate` (CI updated).
- **Storage interface.** `core/storage.py` has areas `uploads`, `lake`, `models` and `reports`, with a `local` backend (the existing folder layout) and an `s3` backend (boto3, any S3-compatible store). Code that needs a real file path gets a cached copy that is refreshed when the stored object's ETag changes. Job uploads, per-drug forecaster models and the deployed interaction model all go through it. The same 25-test suite passes on both backends (S3 via moto). `python -m core.storage seed-models` copies shipped models into a bucket.
- **Queue interface and workers.** `core/queue.py`: `inline` (threads, the default) or `kafka` (topics `jobs.<kind>` with 6 partitions). `python -m workers.run` runs the same handlers. Jobs carry a lease (owner, expiry, attempts): a worker claims a job with one atomic update and renews the lease while it works. A sweeper requeues expired leases, and a job interrupted 3 times is failed with that reason. A message delivered twice runs the job once, and a late result from a worker that lost its lease is ignored. Inline startup requeues whatever the previous process left. Tests: the two modes give the same result, duplicate delivery, dead worker, the attempts cap, lease renewal during a long job, startup recovery.
- **Stateless web tier.** Two-process tests: a job uploaded through one app process is run by a separate worker process and read back; a cache written in one process is served in another. Every module-level cache was audited, and the decision for each is recorded in `docs/SCALING.md`; a test fails if a new one appears without an entry. Rate limits can be shared across copies (`RATE_LIMIT_STORAGE_URI`, Redis). New `/health/live` and `/health/ready` endpoints: ready is 503 until the required dependencies are reachable, and optional ones are checked within 0.5 s so they never slow the probe. Responses carry an `X-Instance` header naming the copy.
- **Scaled profile and check.** `docker/docker-compose.scale.yml` runs 2 web copies behind Caddy (round robin), 2 workers, an S3-compatible store (SeaweedFS), Redis, Kafka, MySQL, and a Spark master with 2 workers. Each count is one variable, and the profile is fully separate from the production stack. `scripts/scale_check.py` passed 6/6 checks: load balancing, job run on a worker, worker killed mid-job (finished by another in 45 s), shared storage, Spark cluster, readiness following dependencies.
- **Measured scaling.** Results are on the Methods page and in `docs/SCALING.md`. With default threads, 4 OCR workers were slower than 2 (thread oversubscription). With `TORCH_THREADS` = cores ÷ workers, 4 workers did 16.4 jobs/min against 9.6 for 1. Three web copies beat one in all three runs (latest 602 → 919 req/s, p95 36 → 26 ms), but absolute numbers varied up to 4 times between runs on this laptop.
- **Runbook.** `docs/SCALING.md` covers moving each component, its limits, and the path to Kubernetes.

## Found and fixed along the way
- EasyOCR downloaded its models on each new worker's first job (about 60 s); they are now built into the image.
- Kafka took too long to notice a dead worker; the worker session timeout is now 10 s.
- The readiness probe took 4–8 s: a DNS lookup for an absent HBase host, and a 2 s Kafka probe on Windows. Optional checks now have a 0.5 s budget.
- The rate limiter counted per process.
- The scale check's own bugs: a truncated model list, and recovery timed after the restart instead of at job completion.

## Limits and open points
- The measurements come from one laptop on a Balanced power plan, with Windows Modern Standby interrupting runs (stay-awake is used for measurement now). Read them as "the mechanism works", not as capacity.
- One worker-kill run took 11 minutes while the machine was running slowly; the cause was not established.
- MinIO's images can no longer be pulled anonymously, so the reference profile uses SeaweedFS. The app only speaks S3.
- DuckDB stays a per-copy read-only engine, and MySQL is a single primary (see SCALING.md).
- The production Dockerfile still copies data files into the image (Phase 11).
- Spark output equality and the HBase-stopped check join `scale_check` in 10-03.
