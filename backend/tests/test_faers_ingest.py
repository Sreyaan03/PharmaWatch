"""
FAERS ingest: real-file guards, case de-duplication, deletions, age normalisation, child-table
filtering, the quarter registry and the downloader's resume and verification logic.

All data comes from the generated fixture in tests/fixtures/faers/make_fixture.py.
"""

import hashlib
import os
import sys
import zipfile

import duckdb
import pytest
import requests

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
for p in (BASE_DIR, os.path.join(os.path.dirname(__file__), "fixtures", "faers")):
    if p not in sys.path:
        sys.path.insert(0, p)

import make_fixture  # noqa: E402
from faers import downloader, loader, parser, registry  # noqa: E402
from faers.dedupe import age_years_expr  # noqa: E402


# ── fixtures ──────────────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """Fixture zips parsed to Parquet and loaded into a DuckDB file."""
    root = tmp_path_factory.mktemp("faers")
    z1, z2 = make_fixture.two_quarter_fixture(str(root / "raw"))
    out = str(root / "parquet")
    manifests = [parser.parse_quarter(z, out, allow_fixture=True, log=lambda *_: None) for z in (z1, z2)]
    db = str(root / "faers.duckdb")
    summary = loader.load_database(out, db, log=lambda *_: None)
    con = duckdb.connect(db, read_only=True)
    yield {"con": con, "db": db, "manifests": manifests, "summary": summary, "out": out, "raw": str(root / "raw")}
    con.close()


# ── parser guards ─────────────────────────────────────────────────────────────
def test_small_files_are_refused_unless_marked_as_fixture(tmp_path):
    z1, _ = make_fixture.two_quarter_fixture(str(tmp_path / "raw"))
    with pytest.raises(parser.NotARealQuarter, match="sample or mock"):
        parser.parse_quarter(z1, str(tmp_path / "out"), log=lambda *_: None)
    assert not os.path.exists(tmp_path / "out" / "demo_2024q1.parquet")


def test_zip_without_required_members_is_refused(tmp_path):
    bad = tmp_path / "faers_ascii_2024q3.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("ASCII/DEMO24Q3.txt", "primaryid$caseid\n1$1\n")
    with pytest.raises(parser.NotARealQuarter, match="missing"):
        parser.parse_quarter(str(bad), str(tmp_path / "out"), allow_fixture=True, log=lambda *_: None)


def test_malformed_rows_are_counted_not_lost_silently(built):
    q1 = next(m for m in built["manifests"] if m["quarter"] == "2024q1")
    assert q1["malformed_rows"] == {"demo": 1}
    assert q1["tables"]["demo"] == 4          # the malformed line is not among them


def test_deleted_case_ids_are_read_past_the_blank_first_line(built):
    q2 = next(m for m in built["manifests"] if m["quarter"] == "2024q2")
    assert q2["deleted_cases"] == 1


# ── de-duplication ────────────────────────────────────────────────────────────
def test_each_case_appears_once_at_its_latest_version(built):
    con = built["con"]
    rows = con.execute("SELECT caseid, count(*) FROM faers_demo GROUP BY caseid HAVING count(*) > 1").fetchall()
    assert rows == []
    latest = con.execute("SELECT primaryid, caseversion, quarter FROM faers_demo WHERE caseid = '1001'").fetchall()
    assert latest == [("10012", 2, "2024q2")]


def test_deleted_cases_are_removed_in_every_version(built):
    assert built["con"].execute("SELECT count(*) FROM faers_demo WHERE caseid = '1004'").fetchone()[0] == 0
    assert built["con"].execute("SELECT count(*) FROM faers_drug WHERE caseid = '1004'").fetchone()[0] == 0


def test_child_rows_belong_to_the_surviving_version_only(built):
    con = built["con"]
    # v1 of case 1001 had NAUSEA and ASPIRIN; v2 has HAEMORRHAGE and no aspirin
    reacs = {r[0] for r in con.execute("SELECT pt FROM faers_reac WHERE primaryid IN ('10011','10012')").fetchall()}
    assert reacs == {"Haemorrhage"}
    assert con.execute("SELECT count(*) FROM faers_drug WHERE primaryid = '10011'").fetchone()[0] == 0
    # outcomes and therapy of the superseded version are gone too
    assert con.execute("SELECT count(*) FROM faers_outc WHERE primaryid = '10011'").fetchone()[0] == 0


