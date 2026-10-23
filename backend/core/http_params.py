# backend/core/http_params.py
# Small helpers for turning untrusted query-string values into safe, bounded values.
#
# A raw query parameter is a string a client controls. int(raw) raises on anything that is not a
# number (turning "?limit=abc" into a 500), and even a valid number can be negative or enormous.
# bounded_int() never raises and always returns a value inside [minimum, maximum], so a route can
# pass it straight to a service as a row count, page size or offset.

def bounded_int(raw, default, minimum, maximum):
    """Parse raw into an int clamped to [minimum, maximum]; return default when it is missing or
    not a whole number. default is itself clamped, so the result is always within range."""
    def clamp(n):
        return max(minimum, min(maximum, n))

    if raw is None:
        return clamp(int(default))
    try:
        return clamp(int(str(raw).strip()))
    except (TypeError, ValueError):
        return clamp(int(default))
