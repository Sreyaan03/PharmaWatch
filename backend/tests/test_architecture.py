"""
test_architecture.py — keeps the backend layering from eroding.

Layers (an arrow means "may import from"):

    routes -> services, core
    services -> clients, ml, faers, db, database, core, other services
    clients, ml, faers -> core, db
    core -> db

Routes parse requests and format responses; they never reach the database, the
network, or a model directly. Services contain no Flask code. Nothing outside
the tests edits sys.path.
"""

import ast
import os

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SKIP_DIRS = {"tests", "__pycache__", "trained_models", "http_cache"}


def _py_files(*subdirs):
    roots = [os.path.join(BACKEND, d) for d in subdirs] if subdirs else [BACKEND]
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for name in filenames:
                if name.endswith(".py"):
                    yield os.path.join(dirpath, name)


def _imported_roots(path):
    """Top-level package names imported anywhere in the file (relative imports excluded)."""
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def _violations(subdirs, forbidden):
    found = []
    for path in _py_files(*subdirs):
        bad = _imported_roots(path) & forbidden
        if bad:
            found.append(f"{os.path.relpath(path, BACKEND)} imports {sorted(bad)}")
    return found


def test_nothing_outside_tests_edits_sys_path():
    offenders = []
    for path in _py_files():
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=path)
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in {"insert", "append", "extend"}
                    and isinstance(node.func.value, ast.Attribute)
                    and node.func.value.attr == "path"
                    and isinstance(node.func.value.value, ast.Name)
                    and node.func.value.value.id == "sys"):
                offenders.append(f"{os.path.relpath(path, BACKEND)}:{node.lineno}")
    assert not offenders, f"sys.path edited outside tests: {offenders}"


def test_routes_only_parse_requests_and_call_services():
    forbidden = {"db", "database", "requests", "torch", "duckdb", "clients", "ml", "faers",
                 "numpy", "pandas", "sklearn", "transformers", "reddit_scraper"}
    assert not _violations(["routes"], forbidden)


def test_services_have_no_flask_code():
    assert not _violations(["services"], {"flask", "werkzeug", "routes"})


def test_lower_layers_do_not_import_upward():
    upward = {"routes", "services", "flask"}
    # core/auth.py is the one request guard that legitimately needs Flask.
    problems = [p for p in _violations(["clients", "ml", "faers", "ingest"], upward)]
    problems += [p for p in _violations(["core"], {"routes", "services", "ml", "clients", "faers"})]
    assert not problems, problems


def test_only_the_app_entry_point_imports_routes():
    offenders = []
    for path in _py_files():
        rel = os.path.relpath(path, BACKEND)
        if rel in {"app.py"} or rel.startswith("routes" + os.sep):
            continue
        if "routes" in _imported_roots(path):
            offenders.append(rel)
    assert not offenders, offenders
