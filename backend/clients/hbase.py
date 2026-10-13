# backend/clients/hbase.py
# HBase (Thrift) connection with a circuit breaker for the TWOSIDES interaction table.

import os
import time


# ── HBase Circuit Breaker Cache ──────────────────────────────────────────────
_hbase_last_check = 0.0
_hbase_online_cached = True


def _hbase_connect():
    """
    Return a live happybase Connection.

    Connection strategy
    -------------------
    * The Thrift daemon inside the HBase container silently drops idle TCP
      sockets after ~60 s, so the first call after a quiet period raises
      ``TTransportException: TSocket read 0 bytes``.  That is NOT a sign
      the container is down — it just means the socket is stale.
    * We therefore allow one silent retry with a brand-new Connection before
      surfacing the error.  A genuine refusal (container stopped / port closed)
      trips the 30-second circuit breaker and fast-fails immediately.
    * Returns None only when happybase is not installed.
    """
    global _hbase_last_check, _hbase_online_cached
    try:
        import happybase
    except ImportError:
        return None  # happybase not installed

    try:
        from thrift.transport.TTransport import TTransportException
    except ImportError:
        try:
            from thriftpy2.transport.base import TTransportException
        except ImportError:
            TTransportException = Exception

    now = time.time()
    if not _hbase_online_cached and (now - _hbase_last_check < 30):
        raise RuntimeError("HBase is offline (circuit breaker active — wait 30 s)")

    def _open_fresh():
        """Open a brand-new Thrift connection with a short timeout."""
        host = os.getenv("HBASE_HOST", "127.0.0.1")
        port = int(os.getenv("HBASE_PORT", 9090))
        c = happybase.Connection(host, port=port, timeout=3000)
        c.open()
        return c

    # First attempt
    try:
        conn = _open_fresh()
        _hbase_online_cached = True
        _hbase_last_check = now
        return conn
    except TTransportException:
        # Stale socket — retry once with a fresh connection
        pass
    except Exception as e:
        # Genuine failure (port closed, container down, …)
        _hbase_online_cached = False
        _hbase_last_check = now
        raise

    # Second attempt (after stale-socket failure)
    try:
        conn = _open_fresh()
        _hbase_online_cached = True
        _hbase_last_check = now
        return conn
    except Exception as e:
        _hbase_online_cached = False
        _hbase_last_check = now
        raise


def find_pair_row(name_a, name_b):
    """Return the TWOSIDES interaction row for a drug pair (either order), or None.

    Returns None when happybase is not installed; connection errors propagate.
    """
    conn = _hbase_connect()
    if not conn:
        return None
    try:
        table = conn.table('interactions')
        for key in (f"{name_a.upper()}_{name_b.upper()}".encode(),
                    f"{name_b.upper()}_{name_a.upper()}".encode()):
            row = table.row(key)
            if row:
                return row
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass
