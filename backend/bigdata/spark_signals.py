# backend/bigdata/spark_signals.py
# Spark batch job: disproportionality for every drug ingredient x MedDRA Preferred Term pair, for
# every loaded quarter, cumulative from the first quarter through that quarter.
#
# Reads the lake written by faers.export_lake (lake/clean/{case,case_drug,reaction}) and writes
#   lake/results/signals_by_quarter/quarter=<q>/*.parquet
#     rxcui, pt_lower, a, b, c, d, prr, prr_ci_low, prr_ci_high, ror, ror_ci_low, ror_ci_high,
#     chi2, bcpnn_ic, ic025, signal_strength, continuity_correction
#   lake/results/signals_by_quarter/_manifest.json
#
# The formulas are those of faers.disproportionality.compute_metrics (Evans criteria for a signal;
# ROR lower bound > 1 and IC025 > 0 in addition for a strong signal; Haldane-Anscombe 0.5 added to
# every cell when any cell is zero, for PRR and ROR only). Values are written unrounded; the
# agreement check (bigdata.verify) compares them with compute_metrics on sampled pairs.
# Pairs are kept from the first quarter in which they have 3 cases (fewer cannot be a signal).
#
# Runs anywhere Spark runs (Python 3.8+, no other dependencies):
#   spark-submit --master local[*] spark_signals.py --lake /lake --run-id <id>
#   spark-submit --master spark://spark-master:7077 spark_signals.py --lake s3a://bucket/lake ...

import argparse
import json
import sys
import time

MIN_CASES = 3
PRR_THRESHOLD = 2.0
CHI2_THRESHOLD = 4.0
Z_95 = 1.959964

METRICS_SQL = """
WITH t AS (
    SELECT rxcui, pt_lower, quarter, a, b, c, d, a + b + c + d AS n,
           CASE WHEN least(a, b, c, d) = 0 THEN 0.5 ELSE 0.0 END AS k
    FROM tables
),
k AS (
    SELECT *, a + k AS ak, b + k AS bk, c + k AS ck, d + k AS dk FROM t
),
m AS (
    SELECT rxcui, pt_lower, quarter, a, b, c, d, n, k,
           (ak / (ak + bk)) / (ck / (ck + dk)) AS prr,
           sqrt(1 / ak - 1 / (ak + bk) + 1 / ck - 1 / (ck + dk)) AS prr_se,
           (ak * dk) / (bk * ck) AS ror,
           sqrt(1 / ak + 1 / bk + 1 / ck + 1 / dk) AS ror_se,
           CASE WHEN (a + b) * (c + d) * (a + c) * (b + d) > 0
                THEN n * pow(greatest(0.0, abs(a * d - b * c) - n / 2.0), 2)
                     / ((a + b) * (c + d) * (a + c) * (b + d))
                ELSE 0.0 END AS chi2,
           log2((a + 0.5) / ((a + b) * (a + c) / n + 0.5)) AS bcpnn_ic
    FROM k
),
f AS (
    SELECT rxcui, pt_lower, quarter, a, b, c, d,
           prr, exp(ln(prr) - {z} * prr_se) AS prr_ci_low, exp(ln(prr) + {z} * prr_se) AS prr_ci_high,
           ror, exp(ln(ror) - {z} * ror_se) AS ror_ci_low, exp(ln(ror) + {z} * ror_se) AS ror_ci_high,
           chi2, bcpnn_ic,
           bcpnn_ic - 3.3 * pow(a + 0.5, -0.5) - 2 * pow(a + 0.5, -1.5) AS ic025,
           k > 0 AS continuity_correction
    FROM m
)
SELECT *,
       CASE WHEN a >= {min_cases} AND prr >= {prr} AND chi2 >= {chi2}
            THEN CASE WHEN ror_ci_low > 1.0 AND ic025 > 0.0 THEN 'strong_signal' ELSE 'signal' END
            ELSE 'none' END AS signal_strength
FROM f
""".format(z=Z_95, min_cases=MIN_CASES, prr=PRR_THRESHOLD, chi2=CHI2_THRESHOLD)