def test_therapy_and_outcome_rows_are_loaded(built):
    con = built["con"]
    assert con.execute("SELECT start_dt FROM faers_ther WHERE primaryid = '10061'").fetchone()[0] == "20231202"
    assert con.execute("SELECT outc_cod FROM faers_outc WHERE primaryid = '10012'").fetchone()[0] == "HO"


def test_total_cases_kept(built):
    # 1001(v2) 1002 1003 1005 1006 1007  (1004 deleted, malformed 1099 skipped)
    assert built["summary"]["cases_kept"] == 6
    assert built["summary"]["reports_in_files"] == 8       # 4 + 4 rows actually parsed


# ── age normalisation ─────────────────────────────────────────────────────────
def test_ages_are_converted_to_years_from_the_reported_unit(built):
    ages = dict(built["con"].execute("SELECT caseid, age_years FROM faers_demo").fetchall())
    assert ages["1001"] == 66
    assert ages["1002"] == pytest.approx(0.5)      # 6 months
    assert ages["1003"] == 70                      # 7 decades
    assert ages["1005"] is None                    # 150 years is out of range
    assert ages["1007"] is None                    # age without a unit is not assumed to be years


@pytest.mark.parametrize("age,cod,expected", [
    ("36", "HR", 36 / (24 * 365.25)),
    ("2", "WK", 14 / 365.25),
    ("30", "DY", 30 / 365.25),
    ("10", "yr", 10),                 # unit codes are case-insensitive
    ("abc", "YR", None),
    ("", "YR", None),
    ("-5", "YR", None),
])
def test_age_expression_edge_cases(age, cod, expected):
    value = duckdb.connect().execute(
        f"SELECT {age_years_expr('a', 'c')} FROM (SELECT ? AS a, ? AS c)", [age, cod]).fetchone()[0]
    if expected is None:
        assert value is None
    else:
        assert value == pytest.approx(expected)


# ── registry ──────────────────────────────────────────────────────────────────
def test_registry_lists_every_loaded_quarter(built):
    quarters = registry.list_quarters(built["db"])
    assert [q["quarter"] for q in quarters] == ["2024q1", "2024q2"]
    assert all(len(q["zip_sha256"]) == 64 for q in quarters)
    assert sum(q["cases_kept"] for q in quarters) == 6
    assert quarters[0]["reports_in_file"] == 4 and quarters[0]["is_fixture"] is True


def test_data_version_string(built):
    text = registry.data_version(built["db"])
    assert text == "FAERS 2024Q1-2024Q2 (2 quarters, 6 cases) [test fixture]"


def test_data_version_when_nothing_is_loaded(tmp_path):
    assert registry.data_version(str(tmp_path / "missing.duckdb")) == "FAERS data not loaded"


def test_loader_swaps_database_in_atomically(built, tmp_path):
    db = tmp_path / "live.duckdb"
    db.write_bytes(b"previous database")
    loader.load_database(built["out"], str(db), log=lambda *_: None)
    assert not os.path.exists(str(db) + ".building")
    assert registry.list_quarters(str(db))


def test_loader_refuses_when_nothing_was_parsed(tmp_path):
    with pytest.raises(FileNotFoundError, match="No parsed quarters"):
        loader.load_database(str(tmp_path), str(tmp_path / "x.duckdb"), log=lambda *_: None)


# ── downloader: pure logic and resume ─────────────────────────────────────────
def test_quarter_labels_round_trip():
    assert downloader.quarter_label(2024, 3) == "2024q3"
    assert downloader.parse_label("2024Q3") == (2024, 3)
    with pytest.raises(ValueError):
        downloader.parse_label("2024q5")
    with pytest.raises(ValueError):
        downloader.quarter_label(2024, 0)


def test_latest_released_skips_unreleased_quarters_and_returns_oldest_first():
    import datetime
    released = {(y, q) for y in range(2019, 2027) for q in (1, 2, 3, 4)} - {(2026, 3), (2026, 4)}
    got = downloader.latest_released(20, today=datetime.date(2026, 10, 13),
                                     probe=lambda y, q: 1 if (y, q) in released else None)
    assert got[0] == (2021, 3) and got[-1] == (2026, 2) and len(got) == 20
    assert got == sorted(got)


