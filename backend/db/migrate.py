#!/usr/bin/env python3
"""
migrate.py — PharmaWatch MySQL Database Migration Runner
Idempotently creates the MySQL schema, verifies tables, and optionally seeds clean legacy data.
"""

import os
import sys
import argparse
import logging
import sqlite3
import time
import pymysql
from pymysql.cursors import DictCursor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [MIGRATE] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger("migrate")

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCHEMA_FILE = os.path.join(os.path.dirname(__file__), "schema.sql")
LEGACY_SQLITE_PATH = os.path.join(BASE_DIR, "pharmawatch.db")

def parse_args():
    env_file = os.path.join(BASE_DIR, ".env")
    if os.path.exists(env_file):
        try:
            from dotenv import load_dotenv
            load_dotenv(env_file, override=False)
        except ImportError:
            pass

    parser = argparse.ArgumentParser(description="PharmaWatch MySQL Schema Migration Runner")
    parser.add_argument("--host", default=os.getenv("MYSQL_HOST", "127.0.0.1"), help="MySQL Host")
    parser.add_argument("--port", type=int, default=int(os.getenv("MYSQL_PORT", 3306)), help="MySQL Port")
    parser.add_argument("--user", default=os.getenv("MYSQL_USER", "root"), help="MySQL User")
    parser.add_argument("--password", default=os.getenv("MYSQL_PASSWORD", ""), help="MySQL Password")
    parser.add_argument("--database", default=os.getenv("MYSQL_DATABASE", "pharmawatch"), help="Target Database")
    parser.add_argument("--skip-seed", action="store_true", help="Skip legacy SQLite clean data seeding")
    return parser.parse_args()

def split_sql_statements(sql_text):
    """Splits SQL script into individual executable statements."""
    statements = []
    current = []
    for line in sql_text.splitlines():
        trimmed = line.strip()
        if not trimmed or trimmed.startswith("--") or trimmed.startswith("/*"):
            continue
        current.append(line)
        if trimmed.endswith(";"):
            statement = "\n".join(current).strip()
            if statement.endswith(";"):
                statement = statement[:-1]
            statements.append(statement)
            current = []
    if current:
        remaining = "\n".join(current).strip()
        if remaining:
            statements.append(remaining)
    return statements

