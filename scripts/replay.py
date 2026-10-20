#!/usr/bin/env python3
"""
scripts/replay.py — replay the loaded FAERS quarters through the surveillance pipeline, in order.

A replay feeds each quarter's precomputed Spark results back through the SAME alert detector that
runs live, writing to an isolated store (replay_alerts) keyed by a run id. It changes no live data.
These are the alerts the system would have raised at the time.

    python scripts/replay.py                                   # replay every loaded quarter, through Kafka
    python scripts/replay.py --from 2024q1 --to 2024q3         # a sub-range
    python scripts/replay.py --seconds-per-quarter 10          # pace the Kafka events (demo)
    python scripts/replay.py --inline                          # no Kafka: run each quarter in-process

With Kafka (the default), one `replay-signals` event per quarter is published on the topic; the
consumer (python -m ingest.consumer) handles it with the alert detector. This is the real pipeline
path, isolated to the replay store. `--inline` drives the detector directly, for a laptop with no
Kafka up — the browser Replay tab uses this same in-process path through the web API.
"""

import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(ROOT, "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)


def main():
    ap = argparse.ArgumentParser(description="Replay loaded quarters through the surveillance pipeline.")
    ap.add_argument("--from", dest="from_q", default=None, help="first quarter (default: earliest loaded)")
    ap.add_argument("--to", dest="to_q", default=None, help="last quarter (default: latest loaded)")
    ap.add_argument("--seconds-per-quarter", type=float, default=5.0,
                    help="pause between quarters when publishing to Kafka (default 5)")
    ap.add_argument("--inline", action="store_true",
                    help="run each quarter in-process instead of publishing to Kafka")
    args = ap.parse_args()

    from services import surveillance as sv

    payload, status = sv.start_replay(from_quarter=args.from_q, to_quarter=args.to_q)
    if status != 200:
        print(f"Could not start replay: {payload.get('error')}", file=sys.stderr)
        return 1
    run_id, span = payload["run_id"], payload["quarters"]
    print(f"Replay {run_id}: {len(span)} quarters {span[0]}..{span[-1]} "
          f"({'inline' if args.inline else 'via Kafka'})")

    if args.inline:
        for q in span:
            result, _ = sv.replay_step(run_id, q)
            print(f"  {q}: {result.get('created', 0)} alerts ({result.get('by_kind')})")
        print(f"Done. View: /api/surveillance/replay/{run_id}?upto={span[-1]}")
        return 0

    # Kafka path: announce each quarter in order; the consumer runs the detector.
    from ingest.producer import create_producer, publish
    producer = create_producer()
    try:
        for i, q in enumerate(span):
            publish(producer, "replay-signals", run_id, {"run_id": run_id, "quarter": q})
            print(f"  published {q}")
            if i < len(span) - 1:
                time.sleep(args.seconds_per_quarter)
        producer.flush()
    finally:
        producer.close(timeout=5)
    print(f"Published {len(span)} quarters. The consumer writes the alerts; view: "
          f"/api/surveillance/replay/{run_id}?upto={span[-1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
