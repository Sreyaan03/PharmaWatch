# backend/clients/rxnav.py
# RxNav (NLM RxNorm) client: maps a drug name, brand or salt form to its RxNorm ingredient(s).
#
# Every response is cached on disk (JSON lines) so a name is looked up once, ever. The client is
# thread-safe: request starts are spaced `min_interval` apart across all threads, which keeps it
# well inside NLM's limit of 20 requests per second per IP.

import json
import os
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

import requests

BASE = "https://rxnav.nlm.nih.gov/REST"
USER_AGENT = "PharmaWatch/1.0 (research; pharmacovigilance dashboard)"

# approximateTerm scores do not separate right from wrong ("ibuprofn" -> ibuprofen scores 8.5,
# "metfromin" -> merbromin, an antiseptic, scores 9.2), so a fuzzy match is only ever offered
# as a suggestion for a person to confirm and is never used as a resolution.
APPROX_MIN_SCORE = 5.0


class RxNavError(RuntimeError):
    """RxNav could not be reached after retries."""


class RxNavClient:
    def __init__(self, cache_path=None, session=None, min_interval=0.08, timeout=(5, 20), attempts=4,
                 sleep=time.sleep):
        self.cache_path = cache_path
        self.session = session or requests
        self.min_interval = min_interval
        self.timeout = timeout
        self.attempts = attempts
        self._sleep = sleep
        self._cache = {}
        self._lock = threading.Lock()
        self._next_slot = 0.0
        self.live_calls = 0
        if cache_path and os.path.exists(cache_path):
            with open(cache_path, encoding="utf-8") as f:
                for line in f:
                    try:
                        entry = json.loads(line)
                        self._cache[entry["k"]] = entry["v"]
                    except (ValueError, KeyError):
                        continue            # a torn last line after a crash is ignored

    # ── transport ────────────────────────────────────────────────────────────
    @staticmethod
    def _key(path, params):
        return path + "?" + urllib.parse.urlencode(sorted((params or {}).items()))

    def _remember(self, key, value):
        with self._lock:
            self._cache[key] = value
            if self.cache_path:
                os.makedirs(os.path.dirname(self.cache_path) or ".", exist_ok=True)
                with open(self.cache_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps({"k": key, "v": value}) + "\n")

    def _wait_for_slot(self):
        """Reserve the next request slot so all threads together stay under the rate limit."""
        with self._lock:
            now = time.time()
            start = max(now, self._next_slot)
            self._next_slot = start + self.min_interval
        if start > now:
            self._sleep(start - now)

    def _get_json(self, path, params=None):
        """Cached GET returning parsed JSON ({} when the API has nothing for the query)."""
        key = self._key(path, params)
        if key in self._cache:
            return self._cache[key]

        last = None
        for attempt in range(self.attempts):
            self._wait_for_slot()
            try:
                resp = self.session.get(BASE + path, params=params, headers={"User-Agent": USER_AGENT},
                                        timeout=self.timeout)
                with self._lock:
                    self.live_calls += 1
                if resp.status_code == 404:
                    self._remember(key, {})
                    return {}
                if resp.status_code in (429, 500, 502, 503, 504):
                    last = RxNavError(f"HTTP {resp.status_code}")
                    self._sleep(2 ** attempt)
                    continue
                resp.raise_for_status()
                data = resp.json()
                self._remember(key, data)
                return data
            except (requests.RequestException, ValueError) as exc:
                last = exc
                self._sleep(2 ** attempt)
        raise RxNavError(f"RxNav request failed for {path}: {last}")

    # ── lookups ──────────────────────────────────────────────────────────────
    def rxcui_for_name(self, name):
        """RxCUIs whose name is exactly `name` (brand, ingredient, salt or clinical drug).

        The exact search (search=0) is used first. The normalised search (search=1) returns every
        concept with a similar name, for example sulfamethoxazole together with sulfamethazine, so
        it is only a fallback and is accepted only when it names a single concept.
        """
        data = self._get_json("/rxcui.json", {"name": name, "search": 0})
        ids = (data.get("idGroup") or {}).get("rxnormId") or []
        if ids:
            return ids
        data = self._get_json("/rxcui.json", {"name": name, "search": 1})
        ids = (data.get("idGroup") or {}).get("rxnormId") or []
        return ids if len(ids) == 1 else []

    def approximate(self, name):
        """Best fuzzy match as {'rxcui', 'name', 'score'}, or None. A suggestion only; see above."""
        data = self._get_json("/approximateTerm.json", {"term": name, "maxEntries": 1})
        for cand in (data.get("approximateGroup") or {}).get("candidate") or []:
            try:
                score = float(cand.get("score", 0))
            except (TypeError, ValueError):
                continue
            if score >= APPROX_MIN_SCORE:
                return {"rxcui": cand["rxcui"], "name": cand.get("name"), "score": score}
        return None

    def properties(self, rxcui):
        data = self._get_json(f"/rxcui/{rxcui}/properties.json")
        return data.get("properties") or None

    def ingredients(self, rxcui):
        """Ingredient concepts (term type IN) of any RxNorm concept, as [{'rxcui','name'}].

        For an ingredient concept this returns the concept itself.
        """
        data = self._get_json(f"/rxcui/{rxcui}/related.json", {"tty": "IN"})
        out = []
        for group in (data.get("relatedGroup") or {}).get("conceptGroup") or []:
            for prop in group.get("conceptProperties") or []:
                out.append({"rxcui": prop["rxcui"], "name": prop["name"]})
        return out

    def resolve(self, name):
        """Resolve a free-text drug name.

        Returns {'input', 'match': 'exact'|None, 'rxcui', 'ingredients': [...], 'suggestion'}.
        Only an exact (normalised) name match resolves. A name that does not match returns
        match None and no ingredients; if RxNav has a fuzzy candidate it is given under
        'suggestion' and is not used.
        """
        text = " ".join((name or "").split())
        result = {"input": text, "match": None, "rxcui": None, "ingredients": [], "suggestion": None}
        if not text:
            return result

        ids = self.rxcui_for_name(text)
        if not ids:
            cand = self.approximate(text)
            if cand:
                props = self.properties(cand["rxcui"]) or {}
                result["suggestion"] = {"rxcui": cand["rxcui"], "name": props.get("name") or cand.get("name"),
                                        "score": cand["score"]}
            return result

        # An exact name that maps to several concepts is only accepted when they all lead to the
        # same ingredient(s); otherwise it is ambiguous and is not resolved.
        resolved = []
        for rxcui in ids:
            ingredients = self.ingredients(rxcui)
            if ingredients:
                resolved.append((rxcui, ingredients))
        if not resolved:
            return result            # e.g. a dose form or a non-drug concept
        first = {i["rxcui"] for i in resolved[0][1]}
        if any({i["rxcui"] for i in ings} != first for _, ings in resolved[1:]):
            return result
        rxcui, ingredients = resolved[0]
        result.update(match="exact", rxcui=rxcui, ingredients=ingredients)
        return result

    def resolve_many(self, names, workers=8):
        """Resolve many names concurrently. Returns {name: result, or None if RxNav failed}."""
        unique = list(dict.fromkeys(names))

        def one(n):
            try:
                return n, self.resolve(n)
            except RxNavError:
                return n, None

        with ThreadPoolExecutor(max_workers=workers) as pool:
            return dict(pool.map(one, unique))
