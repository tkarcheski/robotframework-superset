"""Diagnose the environment-to-Superset event data path.

Checks every link in the chain: environment -> ports -> database connection ->
schema -> data -> Superset metadata -> Superset health. Run from the
repository root:

    python infra/scripts/diagnose_superset_db.py

Or with an explicit DATABASE_URL:

    DATABASE_URL=postgresql://rfs:changeme@localhost:5433/rfs \\
        python infra/scripts/diagnose_superset_db.py

Passwords are always masked; no secret material is printed. Output uses
[OK]/[WARN]/[FAIL] labels (colored only on a terminal) and failures come with
remediation hints. Exit code is non-zero when any check fails.
"""

from __future__ import annotations

import os
import re
import socket
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

# Superset database connection created by infra/superset/bootstrap_dashboards.py.
EVENTS_DB_NAME = "Robot Framework Events"

_REPO_ROOT = Path(__file__).resolve().parents[2]

_COLORS = {"OK": "\033[92m", "WARN": "\033[93m", "FAIL": "\033[91m"}
_RESET = "\033[0m"

# Result counters for the final summary (labels match the printed tags).
_counts: dict[str, int] = {"OK": 0, "WARN": 0, "FAIL": 0}


def _report(label: str, message: str, hint: str | None = None) -> None:
    _counts[label] += 1
    tag = f"[{label}]"
    if sys.stdout.isatty():
        tag = f"{_COLORS[label]}{tag}{_RESET}"
    print(f"{tag} {message}")
    if hint:
        print(f"       -> {hint}")


def ok(message: str) -> None:
    _report("OK", message)


def warn(message: str, hint: str | None = None) -> None:
    _report("WARN", message, hint)


def fail(message: str, hint: str | None = None) -> None:
    _report("FAIL", message, hint)


def load_dotenv(path: Path = _REPO_ROOT / ".env") -> None:
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


def check_environment() -> str:
    """Report .env / variable presence; return DATABASE_URL ('' when unset)."""
    env_file = _REPO_ROOT / ".env"
    if env_file.exists():
        ok(f"Environment: .env present at {env_file}")
    else:
        warn(
            "Environment: .env is missing",
            "Copy .env.example to .env and set credentials (or export them).",
        )

    database_url = os.getenv("DATABASE_URL", "")
    if database_url:
        ok(f"Environment: DATABASE_URL={mask_database_url(database_url)}")
    else:
        fail(
            "Environment: DATABASE_URL is not set",
            "Example: DATABASE_URL=postgresql://rfs:changeme@localhost:5433/rfs",
        )

    if os.getenv("POSTGRES_PASSWORD"):
        ok("Environment: POSTGRES_PASSWORD is set")
    else:
        warn(
            "Environment: POSTGRES_PASSWORD is not set",
            "docker-compose falls back to the insecure default 'changeme'.",
        )
    return database_url


def _parse_db_host_port(url: str) -> tuple[str, int]:
    """Extract (host, port) from a DATABASE_URL, falling back to defaults."""
    default_port = int(os.getenv("POSTGRES_PORT", "5433"))
    if url and "@" in url:
        host_port = url.split("@", 1)[1].split("/", 1)[0]
        if ":" in host_port:
            host, port_text = host_port.rsplit(":", 1)
            try:
                return host, int(port_text)
            except ValueError:
                return host, default_port
        return host_port, default_port
    return os.getenv("DATABASE_HOST", "localhost"), default_port


def check_ports(database_url: str) -> None:
    """Socket-level reachability of the database and Superset ports."""
    db_host, db_port = _parse_db_host_port(database_url)
    try:
        socket.create_connection((db_host, db_port), timeout=3).close()
        ok(f"Port: database {db_host}:{db_port} is accepting connections")
    except OSError as exc:
        fail(
            f"Port: database {db_host}:{db_port} is not reachable ({exc})",
            "Is PostgreSQL up? Check: make ps (host port 5433; Docker-internal 5432).",
        )

    superset_port = int(os.getenv("SUPERSET_PORT", "8088"))
    try:
        socket.create_connection(("localhost", superset_port), timeout=3).close()
        ok(f"Port: Superset localhost:{superset_port} is accepting connections")
    except OSError:
        warn(
            f"Port: Superset localhost:{superset_port} is not reachable",
            "Is the stack up? Try: make up (then: make ps).",
        )


