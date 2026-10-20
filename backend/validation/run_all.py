"""
One command to rerun every benchmark and regenerate the validation summary.

    python -m validation.run_all            # full: all data, full model comparison
    python -m validation.run_all --smoke    # small and fast; what CI runs

A component whose inputs are missing (no FAERS data, no ADE corpus, no deployed model) is skipped
with the reason printed and recorded under `not_run`; it is never filled in.
"""

import argparse
import os
import sys
import time
import traceback

from validation import report


def _signals(smoke):
    from faers.analytics import DB_PATH
    from validation import signals_benchmark
    out, path = signals_benchmark.run(DB_PATH, smoke=smoke)
    return path


def _ddi(smoke):
    from validation import ddi_deployed
    if not smoke:
        from validation import ddi_calibrate, ddi_eval
        ddi_eval.run_benchmark()
        sys.argv = [sys.argv[0]]                      # ddi_calibrate parses its own arguments
        ddi_calibrate.main()
    return ddi_deployed.run(smoke=smoke)[1]


def _ner(smoke):
    from validation import ner_benchmark
    if not __import__("os").path.exists(ner_benchmark.DEFAULT_DATA):
        raise FileNotFoundError(f"ADE corpus not found at {ner_benchmark.DEFAULT_DATA}")
    return ner_benchmark.run(ner_benchmark.DEFAULT_DATA, smoke=smoke)[1]


def _prescription(smoke):
    from validation import prescription_benchmark
    if not os.path.exists(os.path.join(prescription_benchmark.FIXTURES, "manifest.json")):
        raise FileNotFoundError("prescription test set not found")
    return prescription_benchmark.run("local", smoke=smoke)[1]


def _lead_time(smoke):
    from validation import lead_time
    return lead_time.run(smoke=smoke)[1]       # skips (FileNotFoundError) without label_changes.csv or the lake


STEPS = [("signals", _signals), ("ddi", _ddi), ("ner", _ner), ("prescription", _prescription),
         ("lead_time", _lead_time)]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--only", nargs="+", choices=[n for n, _ in STEPS])
    args = parser.parse_args(argv)

    failed = []
    for name, step in STEPS:
        if args.only and name not in args.only:
            continue
        started = time.time()
        try:
            path = step(args.smoke)
            print(f"[+] {name}: wrote {path} ({time.time() - started:.0f}s)", flush=True)
        except FileNotFoundError as exc:
            print(f"[-] {name}: skipped - {exc}", flush=True)
        except Exception:
            failed.append(name)
            print(f"[!] {name}: failed\n{traceback.format_exc()}", flush=True)

    summary = report.build_summary(smoke=args.smoke)
    print(f"[+] summary: components {sorted(summary['components'])}, not run {summary['not_run']}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