def build(spark, lake):
    """DataFrame of every kept pair and quarter with its 2x2 table and metrics."""
    from pyspark.sql import Window
    from pyspark.sql import functions as F

    case = spark.read.parquet(f"{lake}/clean/case")
    drug = spark.read.parquet(f"{lake}/clean/case_drug")
    reac = spark.read.parquet(f"{lake}/clean/reaction")

    quarters = sorted(r[0] for r in case.select("quarter").distinct().collect())
    qdf = spark.createDataFrame([(q, i) for i, q in enumerate(quarters)], ["quarter", "qi"])
    upto = Window.partitionBy().orderBy("qi").rowsBetween(Window.unboundedPreceding, 0)

    def cumulative(counts, key):
        """counts per (key, quarter) -> cumulative counts for every key and every quarter."""
        keys = counts.select(key).distinct()
        dense = keys.crossJoin(qdf).join(counts, [key, "quarter"], "left").fillna(0, ["n"])
        w = Window.partitionBy(key).orderBy("qi").rowsBetween(Window.unboundedPreceding, 0)
        return dense.withColumn("cum", F.sum("n").over(w)).select(key, "quarter", "cum")

    n_total = (case.groupBy("quarter").agg(F.count("*").alias("n")).join(qdf, "quarter")
               .withColumn("N", F.sum("n").over(upto)).select("quarter", "N"))
    drug_tot = cumulative(drug.groupBy("rxcui", "quarter").agg(F.count("*").alias("n")), "rxcui") \
        .withColumnRenamed("cum", "exposed")
    event_tot = cumulative(reac.groupBy("pt_lower", "quarter").agg(F.count("*").alias("n")), "pt_lower") \
        .withColumnRenamed("cum", "events")

    pair_q = (drug.join(reac, ["primaryid", "quarter"])
              .groupBy("rxcui", "pt_lower", "quarter").agg(F.count("*").alias("n")))
    kept = (pair_q.groupBy("rxcui", "pt_lower").agg(F.sum("n").alias("total"))
            .where(F.col("total") >= MIN_CASES).select("rxcui", "pt_lower"))
    w = Window.partitionBy("rxcui", "pt_lower").orderBy("qi").rowsBetween(Window.unboundedPreceding, 0)
    pair_cum = (kept.crossJoin(qdf).join(pair_q, ["rxcui", "pt_lower", "quarter"], "left").fillna(0, ["n"])
                .withColumn("a", F.sum("n").over(w)).where(F.col("a") >= MIN_CASES)
                .select("rxcui", "pt_lower", "quarter", "a"))

    tables = (pair_cum.join(drug_tot, ["rxcui", "quarter"]).join(event_tot, ["pt_lower", "quarter"])
              .join(n_total, "quarter")
              .select("rxcui", "pt_lower", "quarter",
                      F.col("a").cast("double").alias("a"),
                      (F.col("exposed") - F.col("a")).cast("double").alias("b"),
                      (F.col("events") - F.col("a")).cast("double").alias("c"),
                      (F.col("N") - F.col("exposed") - F.col("events") + F.col("a")).cast("double").alias("d")))
    tables.createOrReplaceTempView("tables")
    out = spark.sql(METRICS_SQL)
    for col in ("a", "b", "c", "d"):
        out = out.withColumn(col, F.col(col).cast("long"))
    return out, quarters


def compare(spark, written, other_path):
    """Pair-quarters present in only one result, and those whose cells, class or IC025 differ."""
    from pyspark.sql import functions as F
    other = spark.read.parquet(other_path)
    keys = ["rxcui", "pt_lower", "quarter"]
    left = written.select(*keys, *[F.col(c).alias("x_" + c) for c in ("a", "b", "c", "d", "signal_strength", "ic025")])
    right = other.select(*keys, *[F.col(c).alias("y_" + c) for c in ("a", "b", "c", "d", "signal_strength", "ic025")])
    j = left.join(right, keys, "full_outer")
    one_side = j.where(F.col("x_a").isNull() | F.col("y_a").isNull()).count()
    differ = j.where(F.col("x_a").isNotNull() & F.col("y_a").isNotNull() & (
        (F.col("x_a") != F.col("y_a")) | (F.col("x_b") != F.col("y_b")) | (F.col("x_c") != F.col("y_c"))
        | (F.col("x_d") != F.col("y_d")) | (F.col("x_signal_strength") != F.col("y_signal_strength"))
        | (F.abs(F.col("x_ic025") - F.col("y_ic025")) > 1e-9))).count()
    return {"compared_with": other_path, "rows_in_only_one": one_side, "rows_differing": differ}


def main(argv=None):
    ap = argparse.ArgumentParser(description="All-pairs disproportionality by quarter (Spark).")
    ap.add_argument("--lake", required=True, help="lake root: a local path or s3a://bucket/lake")
    ap.add_argument("--run-id", default=time.strftime("%Y%m%dT%H%M%S"))
    ap.add_argument("--data-version", default="")
    ap.add_argument("--manifest-out", help="also write the run manifest to this local file")
    ap.add_argument("--out-name", default="signals_by_quarter", help="results folder under <lake>/results")
    ap.add_argument("--compare-with", help="another results folder: report pair-quarters that differ")
    args = ap.parse_args(argv)

    from pyspark.sql import SparkSession
    spark = (SparkSession.builder.appName("pharmawatch-signals-by-quarter")
             .config("spark.sql.shuffle.partitions", "48")
             .config("spark.sql.session.timeZone", "UTC").getOrCreate())
    spark.sparkContext.setLogLevel("WARN")
    started = time.time()
    out, quarters = build(spark, args.lake)
    target = f"{args.lake}/results/{args.out_name}"
    # The caller empties the target first (bigdata.run_spark, through the storage layer). Appending
    # means the job never asks the store to delete a folder, which S3-compatible stores handle
    # differently (SeaweedFS answers 500 where AWS S3 succeeds).
    (out.repartition("quarter").sortWithinPartitions("rxcui", "pt_lower")
     .write.mode("append").partitionBy("quarter").parquet(target))
    written = spark.read.parquet(target)
    counts = {r["quarter"]: r["count"] for r in written.groupBy("quarter").count().collect()}
    classes = {r["signal_strength"]: r["count"] for r in
               written.where(f"quarter = '{quarters[-1]}'").groupBy("signal_strength").count().collect()}
    manifest = {
        "run_id": args.run_id, "engine": "spark", "spark_version": spark.version,
        "master": spark.sparkContext.master, "default_parallelism": spark.sparkContext.defaultParallelism,
        "quarters": quarters, "rows_by_quarter": counts, "rows": sum(counts.values()),
        "latest_quarter_classes": classes, "data_version": args.data_version,
        "min_cases": MIN_CASES, "seconds": round(time.time() - started, 1),
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "definition": "cumulative from the first loaded quarter; pairs kept from their first quarter with 3+ cases",
    }
    if args.compare_with:
        manifest["comparison"] = compare(spark, written, args.compare_with)
    text = json.dumps(manifest, indent=2)
    if args.manifest_out:
        with open(args.manifest_out, "w") as f:
            f.write(text)
    print("MANIFEST " + json.dumps(manifest))
    spark.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
