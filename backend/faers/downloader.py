# backend/faers/downloader.py
# Downloads real FDA FAERS quarterly ASCII files into <DATA_DIR>/faers_raw.
#
#   python -m faers.downloader --latest 20            # the 20 most recent released quarters
#   python -m faers.downloader --latest 20 --dry-run  # list them, check only the newest
#   python -m faers.downloader --quarters 2024q1 2024q2
#   python -m faers.downloader --year 2024 --qtr 3
#   python -m faers.downloader --adopt                # register zips you downloaded by hand
#
# Run from the backend directory. Only the FDA's published files are accepted: a download is
# checked against the size the server reports, tested as a zip, and given a SHA-256 sidecar.
#
# The FDA host rate-limits bursts (it resets connections), so this module is deliberately
# gentle: files that already verified are never requested again, there is one request per new
# file, and requests are spaced out.

import argparse
import datetime
import hashlib
import os
import sys
import time
import zipfile

import requests

from core.config import data_dir

URL_TEMPLATE = "https://fis.fda.gov/content/Exports/faers_ascii_{label}.zip"
USER_AGENT = "PharmaWatch/1.0 (research; pharmacovigilance dashboard)"
CHUNK = 1024 * 1024
TIMEOUT = (10, 30)        # connect, read: the host sometimes stalls a request indefinitely
ATTEMPTS = 6
PAUSE_BETWEEN_FILES = 3.0


def _backoff(attempt):
    time.sleep(min(5 * 2 ** attempt, 120))


def quarter_label(year, qtr):
    """(2024, 3) -> '2024q3'."""
    if qtr not in (1, 2, 3, 4):
        raise ValueError(f"quarter must be 1-4, got {qtr!r}")
    return f"{int(year)}q{int(qtr)}"


def parse_label(label):
    """'2024q3' -> (2024, 3)."""
    text = label.strip().lower()
    if len(text) != 6 or text[4] != "q" or not text[:4].isdigit() or text[5] not in "1234":
        raise ValueError(f"not a quarter label (expected e.g. 2024q3): {label!r}")
    return int(text[:4]), int(text[5])


def previous_quarter(year, qtr):
    return (year, qtr - 1) if qtr > 1 else (year - 1, 4)


def current_quarter(today=None):
    today = today or datetime.date.today()
    return today.year, (today.month - 1) // 3 + 1


def url_for(year, qtr):
    return URL_TEMPLATE.format(label=quarter_label(year, qtr))


def raw_dir():
    path = os.path.join(data_dir(), "faers_raw")
    os.makedirs(path, exist_ok=True)
    return path


def zip_path_for(year, qtr, directory=None):
    return os.path.join(directory or raw_dir(), f"faers_ascii_{quarter_label(year, qtr)}.zip")


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def sidecar_path(zip_path):
    return zip_path + ".sha256"


def is_verified(zip_path):
    """True when the zip exists and matches the checksum written when it was verified.

    The sidecar is only ever written after the size and zip integrity checks passed, so a
    matching checksum means the file is complete.
    """
    sidecar = sidecar_path(zip_path)
    if not (os.path.exists(zip_path) and os.path.exists(sidecar)):
        return False
    with open(sidecar, encoding="utf-8") as f:
        recorded = f.read().split()[0]
    return recorded == sha256_of(zip_path)


def _get(http, url, headers, stream=True):
    """GET with retries on connection errors and stalls. Returns the response."""
    last = None
    for attempt in range(ATTEMPTS):
        try:
            return http.get(url, headers=headers, stream=stream, timeout=TIMEOUT, allow_redirects=True)
        except requests.RequestException as exc:
            last = exc
            _backoff(attempt)
    raise RuntimeError(f"The FDA FAERS site did not respond after {ATTEMPTS} attempts: {last}")


def _total_size(resp):
    """Total file size from a (possibly ranged) response, or None."""
    content_range = resp.headers.get("Content-Range", "")
    if "/" in content_range and content_range.rsplit("/", 1)[1].isdigit():
        return int(content_range.rsplit("/", 1)[1])
    length = resp.headers.get("Content-Length")
    if resp.status_code == 200 and length and length.isdigit():
        return int(length)
    return None


