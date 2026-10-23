"""
Startup configuration validation: config.validate() reports every problem at once (not one per
restart), the checks are conditional on the feature being turned on, and a coherent configuration
reports nothing. check_or_raise() turns a non-empty list into a single RuntimeError.
"""

import os
import sys

import pytest

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from core import config  # noqa: E402

# Settings each test pins, so the ambient environment never changes the outcome.
_PINNED = ["AUTH_REQUIRED", "FLASK_SECRET_KEY", "STORAGE_BACKEND", "S3_BUCKET",
           "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY", "QUEUE_MODE",
           "KAFKA_BOOTSTRAP_SERVERS", "RX_EXTRACTOR", "ANTHROPIC_API_KEY"]


@pytest.fixture
def clean_env(monkeypatch):
    # A coherent single-machine default: nothing turned on that needs extra configuration.
    for name in _PINNED:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("QUEUE_MODE", "inline")
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("RX_EXTRACTOR", "auto")
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    return monkeypatch


def test_coherent_default_has_no_problems(clean_env):
    assert config.validate() == []


def test_auth_required_without_secret_is_reported(clean_env):
    clean_env.setenv("AUTH_REQUIRED", "true")
    problems = config.validate()
    assert any("FLASK_SECRET_KEY" in p for p in problems)


def test_auth_required_with_secret_is_fine(clean_env):
    clean_env.setenv("AUTH_REQUIRED", "true")
    clean_env.setenv("FLASK_SECRET_KEY", "x" * 64)
    assert config.validate() == []


def test_all_problems_are_reported_at_once(clean_env):
    # s3 storage with no credentials AND cloud extractor with no key: every missing value appears.
    clean_env.setenv("STORAGE_BACKEND", "s3")
    clean_env.setenv("RX_EXTRACTOR", "cloud")
    problems = config.validate()
    joined = "\n".join(problems)
    for expected in ("S3_BUCKET", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY", "ANTHROPIC_API_KEY"):
        assert expected in joined
    assert len(problems) >= 4


def test_invalid_enum_is_reported(clean_env):
    clean_env.setenv("QUEUE_MODE", "sometimes")
    assert any("QUEUE_MODE" in p for p in config.validate())


def test_check_or_raise_names_every_problem(clean_env):
    clean_env.setenv("STORAGE_BACKEND", "s3")
    with pytest.raises(RuntimeError) as exc:
        config.check_or_raise()
    msg = str(exc.value)
    assert "S3_BUCKET" in msg and "S3_SECRET_ACCESS_KEY" in msg


def test_check_or_raise_silent_when_coherent(clean_env):
    config.check_or_raise()   # must not raise
