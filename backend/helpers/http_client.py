# backend/helpers/http_client.py
# ─────────────────────────────────────────────────────────────────────────────
# Patched requests client for openFDA caching and API-key appending
# ─────────────────────────────────────────────────────────────────────────────

import os
import requests as original_requests
from dotenv import load_dotenv

load_dotenv()

FDA_API_KEY = os.getenv("FDA_API_KEY")

class PatchedRequests:
    @staticmethod
    def get(url, **kwargs):
        # Automatically append FDA API key if querying openFDA
        if "api.fda.gov" in url and "api_key" not in url:
            connector = "&" if "?" in url else "?"
            url = f"{url}{connector}api_key={FDA_API_KEY}"
        
        # Check database cache for FDA responses
        if "api.fda.gov" in url:
            try:
                from database import get_cached_fda_response
                cached = get_cached_fda_response(url)
                if cached is not None:
                    class MockResponse:
                        def __init__(self, json_data):
                            self.json_data = json_data
                            self.status_code = 200
                        def json(self):
                            return self.json_data
                    return MockResponse(cached)
            except Exception as e:
                print(f"[CACHE ERROR] {e}")

        resp = original_requests.get(url, **kwargs)

        # Cache successful openFDA responses
        if "api.fda.gov" in url and resp.status_code == 200:
            try:
                from database import cache_fda_response
                cache_fda_response(url, resp.json())
            except Exception as e:
                print(f"[CACHE SAVE ERROR] {e}")

        return resp

http_requests = PatchedRequests()
