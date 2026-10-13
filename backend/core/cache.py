# backend/core/cache.py
# JSON payload cache keyed by (drug, event, kind) in a MySQL table.

import json
import logging
from datetime import datetime, timedelta

from db.mysql import query_one, execute

log = logging.getLogger(__name__)


class PayloadCache:
    """Cache computed payloads in `table`, expiring after `max_age` (24 hours by default)."""

    def __init__(self, table, kind_column, label, max_age=timedelta(hours=24)):
        self.table = table
        self.kind_column = kind_column
        self.label = label
        self.max_age = max_age

    def get(self, drug, event, kind, version=None):
        """Return the cached payload if present, fresh and of the same version, else None.

        With `version` given, an entry stored without that exact version is a miss, so a payload
        computed from other data (or by an older algorithm) is never served.
        """
        try:
            sql = (f"SELECT payload, cached_at FROM {self.table} "
                   f"WHERE drug = %s AND event = %s AND {self.kind_column} = %s LIMIT 1")
            row = query_one(sql, (drug.lower(), event.lower(), kind))
            if row and row.get("payload"):
                cached_at = row["cached_at"]
                if isinstance(cached_at, str):
                    try:
                        cached_at = datetime.fromisoformat(cached_at.rstrip("Z"))
                    except ValueError:
                        cached_at = datetime.strptime(cached_at[:19], "%Y-%m-%d %H:%M:%S")
                if datetime.now() - cached_at < self.max_age:
                    payload = json.loads(row["payload"])
                    if version is not None:
                        if not isinstance(payload, dict) or payload.get("_version") != version:
                            return None
                        payload.pop("_version", None)
                    return payload
        except Exception as e:
            log.error(f"[{self.label} CACHE ERROR] {e}")
        return None

    def set(self, drug, event, kind, payload, version=None):
        """Store a payload, replacing any previous one for the same key."""
        try:
            if version is not None and isinstance(payload, dict):
                payload = dict(payload, _version=version)
            sql = (f"INSERT INTO {self.table} (drug, event, {self.kind_column}, payload, cached_at) "
                   f"VALUES (%s, %s, %s, %s, NOW()) "
                   f"ON DUPLICATE KEY UPDATE payload = VALUES(payload), cached_at = NOW()")
            execute(sql, (drug.lower(), event.lower(), kind, json.dumps(payload)))
        except Exception as e:
            log.error(f"[{self.label} CACHE SAVE ERROR] {e}")
