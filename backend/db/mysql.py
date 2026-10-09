"""
mysql.py — Production MySQL Connection Pooling & Database Adapter for PharmaWatch
Provides thread-safe connection pooling, DictCursor results, and Decimal-safe type casting.
"""

import os
import logging
from decimal import Decimal
from contextlib import contextmanager
import pymysql
from pymysql.cursors import DictCursor
from dbutils.pooled_db import PooledDB

from dotenv import load_dotenv

log = logging.getLogger("pharmawatch.db")

_POOL = None

def get_db_config(load_env=True):
    """Retrieve MySQL credentials from environment variables."""
    if load_env:
        env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
        if os.path.exists(env_path):
            load_dotenv(env_path, override=False)
        else:
            load_dotenv(override=False)

    return {
        "host": os.getenv("MYSQL_HOST", "localhost"),
        "port": int(os.getenv("MYSQL_PORT", 3306)),
        "user": os.getenv("MYSQL_USER", "root"),
        "password": os.getenv("MYSQL_PASSWORD", ""),
        "database": os.getenv("MYSQL_DATABASE", "pharmawatch"),
        "charset": "utf8mb4",
        "cursorclass": DictCursor,
        "connect_timeout": 10,
        "read_timeout": 30,
        "write_timeout": 30,
    }

def get_pool():
    """Initializes or returns the thread-safe connection pool."""
    global _POOL
    if _POOL is None:
        cfg = get_db_config()
        log.info(f"Initializing MySQL connection pool for {cfg['user']}@{cfg['host']}:{cfg['port']}/{cfg['database']}")
        _POOL = PooledDB(
            creator=pymysql,
            maxconnections=int(os.getenv("MYSQL_MAX_CONNECTIONS", 20)),
            mincached=int(os.getenv("MYSQL_MIN_CACHED", 2)),
            maxcached=int(os.getenv("MYSQL_MAX_CACHED", 10)),
            blocking=True,
            ping=7,  # Ping on borrow to ensure live connection (auto-reconnect)
            **cfg
        )
    return _POOL

def reset_pool():
    """Reset the pool instance (useful during testing or reconfiguration)."""
    global _POOL
    if _POOL is not None:
        try:
            _POOL.close()
        except Exception:
            pass
        _POOL = None

def clean_row(row):
    """
    Recursively converts MySQL Decimal instances to int or float.
    Prevents TypeError: unsupported operand type for +: 'Decimal' and 'float'
    and ensures clean JSON serialization.
    """
    if row is None:
        return None
    if isinstance(row, dict):
        return {k: clean_row(v) for k, v in row.items()}
    if isinstance(row, (list, tuple)):
        return [clean_row(item) for item in row]
    if isinstance(row, Decimal):
        return int(row) if row % 1 == 0 else float(row)
    if isinstance(row, (bytes, bytearray)):
        return row.decode("utf-8", errors="replace")
    return row

@contextmanager
def get_db():
    """
    Context manager yielding a pooled connection with automatic transaction commit/rollback.
    Usage:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM signals_cache WHERE drug = %s", (drug,))
                rows = cur.fetchall()
    """
    pool = get_pool()
    conn = pool.connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def query(sql, params=None):
    """
    Execute a SELECT query and return a list of dictionaries with clean data types.
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            rows = cur.fetchall()
            return [clean_row(r) for r in rows]

def query_one(sql, params=None):
    """
    Execute a SELECT query and return a single dictionary or None.
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            row = cur.fetchone()
            return clean_row(row)

def execute(sql, params=None):
    """
    Execute an INSERT, UPDATE, or DELETE statement.
    Returns: lastrowid if non-zero, else rowcount.
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            return cur.lastrowid if cur.lastrowid else cur.rowcount

def execute_many(sql, seq_of_params):
    """
    Execute a batch of statements with parameter tuples.
    Returns: affected rowcount.
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            return cur.executemany(sql, seq_of_params)

def ping():
    """Health check helper verifying database connectivity and listing tables."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT DATABASE() as db, VERSION() as version, NOW() as server_time")
            meta = cur.fetchone()
            cur.execute("SHOW TABLES")
            tables = [list(r.values())[0] for r in cur.fetchall()]
            return {
                "status": "healthy",
                "database": meta.get("db"),
                "version": meta.get("version"),
                "server_time": str(meta.get("server_time")),
                "table_count": len(tables),
                "tables": tables,
            }
