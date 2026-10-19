# backend/core/storage.py
# Every file the app reads or writes at run time goes through here, in one of a few named areas:
#
#   uploads   prescription and text-mining uploads, deleted when their job ends
#   lake      the cleaned FAERS data and batch results (Parquet)
#   models    trained models (the deployed interaction model, per-drug forecasters)
#   reports   validation results
#
# Two backends, chosen by the STORAGE_BACKEND setting:
#   local  directories on this machine (the default; the layout the app has always used)
#   s3     any S3-compatible store (MinIO locally, S3 or similar in the cloud): one bucket,
#          keys "<area>/<key>". Every machine running the app sees the same files.
#
# Code that needs a real file path (torch.load, xgboost, DuckDB) asks for local_path(): on the
# local backend that is the file itself; on s3 it is a cached copy, refreshed when the stored
# object changes.

import os
import shutil
import threading

from core import config

AREAS = ("uploads", "lake", "models", "reports")


def _check(area, key):
    if area not in AREAS:
        raise ValueError(f"unknown storage area {area!r}; one of {AREAS}")
    key = (key or "").replace("\\", "/").lstrip("/")
    if not key or any(part in ("", ".", "..") for part in key.split("/")):
        raise ValueError(f"invalid storage key {key!r}")
    return key


class LocalStorage:
    name = "local"

    def root(self, area):
        if area == "models":
            return config.MODEL_DIR
        if area == "reports":
            return os.path.join(config.REPORTS_DIR, "validation")
        return os.path.join(config.data_dir(), area)

    def _path(self, area, key):
        return os.path.join(self.root(area), *_check(area, key).split("/"))

    def put(self, area, key, data):
        path = self._path(area, key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)

    def put_file(self, area, key, source):
        path = self._path(area, key)
        if os.path.abspath(source) != os.path.abspath(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            shutil.copyfile(source, path)

    def get(self, area, key):
        path = self._path(area, key)
        if not os.path.exists(path):
            return None
        with open(path, "rb") as f:
            return f.read()

    def exists(self, area, key):
        return os.path.exists(self._path(area, key))

    def delete(self, area, key):
        path = self._path(area, key)
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
        elif os.path.exists(path):
            os.remove(path)

    def delete_prefix(self, area, prefix):
        self.delete(area, prefix.rstrip("/"))

    def list(self, area, prefix=""):
        root = self.root(area)
        base = os.path.join(root, *prefix.strip("/").split("/")) if prefix.strip("/") else root
        out = []
        for d, _, files in os.walk(base):
            for f in files:
                out.append(os.path.relpath(os.path.join(d, f), root).replace(os.sep, "/"))
        return sorted(out)

    def local_path(self, area, key):
        path = self._path(area, key)
        return path if os.path.exists(path) else None

    def uri(self, area, key=""):
        return self._path(area, key) if key else self.root(area)


class S3Storage:
    name = "s3"

    def __init__(self):
        import boto3
        self.bucket = config.get("S3_BUCKET")
        self.client = boto3.client(
            "s3", endpoint_url=config.get("S3_ENDPOINT_URL") or None, region_name=config.get("S3_REGION"),
            aws_access_key_id=config.get("S3_ACCESS_KEY_ID") or None,
            aws_secret_access_key=config.get("S3_SECRET_ACCESS_KEY") or None)
        self.cache_dir = config.get("STORAGE_CACHE_DIR") or os.path.join(config.data_dir(), "cache")
        self._lock = threading.Lock()

    def _key(self, area, key):
        return f"{area}/{_check(area, key)}"

    def ensure_bucket(self):
        from botocore.exceptions import ClientError
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except ClientError:
            self.client.create_bucket(Bucket=self.bucket)

    def put(self, area, key, data):
        self.client.put_object(Bucket=self.bucket, Key=self._key(area, key), Body=data)

    def put_file(self, area, key, source):
        self.client.upload_file(source, self.bucket, self._key(area, key))

    def get(self, area, key):
        from botocore.exceptions import ClientError
        try:
            return self.client.get_object(Bucket=self.bucket, Key=self._key(area, key))["Body"].read()
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
                return None
            raise

    def _head(self, area, key):
        from botocore.exceptions import ClientError
        try:
            return self.client.head_object(Bucket=self.bucket, Key=self._key(area, key))
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404", "NotFound"):
                return None
            raise

    def exists(self, area, key):
        return self._head(area, key) is not None

    def delete(self, area, key):
        self.client.delete_object(Bucket=self.bucket, Key=self._key(area, key))

    def delete_prefix(self, area, prefix):
        keys = [f"{area}/{k}" for k in self.list(area, prefix)]
        for i in range(0, len(keys), 1000):
            self.client.delete_objects(Bucket=self.bucket,
                                       Delete={"Objects": [{"Key": k} for k in keys[i:i + 1000]]})

    def list(self, area, prefix=""):
        full = f"{area}/{prefix.lstrip('/')}"
        out = []
        for page in self.client.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=full):
            out += [o["Key"][len(area) + 1:] for o in page.get("Contents", [])]
        return sorted(out)

    def local_path(self, area, key):
        """A cached local copy, downloaded again when the stored object's ETag changes."""
        head = self._head(area, key)
        if head is None:
            return None
        path = os.path.join(self.cache_dir, area, *_check(area, key).split("/"))
        tag_file = path + ".etag"
        with self._lock:
            current = open(tag_file, encoding="utf-8").read() if os.path.exists(tag_file) else None
            if current != head["ETag"] or not os.path.exists(path):
                os.makedirs(os.path.dirname(path), exist_ok=True)
                self.client.download_file(self.bucket, self._key(area, key), path + ".part")
                os.replace(path + ".part", path)
                with open(tag_file, "w", encoding="utf-8") as f:
                    f.write(head["ETag"])
        return path

    def uri(self, area, key="", scheme="s3a"):
        """Location for engines that read the store directly (Spark reads s3a://)."""
        return f"{scheme}://{self.bucket}/{area}/{_check(area, key) if key else ''}".rstrip("/")