def run_migrations(args):
    log.info(f"Connecting to MySQL server at {args.host}:{args.port} as '{args.user}'...")
    server_conn = None
    max_retries = 5
    for attempt in range(1, max_retries + 1):
        try:
            server_conn = pymysql.connect(
                host=args.host,
                port=args.port,
                user=args.user,
                password=args.password,
                charset="utf8mb4",
                cursorclass=DictCursor,
                connect_timeout=10,
            )
            break
        except Exception as e:
            if attempt < max_retries:
                log.warning(f"Connection attempt {attempt}/{max_retries} failed ({e}). Retrying in 3s...")
                time.sleep(3)
            else:
                log.error(f"Failed to connect to MySQL server after {max_retries} attempts: {e}")
                return False

    with server_conn:
        with server_conn.cursor() as cur:
            log.info(f"Ensuring database '{args.database}' exists...")
            cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{args.database}` "
                f"CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
            )
        server_conn.commit()

    log.info(f"Connecting to database '{args.database}'...")
    app_conn = pymysql.connect(
        host=args.host,
        port=args.port,
        user=args.user,
        password=args.password,
        database=args.database,
        charset="utf8mb4",
        cursorclass=DictCursor,
        connect_timeout=10,
    )

    with app_conn:
        if not os.path.exists(SCHEMA_FILE):
            log.error(f"Schema file not found at: {SCHEMA_FILE}")
            return False

        with open(SCHEMA_FILE, "r", encoding="utf-8") as f:
            schema_sql = f.read()

        statements = split_sql_statements(schema_sql)
        log.info(f"Found {len(statements)} DDL statements to execute.")

        with app_conn.cursor() as cur:
            for idx, stmt in enumerate(statements, start=1):
                first_line = stmt.split("\n")[0][:60]
                try:
                    cur.execute(stmt)
                    log.info(f"[{idx}/{len(statements)}] Executed: {first_line}...")
                except Exception as e:
                    log.error(f"Error executing statement [{idx}]: {e}\nSQL:\n{stmt}")
                    return False
        app_conn.commit()

        # Verify created tables
        with app_conn.cursor() as cur:
            cur.execute("SHOW TABLES;")
            tables = [list(r.values())[0] for r in cur.fetchall()]
            log.info(f"Successfully verified {len(tables)} tables in '{args.database}':")
            for t in sorted(tables):
                log.info(f"  • {t}")

        # Optional clean seed from legacy SQLite
        if not args.skip_seed and os.path.exists(LEGACY_SQLITE_PATH):
            seed_clean_legacy_data(app_conn, LEGACY_SQLITE_PATH)

    log.info("Migration completed successfully!")
    return True

def seed_clean_legacy_data(mysql_conn, sqlite_path):
    """Safely copies uncorrupted legacy tables (violations, interaction predictions) to MySQL."""
    log.info(f"Checking for clean legacy seed data in {sqlite_path}...")
    try:
        sq_conn = sqlite3.connect(sqlite_path)
        sq_conn.row_factory = sqlite3.Row
    except Exception as e:
        log.warning(f"Unable to read legacy SQLite file ({e}); skipping seed.")
        return

    try:
        # 1. Boxed Warning Violations
        rows = sq_conn.execute("SELECT * FROM boxed_warning_violations").fetchall()
        if rows:
            with mysql_conn.cursor() as cur:
                inserted = 0
                for r in rows:
                    sql = """
                        INSERT INTO boxed_warning_violations 
                            (drug, adverse_event, faers_count_post_warning, faers_count_pre_warning, 
                             percentage_of_total, warning_effective_date, warning_text_snippet, detection_method)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE
                            faers_count_post_warning = VALUES(faers_count_post_warning),
                            faers_count_pre_warning = VALUES(faers_count_pre_warning),
                            percentage_of_total = VALUES(percentage_of_total),
                            warning_effective_date = VALUES(warning_effective_date),
                            warning_text_snippet = VALUES(warning_text_snippet),
                            detection_method = VALUES(detection_method)
                    """
                    cur.execute(sql, (
                        r["drug"], r["adverse_event"], r["faers_count_post_warning"],
                        r["faers_count_pre_warning"], r["percentage_of_total"],
                        r["warning_effective_date"], r["warning_text_snippet"],
                        r["detection_method"]
                    ))
                    inserted += 1
                mysql_conn.commit()
                log.info(f"Seeded {inserted} rows into 'boxed_warning_violations'.")
    except Exception as e:
        log.warning(f"Could not seed boxed_warning_violations: {e}")

    try:
        # 2. Interaction Predictions
        rows = sq_conn.execute("SELECT * FROM interaction_predictions").fetchall()
        if rows:
            with mysql_conn.cursor() as cur:
                inserted = 0
                for r in rows:
                    sql = """
                        INSERT INTO interaction_predictions
                            (drug_a, drug_b, smiles_a, smiles_b, cid_a, cid_b,
                             is_harmful, confidence, top_side_effect, all_side_effects, source)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE
                            is_harmful = VALUES(is_harmful),
                            confidence = VALUES(confidence),
                            top_side_effect = VALUES(top_side_effect),
                            all_side_effects = VALUES(all_side_effects),
                            source = VALUES(source)
                    """
                    cur.execute(sql, (
                        r["drug_a"], r["drug_b"], r["smiles_a"], r["smiles_b"],
                        r["cid_a"], r["cid_b"], r["is_harmful"], r["confidence"],
                        r["top_side_effect"], r["all_side_effects"], r["source"]
                    ))
                    inserted += 1
                mysql_conn.commit()
                log.info(f"Seeded {inserted} rows into 'interaction_predictions'.")
    except Exception as e:
        log.warning(f"Could not seed interaction_predictions: {e}")

    sq_conn.close()

if __name__ == "__main__":
    args = parse_args()
    success = run_migrations(args)
    sys.exit(0 if success else 1)
