"""Diagnose the environment-to-Superset event data path."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

REQUIRED_COLUMNS = {
    "id",
    "event_type",
    "source",
    "wall_clock",
    "monotonic_ns",
    "level",
    "message",
    "duration_ns",
    "payload",
}


def load_dotenv(path: Path = Path(".env")) -> None:
    """Load simple KEY=VALUE entries without overriding exported values."""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def mask_database_url(url: str) -> str:
    """Hide a URL password while retaining enough information to diagnose."""
    return re.sub(r"(://[^:/@]+:)[^@]*(@)", r"\1***\2", url)


def safe_error(error: Exception) -> str:
    return mask_database_url(str(error))


def check_database(url: str) -> bool:
    from sqlalchemy import create_engine, inspect, text

    engine = create_engine(url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            print("[OK] Database connection")

            inspector = inspect(connection)
            if "events" not in inspector.get_table_names():
                print("[FAIL] Schema: events table is missing")
                return False
            columns = {column["name"] for column in inspector.get_columns("events")}
            missing = REQUIRED_COLUMNS - columns
            if missing:
                print(f"[FAIL] Schema: missing columns {', '.join(sorted(missing))}")
                return False
            print("[OK] Schema: events table and required columns")

            count = int(connection.scalar(text("SELECT COUNT(*) FROM events")) or 0)
            null_clocks = int(
                connection.scalar(
                    text(
                        "SELECT COUNT(*) FROM events "
                        "WHERE wall_clock IS NULL OR monotonic_ns IS NULL"
                    )
                )
                or 0
            )
            if null_clocks:
                print(f"[FAIL] Data: {null_clocks} event(s) violate the dual-clock invariant")
                return False
            print(f"[OK] Data: {count} event row(s), dual-clock invariant intact")
            return True
    except Exception as exc:  # noqa: BLE001 - diagnostic boundary
        print(f"[FAIL] Database: {safe_error(exc)}")
        return False
    finally:
        engine.dispose()


def check_superset(url: str) -> bool:
    health_url = f"{url.rstrip('/')}/health"
    try:
        with urlopen(health_url, timeout=5) as response:  # noqa: S310 - operator URL
            body = response.read(128).decode("utf-8", errors="replace").strip()
            if 200 <= response.status < 300:
                print(f"[OK] Superset health: {body or response.status}")
                return True
    except (OSError, URLError) as exc:
        print(f"[FAIL] Superset health: {exc}")
        return False
    print("[FAIL] Superset health: unexpected response")
    return False


def main() -> int:
    load_dotenv()
    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        print("[FAIL] Environment: DATABASE_URL is not set")
        return 1
    print(f"[OK] Environment: DATABASE_URL={mask_database_url(database_url)}")

    port = os.getenv("SUPERSET_PORT", "8088")
    superset_url = os.getenv("SUPERSET_URL", f"http://localhost:{port}")
    database_ok = check_database(database_url)
    superset_ok = check_superset(superset_url)
    return 0 if database_ok and superset_ok else 1


if __name__ == "__main__":
    sys.exit(main())
