"""Diagnose Superset database connectivity and the event pipeline.

Checks every link in the chain: environment -> connection -> schema -> data ->
Superset. Run from the repository root:

    python infra/scripts/diagnose_superset_db.py

Or with an explicit DATABASE_URL:

    DATABASE_URL=postgresql://rfs:changeme@localhost:5433/rfs \\
        python infra/scripts/diagnose_superset_db.py

Passwords are always masked in output; no secret material is ever printed.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Superset database connection name created by bootstrap_dashboards.py.
EVENTS_DB_NAME = "RF + LLM Events"

# The generic event table this stack is built around.
EXPECTED_TABLES = ["events"]

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# ---------------------------------------------------------------------------
# ANSI helpers
# ---------------------------------------------------------------------------
_GREEN = "\033[92m"
_RED = "\033[91m"
_YELLOW = "\033[93m"
_BOLD = "\033[1m"
_RESET = "\033[0m"


def ok(msg: str) -> None:
    print(f"  {_GREEN}OK{_RESET}  {msg}")


def fail(msg: str) -> None:
    print(f"  {_RED}FAIL{_RESET}  {msg}")


def warn(msg: str) -> None:
    print(f"  {_YELLOW}WARN{_RESET}  {msg}")


def heading(msg: str) -> None:
    print(f"\n{_BOLD}-- {msg} --{_RESET}")


def mask_database_url(url: str) -> str:
    """Return ``url`` with any password replaced by ``****``.

    Handles ``scheme://user:pass@host:port/db``; a URL without credentials is
    returned unchanged.
    """
    if "@" not in url:
        return url
    pre, rest = url.split("@", 1)
    if ":" in pre:
        scheme_user = pre.rsplit(":", 1)[0]
        return f"{scheme_user}:****@{rest}"
    return url


def load_dotenv_if_present() -> None:
    """Load KEY=VALUE lines from a repo-root .env into the environment.

    Dependency-free (no python-dotenv). Existing environment variables win, so
    an explicit export always overrides the file.
    """
    env_file = _REPO_ROOT / ".env"
    if not env_file.exists():
        return
    for raw in env_file.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------
def check_env() -> str | None:
    """Check environment variables and .env file."""
    heading("Environment")

    env_file = _REPO_ROOT / ".env"
    if env_file.exists():
        ok(f".env file exists at {env_file}")
    else:
        fail(
            f".env file missing at {env_file}\n"
            "        -> Copy .env.example to .env and configure DATABASE_URL."
        )

    url = os.getenv("DATABASE_URL")
    if url:
        ok(f"DATABASE_URL is set: {mask_database_url(url)}")
    else:
        fail(
            "DATABASE_URL is not set.\n"
            "        -> The DB sink and these scripts need it to reach PostgreSQL.\n"
            "        -> Example: DATABASE_URL=postgresql://rfs:changeme@localhost:5433/rfs"
        )

    if os.getenv("POSTGRES_PASSWORD"):
        ok("POSTGRES_PASSWORD is set")
    else:
        warn(
            "POSTGRES_PASSWORD is not set.\n"
            "        -> docker-compose and superset_config.py both default to 'changeme'."
        )

    return url


def check_connection(url: str) -> bool:
    """Try connecting to the database."""
    heading("Database Connection")

    try:
        from sqlalchemy import create_engine, text
    except ImportError:
        fail('sqlalchemy not installed.\n        -> Run: pip install -e ".[db]"')
        return False

    try:
        engine = create_engine(url, connect_args={"connect_timeout": 5})
        with engine.connect() as conn:
            version = conn.execute(text("SELECT version()")).scalar()
            ok(f"Connected to PostgreSQL: {version}")
        engine.dispose()
        return True
    except Exception as exc:  # noqa: BLE001 - report any failure to the user
        fail(
            f"Cannot connect to database: {exc}\n"
            "        -> Is PostgreSQL running? Try: make up (or docker compose ps)\n"
            "        -> Host uses port 5433; Docker-internal uses 5432."
        )
        return False


def check_schema(url: str) -> bool:
    """Check that the events table and Superset metadata tables exist."""
    heading("Schema")

    from sqlalchemy import create_engine, inspect

    engine = create_engine(url, connect_args={"connect_timeout": 5})
    all_tables = inspect(engine).get_table_names()
    all_ok = True

    for table in EXPECTED_TABLES:
        if table in all_tables:
            ok(f"Table '{table}' exists")
        else:
            fail(
                f"Table '{table}' is MISSING\n"
                "        -> Run: make bootstrap (creates it), or write an event."
            )
            all_ok = False

    superset_tables = [t for t in all_tables if t.startswith("ab_") or t == "dashboards"]
    if superset_tables:
        ok(f"Superset metadata tables found ({len(superset_tables)} tables)")
    else:
        warn(
            "No Superset metadata tables found.\n"
            "        -> Has the stack initialized? Run: make up && make bootstrap"
        )

    engine.dispose()
    return all_ok


def check_data(url: str) -> None:
    """Check row counts in the events table."""
    heading("Data")

    from sqlalchemy import create_engine, text

    engine = create_engine(url, connect_args={"connect_timeout": 5})
    with engine.connect() as conn:
        for table in EXPECTED_TABLES:
            try:
                count = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()  # noqa: S608
                if count and count > 0:
                    ok(f"{table}: {count} rows")
                else:
                    warn(f"{table}: 0 rows (empty)")
            except Exception as exc:  # noqa: BLE001 - report per-table failures
                fail(f"{table}: query failed -- {exc}")
    engine.dispose()


def check_superset_database_connection(url: str) -> None:
    """Check the Superset 'Database' object exists and its URI is correct."""
    heading("Superset Database Connection")

    from sqlalchemy import create_engine, text

    engine = create_engine(url, connect_args={"connect_timeout": 5})
    with engine.connect() as conn:
        try:
            row = conn.execute(
                text(
                    "SELECT id, sqlalchemy_uri FROM dbs WHERE database_name = :name"
                ),
                {"name": EVENTS_DB_NAME},
            ).fetchone()
            if row:
                db_id, uri = row
                ok(f"Superset database connection '{EVENTS_DB_NAME}' exists (id={db_id})")
                masked = mask_database_url(uri)
                if "@postgres:" in uri:
                    ok(f"URI uses the Docker-internal host: {masked}")
                elif "localhost" in uri:
                    warn(
                        f"URI uses localhost: {masked}\n"
                        "        -> Works from the host but NOT from inside Docker;\n"
                        "        -> Superset runs in Docker and needs 'postgres:5432'."
                    )
                else:
                    warn(f"URI: {masked} -- verify it resolves from Superset's container")
            else:
                fail(
                    f"No '{EVENTS_DB_NAME}' database connection in Superset.\n"
                    "        -> Run: make bootstrap"
                )
        except Exception:  # noqa: BLE001 - dbs table may not exist yet
            warn(
                "Cannot query Superset's 'dbs' table.\n"
                "        -> Superset metadata may not exist yet. Run: make bootstrap"
            )
    engine.dispose()


def _parse_db_host_port(url: str) -> tuple[str, int]:
    """Extract (host, port) from a DATABASE_URL, falling back to defaults."""
    if url and "@" in url:
        after_at = url.split("@", 1)[1]
        host_port = after_at.split("/", 1)[0]
        if ":" in host_port:
            host, port_str = host_port.rsplit(":", 1)
            try:
                return host, int(port_str)
            except ValueError:
                return host, int(os.getenv("POSTGRES_PORT", "5433"))
        return host_port, int(os.getenv("POSTGRES_PORT", "5433"))
    return os.getenv("DATABASE_HOST", "localhost"), int(os.getenv("POSTGRES_PORT", "5433"))


def check_port_mapping(url: str | None) -> None:
    """Verify the database and Superset ports are reachable."""
    heading("Port Mapping")

    import socket

    db_host, db_port = _parse_db_host_port(url or "")
    try:
        socket.create_connection((db_host, db_port), timeout=3).close()
        ok(f"{db_host}:{db_port} is accepting connections")
    except OSError:
        fail(
            f"{db_host}:{db_port} is NOT reachable.\n"
            f"        -> Is PostgreSQL up? Check: docker compose ps postgres"
        )

    superset_port = int(os.getenv("SUPERSET_PORT", "8088"))
    try:
        socket.create_connection(("localhost", superset_port), timeout=3).close()
        ok(f"localhost:{superset_port} (Superset) is accepting connections")
    except OSError:
        warn(f"localhost:{superset_port} (Superset) is NOT reachable")


def main() -> None:
    load_dotenv_if_present()

    print(f"{_BOLD}Superset Database Diagnostic{_RESET}")
    print("=" * 50)

    url = check_env()
    check_port_mapping(url)

    if not url:
        print(f"\n{_RED}Cannot continue without DATABASE_URL.{_RESET}")
        sys.exit(1)

    if not check_connection(url):
        print(f"\n{_RED}Cannot continue without a database connection.{_RESET}")
        sys.exit(1)

    check_schema(url)
    check_data(url)
    check_superset_database_connection(url)

    heading("Summary")
    print(
        "If the events table is empty, no events are being written.\n"
        "Common causes:\n"
        "  1. DATABASE_URL not set for the process running the DB sink\n"
        "  2. The sink swallowed an error (skip-and-log); check its warnings\n"
        "\n"
        "If data exists but Superset shows nothing:\n"
        "  1. Flush the Redis cache: make cache-flush\n"
        "  2. Re-run bootstrap: make bootstrap\n"
    )


if __name__ == "__main__":
    main()