def probe_size(year, qtr, session=None):
    """Size in bytes of a published quarter, or None if it is not (yet) released.

    One 4-byte ranged request. Raises RuntimeError if the host cannot be reached, so a network
    failure is never mistaken for an unreleased quarter.
    """
    resp = _get(session or requests, url_for(year, qtr), {"Range": "bytes=0-3", "User-Agent": USER_AGENT})
    try:
        if resp.status_code not in (200, 206):
            return None
        head = resp.raw.read(4, decode_content=False)
        if not head.startswith(b"PK"):
            return None
        return _total_size(resp)
    finally:
        resp.close()


def latest_released(count, today=None, probe=probe_size):
    """The `count` most recent released quarters, oldest first, as [(year, qtr), ...].

    Probes downward from the current calendar quarter until the newest released one is found
    (normally one or two requests), then takes the preceding `count - 1` quarters, which
    the FDA has long since published. A missing older file is reported when it is downloaded.
    """
    year, qtr = current_quarter(today)
    for _ in range(6):
        if probe(year, qtr) is not None:
            quarters = [(year, qtr)]
            while len(quarters) < count:
                quarters.append(previous_quarter(*quarters[-1]))
            return sorted(quarters)
        year, qtr = previous_quarter(year, qtr)
        time.sleep(1.0)
    raise RuntimeError("No released FAERS quarter found in the last six quarters; check the FDA site.")


def download_quarter(year, qtr, directory=None, session=None, log=print):
    """Download one quarter, resuming a partial file. Returns the zip path."""
    http = session or requests
    dest = zip_path_for(year, qtr, directory)
    label = quarter_label(year, qtr)
    os.makedirs(os.path.dirname(dest), exist_ok=True)

    if is_verified(dest):
        log(f"[=] {label}: already downloaded and verified ({os.path.getsize(dest) / 1e6:.1f} MB)")
        return dest

    part = dest + ".part"
    expected = None
    for attempt in range(ATTEMPTS):
        have = os.path.getsize(part) if os.path.exists(part) else 0
        if expected is not None and have == expected:
            break
        headers = {"User-Agent": USER_AGENT}
        if have:
            headers["Range"] = f"bytes={have}-"
        resp = _get(http, url_for(year, qtr), headers)
        try:
            if resp.status_code == 404:
                raise RuntimeError(f"FAERS {label} is not available from the FDA.")
            if resp.status_code == 416 and have:        # already have every byte
                expected = have
                break
            resp.raise_for_status()
            expected = _total_size(resp) or expected
            resuming = bool(have) and resp.status_code == 206
            if not resuming:
                have = 0
            log(f"[*] {label}: downloading {(expected or 0) / 1e6:.1f} MB"
                + (f" (resuming at {have / 1e6:.1f} MB)" if resuming else ""))
            try:
                with open(part, "ab" if resuming else "wb") as f:
                    for block in resp.iter_content(chunk_size=CHUNK):
                        if block:
                            f.write(block)
            except requests.RequestException as exc:
                log(f"[!] {label}: {type(exc).__name__}; will resume")
                _backoff(attempt)
                continue
        finally:
            resp.close()
        if expected is None or os.path.getsize(part) >= expected:
            break

    size = os.path.getsize(part) if os.path.exists(part) else 0
    if expected is None or size != expected:
        raise RuntimeError(f"{label}: have {size} of {expected} bytes. Re-run to resume.")
    try:
        with zipfile.ZipFile(part) as zf:
            bad = zf.testzip()
        if bad:
            raise RuntimeError(f"{label}: corrupt member {bad}")
    except zipfile.BadZipFile as exc:
        os.remove(part)
        raise RuntimeError(f"{label}: not a valid zip file ({exc}); removed, re-run to retry.") from exc

    os.replace(part, dest)
    digest = sha256_of(dest)
    with open(sidecar_path(dest), "w", encoding="utf-8") as f:
        f.write(f"{digest}  {os.path.basename(dest)}\n")
    log(f"[+] {label}: verified, sha256 {digest[:16]}...")
    return dest


