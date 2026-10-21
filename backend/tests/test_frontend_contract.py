"""
Keeps the frontend and backend in step so they cannot drift apart unnoticed:
  * every /api/... path the frontend references matches a rule in app.url_map,
  * every navigation target (data-section / data-nav / NAV_AREAS) has a section id in index.html,
  * every <script src="/static/..."> points to a file that exists.
"""

import os
import re
import sys

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

SRC = os.path.abspath(os.path.join(BACKEND, "..", "src"))
from app import app  # noqa: E402

# A same-origin /api/ path: must start right after a quote or backtick (so external URLs such as
# https://en.wikipedia.org/api/rest_v1/... are not picked up). ${...} template blocks allowed.
_API = re.compile(r"""(?<=["'`])/api/(?:\$\{[^}]*\}|[A-Za-z0-9_\-/.:])+""")
# dynamic segment left by a template literal
_DYN = re.compile(r"\$\{[^}]*\}")


def _src_files():
    for name in os.listdir(SRC):
        if name.endswith(".js") or name.endswith(".html"):
            yield os.path.join(SRC, name)


def _referenced_api_paths():
    paths = set()
    for path in _src_files():
        text = open(path, encoding="utf-8", errors="replace").read()
        for m in _API.finditer(text):
            raw = _DYN.sub("1", m.group(0))   # template value -> numeric placeholder (matches <int:>)
            raw = raw.split("?")[0].rstrip("/.")
            if "$" in raw or "{" in raw:
                continue
            paths.add(raw)
    return sorted(paths)


def test_every_referenced_api_path_exists():
    from werkzeug.exceptions import MethodNotAllowed
    adapter = app.url_map.bind("localhost")
    rules = [r.rule for r in app.url_map.iter_rules()]
    missing = []
    for path in _referenced_api_paths():
        ok = False
        for method in ("GET", "POST"):
            try:
                adapter.match(path, method=method)
                ok = True
                break
            except MethodNotAllowed:
                ok = True                            # path exists, just not this method
                break
            except Exception:                        # NotFound: try the other method
                continue
        # a concatenated path can resolve to only a prefix of a real rule (e.g. "/api/drug")
        if not ok:
            ok = any(r.startswith(path + "/") or r == path for r in rules)
        if not ok:
            missing.append(path)
    assert not missing, f"frontend calls /api paths with no backend rule: {missing}"


def test_navigation_targets_have_sections():
    html = open(os.path.join(SRC, "index.html"), encoding="utf-8", errors="replace").read()
    ids = set(re.findall(r'id="([^"]+)"', html))
    targets = set(re.findall(r'data-(?:section|nav)="([^"]+)"', html))
    # section ids declared in the NAV_AREAS block of app.js (scoped to that block only)
    appjs = open(os.path.join(SRC, "app.js"), encoding="utf-8", errors="replace").read()
    block = re.search(r"const NAV_AREAS = \[(.*?)\n\];", appjs, re.S)
    if block:
        targets |= set(re.findall(r"\[\s*'([a-z0-9\-]+)'\s*,\s*'[^']+'\s*\]", block.group(1)))
    missing = sorted(t for t in targets if t and t not in ids)
    assert not missing, f"navigation targets with no matching section id: {missing}"


def test_script_tags_point_to_existing_files():
    html = open(os.path.join(SRC, "index.html"), encoding="utf-8", errors="replace").read()
    missing = []
    for src in re.findall(r'<script[^>]+src="(/static/[^"]+)"', html):
        rel = src[len("/static/"):]
        if not os.path.exists(os.path.join(SRC, rel)):
            missing.append(src)
    assert not missing, f"script tags point to missing files: {missing}"
