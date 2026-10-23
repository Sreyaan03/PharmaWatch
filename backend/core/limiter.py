# backend/core/limiter.py
# One shared rate limiter so route modules can apply per-endpoint limits (e.g. login brute-force
# protection) without importing the app. Initialised in app.py via limiter.init_app(app).
# Falls back to a no-op when flask-limiter is not installed.

try:
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address
    limiter = Limiter(key_func=get_remote_address)
except ImportError:
    class _MockLimiter:
        def init_app(self, *a, **k):
            pass

        def limit(self, *a, **k):
            def decorator(f):
                return f
            return decorator

        def exempt(self, f):
            return f

    limiter = _MockLimiter()