def adopt_existing(directory=None, log=print):
    """Verify zips placed in the raw folder by hand and record their checksum.

    A zip is accepted when its name is faers_ascii_YYYYqN.zip, it is a valid zip, and it holds
    the five FAERS tables. Returns ([adopted labels], [(label or name, reason)]).
    """
    directory = directory or raw_dir()
    adopted, rejected = [], []
    for name in sorted(os.listdir(directory)):
        if not (name.lower().startswith("faers_ascii_") and name.lower().endswith(".zip")):
            continue
        path = os.path.join(directory, name)
        try:
            label = quarter_label(*parse_label(name[len("faers_ascii_"):-len(".zip")]))
        except ValueError as exc:
            rejected.append((name, str(exc)))
            continue
        if is_verified(path):
            continue
        try:
            with zipfile.ZipFile(path) as zf:
                bad = zf.testzip()
                members = {os.path.basename(n).upper()[:4] for n in zf.namelist()}
            if bad:
                raise RuntimeError(f"corrupt member {bad}")
            missing = {"DEMO", "DRUG", "REAC", "THER", "OUTC"} - members
            if missing:
                raise RuntimeError(f"missing tables {sorted(missing)}")
        except (zipfile.BadZipFile, RuntimeError) as exc:
            rejected.append((label, f"not accepted: {exc}"))
            log(f"[-] {label}: not accepted ({exc})")
            continue
        digest = sha256_of(path)
        with open(sidecar_path(path), "w", encoding="utf-8") as f:
            f.write(f"{digest}  {name}" + chr(10))
        adopted.append(label)
        log(f"[+] {label}: adopted, sha256 {digest[:16]}...")
    return adopted, rejected


def main(argv=None):
    parser = argparse.ArgumentParser(description="Download real FDA FAERS quarterly ASCII files.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--adopt", action="store_true", help="verify and register zips already in faers_raw")
    group.add_argument("--latest", type=int, metavar="N", help="the N most recent released quarters")
    group.add_argument("--quarters", nargs="+", metavar="YYYYqN", help="specific quarters, e.g. 2024q1 2024q2")
    group.add_argument("--year", type=int, help="year of a single quarter (use with --qtr)")
    parser.add_argument("--qtr", type=int, choices=[1, 2, 3, 4], help="quarter of a single --year")
    parser.add_argument("--dry-run", action="store_true", help="list quarters and what is already on disk")
    args = parser.parse_args(argv)

    from core.config import load_env
    load_env()

    if args.adopt:
        adopted, rejected = adopt_existing()
        print(f"[*] adopted {len(adopted)}: {', '.join(adopted) or '-'}; rejected {len(rejected)}")
        return 1 if rejected else 0

    if args.latest:
        quarters = latest_released(args.latest)
    elif args.quarters:
        quarters = [parse_label(q) for q in args.quarters]
    else:
        if not args.qtr:
            parser.error("--year requires --qtr")
        quarters = [(args.year, args.qtr)]

    target = raw_dir()
    print(f"[*] Target directory: {target}")
    print(f"[*] Quarters ({len(quarters)}): {quarter_label(*quarters[0])} .. {quarter_label(*quarters[-1])}")
    if args.dry_run:
        for y, q in quarters:
            path = zip_path_for(y, q, target)
            state = "verified on disk" if is_verified(path) else ("partial" if os.path.exists(path + ".part") else "to download")
            print(f"    {quarter_label(y, q)}  {state:17}  {path}")
        return 0

    failures = []
    for i, (y, q) in enumerate(quarters):
        try:
            fresh = not is_verified(zip_path_for(y, q, target))
            download_quarter(y, q, target)
            if fresh and i < len(quarters) - 1:
                time.sleep(PAUSE_BETWEEN_FILES)
        except Exception as exc:                      # keep going; report at the end
            failures.append((quarter_label(y, q), str(exc)))
            print(f"[-] {quarter_label(y, q)}: {exc}", file=sys.stderr)
    if failures:
        print(f"[-] {len(failures)} quarter(s) failed; re-run the same command to resume.", file=sys.stderr)
        return 1
    print("[+] All quarters downloaded and verified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
