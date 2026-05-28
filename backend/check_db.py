# ─────────────────────────────────────────────────────────────────
# check_db.py — Quick database inspection tool
# 
# HOW TO USE:
#   cd backend
#   python check_db.py
#
# This will show you:
#   - Which tables exist in pharmawatch.db
#   - How many drug-event pairs have been collected
#   - How many Reddit posts have been scraped
#   - A sample of stored records
#   - Breakdown by source (biobert / reddit / manual)
#
# Run this anytime you want to see what's in your database.
# ─────────────────────────────────────────────────────────────────

import sqlite3
conn = sqlite3.connect('pharmawatch.db')

print('=== TABLES ===')
tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
print([t[0] for t in tables])

print('\n=== drug_events count ===')
print(conn.execute('SELECT COUNT(*) FROM drug_events').fetchone()[0])

print('\n=== reddit_posts count ===')
print(conn.execute('SELECT COUNT(*) FROM reddit_posts').fetchone()[0])

print('\n=== Sample drug_events ===')
rows = conn.execute('SELECT drug, event, source, confidence FROM drug_events LIMIT 5').fetchall()
for r in rows:
    print(r)

print('\n=== Sources breakdown ===')
sources = conn.execute('SELECT source, COUNT(*) as c FROM drug_events GROUP BY source').fetchall()
for s in sources:
    print(f"  {s[0]}: {s[1]} records")

conn.close()
