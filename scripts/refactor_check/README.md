# Behaviour-preservation check for refactors

Runs every API endpoint against a fake network and a fake MySQL (DuckDB is read
from `data/pharmawatch_faers.duckdb`, read-only) and records each response body,
status and every SQL statement with its parameters. Run it before and after a
change that must not alter behaviour, then compare.

    venv\Scripts\python.exe scripts/refactor_check/diff_harness.py before.json
    # ...refactor...
    venv\Scripts\python.exe scripts/refactor_check/diff_harness.py after.json
    venv\Scripts\python.exe scripts/refactor_check/compare.py before.json after.json

Run from the repository root. No real network call and no database write is made.
Output is deterministic (verified over three runs). Use it for Phases 5 and 7;
for intended changes, expect and review the differences instead.
