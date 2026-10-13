# backend/faers/parser.py
# Converts one FAERS quarterly ASCII zip into Parquet files, streaming each member so memory
# stays bounded (a real DRUG file is about 150 MB of text).
#
#   python -m faers.parser --quarters 2026q2        # one or more quarters from <DATA_DIR>/faers_raw
#   python -m faers.parser --all                    # every verified zip found there
#
# Output, in <DATA_DIR>/faers_parquet:  demo_2026q2.parquet, drug_..., reac_..., ther_...,
# outc_..., dele_...  and  manifest_2026q2.json  (row counts, checksum, malformed-row count).

import argparse
import datetime
import json
import os
import re
import sys
import zipfile

import pyarrow as pa
import pyarrow.csv as pacsv
import pyarrow.parquet as pq

from core.config import data_dir

# Columns kept per table. Missing columns in older files are written as nulls.
TABLES = {
    "demo": ["primaryid", "caseid", "caseversion", "i_f_code", "event_dt", "fda_dt",
             "age", "age_cod", "sex", "reporter_country", "occr_country"],
    "drug": ["primaryid", "caseid", "drug_seq", "role_cod", "drugname", "prod_ai"],
    "reac": ["primaryid", "pt"],
    "ther": ["primaryid", "dsg_drug_seq", "start_dt", "end_dt"],
    "outc": ["primaryid", "outc_cod"],
}
REQUIRED = ("demo", "drug", "reac", "ther", "outc")

# A real quarter has hundreds of thousands of reports; anything far smaller is a sample or a mock.
MIN_REAL_DEMO_ROWS = 50_000


class NotARealQuarter(ValueError):
    """The zip is missing members or is implausibly small for a FAERS quarter."""


def parquet_dir():
    path = os.path.join(data_dir(), "faers_parquet")
    os.makedirs(path, exist_ok=True)
    return path


def _label_from_zip(zip_path):
    m = re.search(r"(\d{4})q([1-4])", os.path.basename(zip_path), re.IGNORECASE)
    if not m:
        raise ValueError(f"Cannot read the quarter from the file name: {zip_path}")
    return f"{m.group(1)}q{m.group(2)}"


def _find_members(zf):
    """Map table name -> zip member, matching on the file's base name (case-insensitive)."""
    found = {}
    for name in zf.namelist():
        base = os.path.basename(name).upper()
        if not base.endswith(".TXT") or base.endswith("/"):
            continue
        for table in TABLES:
            if base.startswith(table.upper()):
                found[table] = name
        if base.startswith("DELE"):
            found["dele"] = name
    return found


def _stream_table(zf, member, columns, writer_path, quarter_label):
    """Stream one delimited member into a Parquet file. Returns (rows, malformed_rows)."""
    bad = {"n": 0}

    def on_invalid(_row):
        bad["n"] += 1
        return "skip"

    with zf.open(member) as raw:
        reader = pacsv.open_csv(
            raw,
            read_options=pacsv.ReadOptions(encoding="latin1", block_size=8 * 1024 * 1024),
            parse_options=pacsv.ParseOptions(delimiter="$", quote_char=False, invalid_row_handler=on_invalid),
            convert_options=pacsv.ConvertOptions(
                column_types={c: pa.string() for c in columns},
                include_columns=columns,
                include_missing_columns=True,
                strings_can_be_null=False,
            ),
        )
        schema = pa.schema([(c, pa.string()) for c in columns] + [("quarter", pa.string())])
        rows = 0
        with pq.ParquetWriter(writer_path, schema, compression="snappy") as writer:
            for batch in reader:
                n = batch.num_rows
                table = pa.Table.from_batches([batch]).append_column("quarter", pa.array([quarter_label] * n, pa.string()))
                writer.write_table(table.cast(schema))
                rows += n
    return rows, bad["n"]


def _read_deleted_cases(zf, member):
    """The DELETE file lists case ids, one per line, preceded by a blank line."""
    ids = []
    with zf.open(member) as f:
        for line in f.read().decode("latin1").splitlines():
            token = line.strip()
            if token.isdigit():
                ids.append(token)
    return ids


