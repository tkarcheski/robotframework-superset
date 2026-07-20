"""Sanitize (truncate) the events table in the Superset PostgreSQL database.

Removes all rows from ``events`` while preserving the schema and every Superset
object (database connection, datasets, charts, dashboards).

Usage:
    python infra/scripts/sanitize_superset_db.py
    python infra/scripts/sanitize_superset_db.py --yes   # skip confirmation

The database URL is never printed; passwords are masked if any URL is shown.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Data tables to truncate. Only the generic events table holds row data; the
# Superset metadata tables are intentionally left untouched.
TABLES = ["events"]

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# ---------------------------------------------------------------------------
# ANSI helpers
# ---------------------------------------------------------------------------
_GREEN = "\033[92m"
_RED = "\033[91m"
_YELLOW = "\033[93m"
_BOLD = "\033[1m"
_RESET = "\033[0m"


def mask_database_url(url: str) -> str:
    """Return ``url`` with any password replaced by ``****``."""
    if "@" not in url:
        return url
    pre, rest = url.split("@", 1)
    if ":" in pre:
        scheme_user = pre.rsplit(":", 1)[0]
        return f"{scheme_user}:****@{rest}"
    return url


def _load_dotenv_if_present() -> None:
    """Load KEY=VALUE lines from a repo-root .env (dependency-free)."""
    env_file = _REPO_ROOT / ".env"
    if not env_file.exists():
        return
    for raw in env_file.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _get_database_url() -> str | None:
    """Resolve DATABASE_URL from the environment or a repo-root .env file."""
    _load_dotenv_if_present()
    return os.getenv("DATABASE_URL")


def _get_row_counts(url: str) -> dict[str, int]:
    """Return row counts per table (-1 if the table is missing)."""
    from sqlalchemy import create_engine, text

    engine = create_engine(url, connect_args={"connect_timeout": 5})
    counts: dict[str, int] = {}
    with engine.connect() as conn:
        for table in TABLES:
            try:
                counts[table] = conn.execute(
                    text(f"SELECT COUNT(*) FROM {table}")  # noqa: S608
                ).scalar() or 0
            except Exception:  # noqa: BLE001 - missing table -> sentinel
                counts[table] = -1
    engine.dispose()
    return counts


def _truncate_tables(url: str) -> None:
    """Truncate all event data tables in one CASCADE statement."""
    from sqlalchemy import create_engine, text

    engine = create_engine(url, connect_args={"connect_timeout": 5})
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE TABLE {', '.join(TABLES)} RESTART IDENTITY CASCADE"))
    engine.dispose()


def main() -> None:
    skip_confirm = "--yes" in sys.argv or "-y" in sys.argv

    print(f"{_BOLD}Superset Database Sanitize{_RESET}")
    print("=" * 50)

    url = _get_database_url()
    if not url:
        print(f"\n{_RED}DATABASE_URL is not set.{_RESET}")
        print("Set it in .env or export it in your shell.")
        print("Example: DATABASE_URL=postgresql://rfs:changeme@localhost:5433/rfs")
        sys.exit(1)

    print(f"\n{_BOLD}Target:{_RESET} {mask_database_url(url)}")
    print(f"\n{_BOLD}Current data:{_RESET}")
    counts = _get_row_counts(url)
    total_rows = 0
    for table, count in counts.items():
        if count < 0:
            print(f"  {_RED}{table}: table not found{_RESET}")
        elif count == 0:
            print(f"  {_YELLOW}{table}: 0 rows (already empty){_RESET}")
        else:
            print(f"  {table}: {count:,} rows")
            total_rows += count

    if total_rows == 0:
        print(f"\n{_GREEN}Nothing to sanitize -- all tables are already empty.{_RESET}")
        sys.exit(0)

    print(f"\n{_RED}{_BOLD}This will permanently delete all {total_rows:,} rows.{_RESET}")
    print("Superset dashboards, charts, and configuration will be preserved.")

    if not skip_confirm:
        try:
            answer = input(f"\n{_BOLD}Are you sure? [y/N]: {_RESET}").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            sys.exit(1)
        if answer not in ("y", "yes"):
            print("Aborted.")
            sys.exit(1)

    print(f"\n{_BOLD}Sanitizing...{_RESET}")
    try:
        _truncate_tables(url)
    except Exception as exc:  # noqa: BLE001 - report and exit non-zero
        print(f"{_RED}Failed to truncate tables: {exc}{_RESET}")
        sys.exit(1)

    print(f"\n{_BOLD}After sanitize:{_RESET}")
    for table, count in _get_row_counts(url).items():
        print(f"  {_GREEN}{table}: {count} rows{_RESET}")

    print(f"\n{_GREEN}Sanitize complete.{_RESET} Flush the Redis cache to refresh:")
    print("  make cache-flush")


if __name__ == "__main__":
    main()
