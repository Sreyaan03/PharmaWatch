"""
The storage interface: the same behaviour on local disk and on an S3-compatible store, so files
can move to shared storage by changing STORAGE_BACKEND alone.
"""

import os
import sys

import pytest

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from core import config, storage  # noqa: E402


@pytest.fixture(params=["local", "s3"])
def store(request, tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(config, "MODEL_DIR", str(tmp_path / "models"))
    if request.param == "local":
        monkeypatch.setenv("STORAGE_BACKEND", "local")
        yield storage.backend()
        return
    moto = pytest.importorskip("moto")
    monkeypatch.setenv("STORAGE_BACKEND", "s3")
    monkeypatch.setenv("S3_BUCKET", "pw-test")
    monkeypatch.setenv("S3_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("S3_SECRET_ACCESS_KEY", "test")
    monkeypatch.setenv("STORAGE_CACHE_DIR", str(tmp_path / "cache"))
    with moto.mock_aws():
        s = storage.backend()
        s.ensure_bucket()
        yield s


def test_put_get_exists_and_delete(store):
    storage.put("uploads", "job1/rx.pdf", b"%PDF-1")
    assert storage.exists("uploads", "job1/rx.pdf") and storage.get("uploads", "job1/rx.pdf") == b"%PDF-1"
    storage.delete("uploads", "job1/rx.pdf")
    assert not storage.exists("uploads", "job1/rx.pdf") and storage.get("uploads", "job1/rx.pdf") is None


def test_listing_and_prefix_delete_stay_inside_one_job(store):
    for key in ("job1/a.png", "job1/b.png", "job2/a.png"):
        storage.put("uploads", key, b"x")
    assert storage.list_keys("uploads", "job1") == ["job1/a.png", "job1/b.png"]
    storage.delete_prefix("uploads", "job1")
    assert storage.list_keys("uploads") == ["job2/a.png"]


def test_areas_are_separate(store):
    storage.put("models", "m.json", b"{}")
    assert storage.get("uploads", "m.json") is None and storage.get("models", "m.json") == b"{}"


def test_local_path_is_a_readable_copy_that_follows_changes(store):
    storage.put("models", "nested/w.bin", b"v1")
    path = storage.local_path("models", "nested/w.bin")
    assert open(path, "rb").read() == b"v1"
    storage.put("models", "nested/w.bin", b"version two")
    assert open(storage.local_path("models", "nested/w.bin"), "rb").read() == b"version two"
    assert storage.local_path("models", "missing.bin") is None


def test_put_file_uploads_a_local_file(store, tmp_path):
    src = tmp_path / "src.bin"
    src.write_bytes(b"abc")
    storage.put_file("models", "copied.bin", str(src))
    assert storage.get("models", "copied.bin") == b"abc"


@pytest.mark.parametrize("bad", ["", "../escape", "a/../../b", "/", "a//b"])
def test_keys_cannot_escape_their_area(store, bad):
    with pytest.raises(ValueError):
        storage.put("uploads", bad, b"x")


def test_unknown_areas_and_backends_are_errors(store, monkeypatch):
    with pytest.raises(ValueError):
        storage.put("secrets", "k", b"x")
    monkeypatch.setenv("STORAGE_BACKEND", "ftp")
    with pytest.raises(ValueError):
        storage.backend()


def test_local_backend_keeps_the_existing_folder_layout(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    storage.put("uploads", "j/x.txt", b"1")
    assert (tmp_path / "uploads" / "j" / "x.txt").read_bytes() == b"1"
    assert storage.backend().root("models") == config.MODEL_DIR


def test_s3_uri_is_what_spark_reads(monkeypatch, tmp_path):
    moto = pytest.importorskip("moto")
    monkeypatch.setenv("STORAGE_BACKEND", "s3")
    monkeypatch.setenv("S3_BUCKET", "lake-bucket")
    monkeypatch.setenv("S3_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("S3_SECRET_ACCESS_KEY", "test")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    with moto.mock_aws():
        assert storage.uri("lake", "clean/case") == "s3a://lake-bucket/lake/clean/case"


def test_no_module_opens_upload_or_model_paths_directly():
    """Uploads and models go through core.storage; reading them by path would break on S3."""
    import re
    offenders = []
    pattern = re.compile(r"uploads_root\(\)|MODEL_DIR")
    allowed = {os.path.join("core", "storage.py"), os.path.join("core", "config.py"), os.path.join("core", "jobs.py"),
               os.path.join("ml", "ddi", "baselines.py")}           # baselines.py writes models at training time
    for root, dirs, files in os.walk(BACKEND):
        dirs[:] = [d for d in dirs if d not in {"tests", "__pycache__", "validation", "trained_models"}]
        for f in files:
            if f.endswith(".py"):
                rel = os.path.relpath(os.path.join(root, f), BACKEND)
                if rel not in allowed and pattern.search(open(os.path.join(root, f), encoding="utf-8").read()):
                    offenders.append(rel)
    assert not offenders, offenders
