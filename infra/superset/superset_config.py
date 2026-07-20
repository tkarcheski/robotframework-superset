"""Apache Superset configuration for robotframework-superset.

Points Superset's metadata database at the same PostgreSQL instance that holds
the event tables written by the DB sink. All credentials come from environment
variables set by docker-compose — no secrets are baked into this file.
"""

import os
from urllib.parse import quote_plus

_pg_user = quote_plus(os.getenv("POSTGRES_USER", "rfs"))
_pg_pass = quote_plus(os.getenv("POSTGRES_PASSWORD", "changeme"))
_pg_db = quote_plus(os.getenv("POSTGRES_DB", "rfs"))
_pg_port = os.getenv("POSTGRES_INTERNAL_PORT", "5432")

# Superset metadata database (its own tables, same PG instance).
SQLALCHEMY_DATABASE_URI = f"postgresql://{_pg_user}:{_pg_pass}@postgres:{_pg_port}/{_pg_db}"

# Replace via SUPERSET_SECRET_KEY; the default is a placeholder, not a secret.
SECRET_KEY = os.getenv("SUPERSET_SECRET_KEY", "change-me-generate-a-random-secret")

# Redis metadata/filter cache (longer TTL is fine).
CACHE_CONFIG = {
    "CACHE_TYPE": "RedisCache",
    "CACHE_DEFAULT_TIMEOUT": 300,
    "CACHE_KEY_PREFIX": "superset_",
    "CACHE_REDIS_HOST": "redis",
    "CACHE_REDIS_PORT": 6379,
    "CACHE_REDIS_DB": 0,
}

# Data query cache — short TTL so freshly-written events appear quickly.
DATA_CACHE_CONFIG = {
    **CACHE_CONFIG,
    "CACHE_DEFAULT_TIMEOUT": 30,
    "CACHE_KEY_PREFIX": "superset_data_",
}

# flask-limiter rate-limit state on its own Redis DB.
RATELIMIT_STORAGE_URI = "redis://redis:6379/2"

FEATURE_FLAGS = {
    "ENABLE_TEMPLATE_PROCESSING": True,
}

# Keep browser protections enabled; bootstrap runs inside the Flask app context.
WTF_CSRF_ENABLED = True

# Allow embedding in iframes.
SESSION_COOKIE_SAMESITE = "Lax"
ENABLE_CORS = True