def test_latest_released_gives_up_when_nothing_is_published():
    import datetime
    with pytest.raises(RuntimeError, match="No released FAERS quarter"):
        downloader.latest_released(3, today=datetime.date(2026, 10, 13), probe=lambda y, q: None)


def test_is_verified_requires_matching_checksum(tmp_path):
    f = tmp_path / "faers_ascii_2024q1.zip"
    f.write_bytes(b"PK data")
    assert not downloader.is_verified(str(f))              # no sidecar
    sidecar = downloader.sidecar_path(str(f))
    with open(sidecar, "w") as s:
        s.write(hashlib.sha256(b"PK data").hexdigest() + "  x\n")
    assert downloader.is_verified(str(f))
    f.write_bytes(b"PK tampered")
    assert not downloader.is_verified(str(f))


class _FakeResponse:
    def __init__(self, status, headers, chunks, fail_after=None):
        self.status_code = status
        self.headers = headers
        self._chunks = chunks
        self._fail_after = fail_after

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def iter_content(self, chunk_size=0):
        for i, c in enumerate(self._chunks):
            if self._fail_after is not None and i >= self._fail_after:
                raise requests.ConnectionError("connection reset")
            yield c

    def close(self):
        pass


class _FakeSession:
    """Serves one zip; the first transfer dies half-way, later ones honour Range."""

    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get(self, url, headers=None, stream=True, timeout=None, allow_redirects=True):
        headers = headers or {}
        self.calls.append(dict(headers))
        start = 0
        if "Range" in headers:
            start = int(headers["Range"].split("=")[1].rstrip("-"))
        body = self.payload[start:]
        pieces = [body[i:i + 1000] for i in range(0, len(body), 1000)]
        if start == 0 and len(self.calls) == 1:
            return _FakeResponse(200, {"Content-Length": str(len(self.payload))}, pieces, fail_after=len(pieces) // 2)
        if start:
            return _FakeResponse(206, {"Content-Range": f"bytes {start}-{len(self.payload)-1}/{len(self.payload)}"}, pieces)
        return _FakeResponse(200, {"Content-Length": str(len(self.payload))}, pieces)


def test_interrupted_download_resumes_and_verifies(tmp_path, monkeypatch):
    monkeypatch.setattr(downloader, "_backoff", lambda attempt: None)
    z1, _ = make_fixture.two_quarter_fixture(str(tmp_path / "src"))
    payload = open(z1, "rb").read()
    session = _FakeSession(payload)

    dest = downloader.download_quarter(2024, 1, directory=str(tmp_path / "raw"), session=session, log=lambda *_: None)

    assert open(dest, "rb").read() == payload
    assert downloader.is_verified(dest)
    assert not os.path.exists(dest + ".part")
    assert any("Range" in c for c in session.calls), "second request should resume with a Range header"

    # a second run makes no request at all
    session.calls.clear()
    downloader.download_quarter(2024, 1, directory=str(tmp_path / "raw"), session=session, log=lambda *_: None)
    assert session.calls == []


# ── adopting hand-downloaded zips ─────────────────────────────────────────────
def test_adopt_registers_valid_zips_and_rejects_bad_ones(tmp_path):
    z1, _ = make_fixture.two_quarter_fixture(str(tmp_path))
    (tmp_path / "faers_ascii_2024q3.zip").write_bytes(b"this is not a zip")
    with zipfile.ZipFile(tmp_path / "faers_ascii_2024q4.zip", "w") as zf:      # a zip without the tables
        zf.writestr("ASCII/DEMO24Q4.txt", "primaryid\n1\n")
    adopted, rejected = downloader.adopt_existing(str(tmp_path), log=lambda *_: None)

    assert sorted(adopted) == ["2024q1", "2024q2"]
    assert {r[0] for r in rejected} == {"2024q3", "2024q4"}
    assert downloader.is_verified(z1)
    assert not os.path.exists(str(tmp_path / "faers_ascii_2024q3.zip.sha256"))
    # a second run adopts nothing new and does not re-hash the verified files
    assert downloader.adopt_existing(str(tmp_path), log=lambda *_: None)[0] == []
