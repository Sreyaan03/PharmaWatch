# backend/helpers/http_client.py
# ─────────────────────────────────────────────────────────────────────────────
# Patched requests client for openFDA caching and API-key appending
# ─────────────────────────────────────────────────────────────────────────────

import os
import requests
import requests_cache
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

FDA_API_KEY = os.getenv("FDA_API_KEY")

# Initialize requests-cache with SQLite backend, 24-hour expiration for FDA API queries
session = requests_cache.CachedSession(
    cache_name=os.path.join(os.path.dirname(__file__), "..", "http_cache"),
    backend='sqlite',
    expire_after=86400, # 24 hours
    allowable_codes=[200],
    urls_expire_after={'api.fda.gov': 86400, '*': requests_cache.DO_NOT_CACHE}
)

class PatchedRequests:
    @staticmethod
    def get(url, **kwargs):
        # Automatically append FDA API key if querying openFDA and key is valid
        if "api.fda.gov" in url and "api_key" not in url and FDA_API_KEY:
            connector = "&" if "?" in url else "?"
            url = f"{url}{connector}api_key={FDA_API_KEY}"
        
        # Use the cached session instead of original_requests and custom db
        return session.get(url, **kwargs)

http_requests = PatchedRequests()
