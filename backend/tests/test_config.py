"""
One configuration surface: every address, path and credential comes from core/config.py, so
moving a component to another machine is a settings change, never a code change.
"""

import ast
import os
import re
import sys

import pytest

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from core import config  # noqa: E402

SKIP_DIRS = {"tests", "__pycache__", "trained_models", "http_cache"}
CONFIG_FILE = os.path.join(BACKEND, "core", "config.py")
LITERAL = re.compile(r"localhost|127\.0\.0\.1|0\.0\.0\.0|^[A-Za-z]:[\\/]|^[\w.-]+:\d{2,5}$")


def _modules():
    for root, dirs, files in os.walk(BACKEND):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if f.endswith(".py"):
                path = os.path.join(root, f)
                if path != CONFIG_FILE:
                    yield path, ast.parse(open(path, encoding="utf-8").read())


def _rel(path):
    return os.path.relpath(path, BACKEND)


def test_only_core_config_reads_the_environment():
    offenders = []
    for path, tree in _modules():
        for node in ast.walk(tree):
            if (isinstance(node, ast.Attribute) and node.attr in ("getenv", "environ")
                    and isinstance(node.value, ast.Name) and node.value.id == "os"):
                offenders.append(f"{_rel(path)}:{node.lineno}")
            if isinstance(node, ast.ImportFrom) and node.module == "dotenv":
                offenders.append(f"{_rel(path)}:{node.lineno} (dotenv)")
    assert not offenders, f"read settings through core.config instead: {offenders}"


def test_no_hard_coded_hosts_ports_or_drive_paths():
    offenders = []
    for path, tree in _modules():
        docstrings = {id(n.value) for n in ast.walk(tree)
                      if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)}
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings
                    and not node.value.startswith(("http://", "https://")) and LITERAL.search(node.value)):
                offenders.append(f"{_rel(path)}:{node.lineno} {node.value[:40]!r}")
    assert not offenders, f"move these into core/config.py SETTINGS: {offenders}"


def test_env_example_is_generated_from_the_settings():
    with open(config.ENV_EXAMPLE, encoding="utf-8") as f:
        assert f.read() == config.env_example(), "run `python -m core.config` from backend/"


def test_env_example_never_contains_a_secret_value(monkeypatch):
    monkeypatch.setenv("MYSQL_PASSWORD", "hunter2")
    text = config.env_example()
    assert "hunter2" not in text
    for s in config.SETTINGS:
        if s.secret:
            assert f"\n{s.name}=\n" in text


def test_settings_are_typed_and_default_when_unset(monkeypatch):
    monkeypatch.setenv("MYSQL_PORT", "3307")
    monkeypatch.setenv("FAERS_AUTO_REFRESH", "yes")
    monkeypatch.setenv("QUEUE_MODE", "  ")
    assert config.get("MYSQL_PORT") == 3307
    assert config.get("FAERS_AUTO_REFRESH") is True
    assert config.get("QUEUE_MODE") == "inline"          # blank counts as unset


def test_an_unknown_setting_is_an_error_not_a_silent_default():
    with pytest.raises(KeyError):
        config.get("MYSQL_HOSTNAME")


def test_every_setting_has_a_description_and_a_unique_name():
    names = [s.name for s in config.SETTINGS]
    assert len(names) == len(set(names))
    assert all(s.description for s in config.SETTINGS)