def parse_quarter(zip_path, out_dir=None, allow_fixture=False, log=print):
    """Parse one quarter zip into Parquet and write its manifest. Returns the manifest dict."""
    out_dir = out_dir or parquet_dir()
    os.makedirs(out_dir, exist_ok=True)
    label = _label_from_zip(zip_path)

    with zipfile.ZipFile(zip_path) as zf:
        members = _find_members(zf)
        missing = [t for t in REQUIRED if t not in members]
        if missing:
            raise NotARealQuarter(f"{label}: missing {missing} in {os.path.basename(zip_path)}; found {sorted(members)}")

        manifest = {"quarter": label, "zip": os.path.basename(zip_path), "tables": {}, "malformed_rows": {}}
        for table, columns in TABLES.items():
            path = os.path.join(out_dir, f"{table}_{label}.parquet")
            log(f"[*] {label}: {table} <- {members[table]}")
            rows, bad = _stream_table(zf, members[table], columns, path, label)
            manifest["tables"][table] = rows
            if bad:
                manifest["malformed_rows"][table] = bad
            if table == "demo" and rows < MIN_REAL_DEMO_ROWS and not allow_fixture:
                os.remove(path)
                raise NotARealQuarter(
                    f"{label}: only {rows} reports (a real quarter has more than {MIN_REAL_DEMO_ROWS}). "
                    "This looks like a sample or mock file; refusing to load it."
                )

        deleted = _read_deleted_cases(zf, members["dele"]) if "dele" in members else []
        dele_schema = pa.schema([("caseid", pa.string()), ("quarter", pa.string())])
        pq.write_table(
            pa.table({"caseid": pa.array(deleted, pa.string()), "quarter": pa.array([label] * len(deleted), pa.string())},
                     schema=dele_schema),
            os.path.join(out_dir, f"dele_{label}.parquet"),
        )
        manifest["deleted_cases"] = len(deleted)

    sidecar = zip_path + ".sha256"
    if os.path.exists(sidecar):
        with open(sidecar, encoding="utf-8") as f:
            manifest["zip_sha256"] = f.read().split()[0]
    else:
        from faers.downloader import sha256_of
        manifest["zip_sha256"] = sha256_of(zip_path)
    manifest["fixture"] = bool(allow_fixture)
    manifest["parsed_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")

    with open(os.path.join(out_dir, f"manifest_{label}.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    log(f"[+] {label}: {manifest['tables']['demo']:,} reports, {manifest['tables']['drug']:,} drug rows, "
        f"{manifest['deleted_cases']:,} deleted cases"
        + (f", malformed rows skipped: {manifest['malformed_rows']}" if manifest["malformed_rows"] else ""))
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description="Parse FAERS quarterly zips into Parquet.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--quarters", nargs="+", metavar="YYYYqN")
    group.add_argument("--all", action="store_true", help="every verified zip in faers_raw")
    parser.add_argument("--force", action="store_true", help="re-parse quarters that already have a manifest")
    args = parser.parse_args(argv)

    try:
        from dotenv import load_dotenv
        from core.config import BACKEND_DIR
        load_dotenv(os.path.join(BACKEND_DIR, ".env"), override=False)
    except ImportError:
        pass

    from faers.downloader import raw_dir, is_verified
    raw = raw_dir()
    if args.all:
        zips = sorted(os.path.join(raw, n) for n in os.listdir(raw) if n.lower().endswith(".zip"))
    else:
        zips = [os.path.join(raw, f"faers_ascii_{q.lower()}.zip") for q in args.quarters]

    out = parquet_dir()
    failures = 0
    for zp in zips:
        label = _label_from_zip(zp)
        if not os.path.exists(zp):
            print(f"[-] {label}: {zp} not found; run faers.downloader first.", file=sys.stderr)
            failures += 1
            continue
        if not is_verified(zp):
            print(f"[-] {label}: {os.path.basename(zp)} has no verified checksum; run faers.downloader first.", file=sys.stderr)
            failures += 1
            continue
        if os.path.exists(os.path.join(out, f"manifest_{label}.json")) and not args.force:
            print(f"[=] {label}: already parsed (use --force to redo)")
            continue
        try:
            parse_quarter(zp, out)
        except Exception as exc:
            print(f"[-] {label}: {exc}", file=sys.stderr)
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