_backend = None
_backend_key = None
_backend_lock = threading.Lock()


def backend():
    """The configured backend; rebuilt when the settings change (tests switch between them)."""
    global _backend, _backend_key
    kind = (config.get("STORAGE_BACKEND") or "local").lower()
    key = (kind, config.get("S3_ENDPOINT_URL"), config.get("S3_BUCKET"), config.data_dir())
    with _backend_lock:
        if _backend is None or _backend_key != key:
            if kind == "local":
                _backend = LocalStorage()
            elif kind == "s3":
                _backend = S3Storage()
            else:
                raise ValueError(f"STORAGE_BACKEND must be 'local' or 's3', not {kind!r}")
            _backend_key = key
        return _backend


def put(area, key, data):
    backend().put(area, key, data)


def put_file(area, key, source):
    backend().put_file(area, key, source)


def get(area, key):
    return backend().get(area, key)


def exists(area, key):
    return backend().exists(area, key)


def delete(area, key):
    backend().delete(area, key)


def delete_prefix(area, prefix):
    backend().delete_prefix(area, prefix)


def list_keys(area, prefix=""):
    return backend().list(area, prefix)


def local_path(area, key):
    return backend().local_path(area, key)


def uri(area, key=""):
    return backend().uri(area, key)


def seed_models(source_dir=None, log=print):
    """Copy the model files shipped with the code into the configured store (S3 deployments).

    Skips files the store already has with the same size, so it is safe to run on every start.
    """
    import os as _os
    source_dir = source_dir or config.MODEL_DIR
    b = backend()
    if b.name == "local":
        log("[*] local storage reads models from the code folder; nothing to seed")
        return 0
    b.ensure_bucket()
    copied = 0
    for root, _, files in _os.walk(source_dir):
        for f in files:
            path = _os.path.join(root, f)
            key = _os.path.relpath(path, source_dir).replace(_os.sep, "/")
            head = b._head("models", key)
            if head is not None and head.get("ContentLength") == _os.path.getsize(path):
                continue
            b.put_file("models", key, path)
            copied += 1
    log(f"[+] seeded {copied} model file(s) into {b.uri('models')}")
    return copied


if __name__ == "__main__":
    import sys as _sys
    command = _sys.argv[1] if len(_sys.argv) > 1 else ""
    if command == "seed-models":
        seed_models()
    elif command == "ensure-bucket":
        b = backend()
        if b.name == "s3":
            b.ensure_bucket()
        print(f"[+] storage ready ({b.name})")
    else:
        print("usage: python -m core.storage [ensure-bucket | seed-models]", file=_sys.stderr)
        _sys.exit(2)
