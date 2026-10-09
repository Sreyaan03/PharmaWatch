#!/usr/bin/env python3
"""
fix_prr_stale.py  —  One-shot cleanup for inflated PRR values.

Run once after restarting the consumer:
    venv\Scripts\python.exe scripts\fix_prr_stale.py
"""
import sqlite3, os

DB = os.path.join(os.path.dirname(__file__), "..", "backend", "pharmawatch.db")
conn = sqlite3.connect(DB, timeout=15)
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("PRAGMA busy_timeout=15000")

print("=== PRR Stale Data Cleanup ===\n")

sc_total  = conn.execute("SELECT COUNT(*) FROM signals_cache").fetchone()[0]
sc_bad    = conn.execute("SELECT COUNT(*) FROM signals_cache WHERE prr > 100 AND source='kafka_faers'").fetchone()[0]
de_total  = conn.execute("SELECT COUNT(*) FROM drug_events").fetchone()[0]
de_unique = conn.execute("SELECT COUNT(*) FROM (SELECT DISTINCT drug, event FROM drug_events)").fetchone()[0]

print(f"signals_cache : {sc_total} rows total, {sc_bad} with inflated PRR > 100")
print(f"drug_events   : {de_total} rows total, {de_unique} unique pairs, {de_total - de_unique} duplicates\n")

# 1. Delete inflated signals
conn.execute("DELETE FROM signals_cache WHERE prr > 100 AND source='kafka_faers'")
deleted_sc = conn.total_changes
print(f"[1] Deleted {deleted_sc} inflated signals_cache rows")

# 2. Deduplicate drug_events
conn.execute("DELETE FROM drug_events WHERE rowid NOT IN (SELECT MIN(rowid) FROM drug_events GROUP BY drug, event)")
deleted_de = conn.total_changes - deleted_sc
print(f"[2] Removed {deleted_de} duplicate drug_events rows")

conn.commit()

sc_after = conn.execute("SELECT COUNT(*) FROM signals_cache").fetchone()[0]
de_after = conn.execute("SELECT COUNT(*) FROM drug_events").fetchone()[0]
bad_left = conn.execute("SELECT COUNT(*) FROM signals_cache WHERE prr > 100").fetchone()[0]

print(f"\n=== After ===")
print(f"signals_cache  : {sc_after} rows  (-{sc_total - sc_after})")
print(f"drug_events    : {de_after} rows  (-{de_total - de_after})")
print(f"PRR > 100 left : {bad_left}")
print("\nDone. Consumer will now rebuild clean signals from real FAERS counts.")
conn.close()