def check_database(url: str) -> bool:
    from sqlalchemy import create_engine, inspect, text

    engine = create_engine(url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            ok("Database connection")

            inspector = inspect(connection)
            if "events" not in inspector.get_table_names():
                fail(
                    "Schema: events table is missing",
                    "Run: make bootstrap (or write one event through the DB sink).",
                )
                return False
            columns = {column["name"] for column in inspector.get_columns("events")}
            missing = REQUIRED_COLUMNS - columns
            if missing:
                fail(
                    f"Schema: missing columns {', '.join(sorted(missing))}",
                    "Re-run: make bootstrap (never drops, only creates).",
                )
                return False
            ok("Schema: events table and required columns")

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
                fail(f"Data: {null_clocks} event(s) violate the dual-clock invariant")
                return False
            if count == 0:
                warn(
                    "Data: events table is empty",
                    "No events written yet; check DATABASE_URL for the sink process.",
                )
            else:
                ok(f"Data: {count} event row(s), dual-clock invariant intact")
            return True
    except Exception as exc:  # noqa: BLE001 - diagnostic boundary
        fail(
            f"Database: {safe_error(exc)}",
            "Is PostgreSQL up? Try: make up (host port 5433; Docker-internal 5432).",
        )
        return False
    finally:
        engine.dispose()


def check_superset_metadata(database_url: str) -> None:
    """Verify the bootstrap-created Superset database connection is sane."""
    from sqlalchemy import create_engine, text

    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            row = connection.execute(
                text("SELECT id, sqlalchemy_uri FROM dbs WHERE database_name = :name"),
                {"name": EVENTS_DB_NAME},
            ).fetchone()
    except Exception:  # noqa: BLE001 - the dbs table may not exist yet
        warn(
            "Superset metadata: cannot query the 'dbs' table",
            "Superset may not be initialized yet. Run: make up && make bootstrap",
        )
        return
    finally:
        engine.dispose()

    if row is None:
        fail(
            f"Superset metadata: no '{EVENTS_DB_NAME}' database connection",
            "Run: make bootstrap",
        )
        return
    database_id, uri = row
    masked = mask_database_url(str(uri))
    if "@postgres:" in str(uri):
        ok(
            f"Superset metadata: '{EVENTS_DB_NAME}' (id={database_id}) "
            f"uses the Docker-internal host: {masked}"
        )
    elif "localhost" in str(uri) or "127.0.0.1" in str(uri):
        fail(
            f"Superset metadata: '{EVENTS_DB_NAME}' points at {masked}",
            "Superset runs in Docker and needs postgres:5432, not localhost. "
            "Run: make bootstrap",
        )
    else:
        warn(
            f"Superset metadata: '{EVENTS_DB_NAME}' uses {masked}",
            "Verify this host resolves from inside the Superset container.",
        )


def check_superset(url: str) -> bool:
    health_url = f"{url.rstrip('/')}/health"
    try:
        with urlopen(health_url, timeout=5) as response:  # noqa: S310 - operator URL
            body = response.read(128).decode("utf-8", errors="replace").strip()
            if 200 <= response.status < 300:
                ok(f"Superset health: {body or response.status}")
                return True
    except (OSError, URLError) as exc:
        fail(
            f"Superset health: {exc}",
            "Is the superset service running? Check: make ps (or make logs).",
        )
        return False
    fail("Superset health: unexpected response")
    return False


def main() -> int:
    load_dotenv()
    print("Superset event pipeline diagnostic")
    print("=" * 50)

    database_url = check_environment()
    check_ports(database_url)
    if database_url:
        if check_database(database_url):
            check_superset_metadata(database_url)
        else:
            warn("Superset metadata: skipped because the database is unreachable")

    port = os.getenv("SUPERSET_PORT", "8088")
    check_superset(os.getenv("SUPERSET_URL", f"http://localhost:{port}"))

    print()
    print(f"Summary: {_counts['OK']} OK, {_counts['WARN']} WARN, {_counts['FAIL']} FAIL")
    if _counts["FAIL"]:
        print("Verdict: FAIL - fix the failures above and re-run.")
        return 1
    print("Verdict: OK" + (" (with warnings)" if _counts["WARN"] else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
