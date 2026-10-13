"""find_pair_row: the single TWOSIDES pair lookup used by the interaction services."""

import os
import sys

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from clients import hbase  # noqa: E402


class FakeTable:
    def __init__(self, rows):
        self.rows = rows
        self.asked = []

    def row(self, key):
        self.asked.append(key)
        return self.rows.get(key, {})


class FakeConn:
    def __init__(self, rows, close_raises=False):
        self.table_obj = FakeTable(rows)
        self.closed = False
        self.close_raises = close_raises

    def table(self, name):
        assert name == "interactions"
        return self.table_obj

    def close(self):
        self.closed = True
        if self.close_raises:
            raise RuntimeError("stale socket")


def test_finds_pair_in_stored_order(monkeypatch):
    conn = FakeConn({b"WARFARIN_ASPIRIN": {b"info:side_effect": b"bleeding"}})
    monkeypatch.setattr(hbase, "_hbase_connect", lambda: conn)
    row = hbase.find_pair_row("Warfarin", "Aspirin")
    assert row == {b"info:side_effect": b"bleeding"}
    assert conn.closed


def test_finds_pair_in_reverse_order(monkeypatch):
    conn = FakeConn({b"ASPIRIN_WARFARIN": {b"info:severity": b"high"}})
    monkeypatch.setattr(hbase, "_hbase_connect", lambda: conn)
    assert hbase.find_pair_row("Warfarin", "Aspirin") == {b"info:severity": b"high"}
    assert conn.table_obj.asked == [b"WARFARIN_ASPIRIN", b"ASPIRIN_WARFARIN"]


def test_returns_none_when_absent_and_still_closes(monkeypatch):
    conn = FakeConn({}, close_raises=True)
    monkeypatch.setattr(hbase, "_hbase_connect", lambda: conn)
    assert hbase.find_pair_row("A", "B") is None
    assert conn.closed


def test_returns_none_without_happybase(monkeypatch):
    monkeypatch.setattr(hbase, "_hbase_connect", lambda: None)
    assert hbase.find_pair_row("A", "B") is None
