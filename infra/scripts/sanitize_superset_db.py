"""Remove event data while preserving Superset dashboards and configuration."""

from __future__ import annotations

import argparse
import os
import sys

try:
    from .diagnose_superset_db import load_dotenv, mask_database_url
except ImportError:  # Direct script execution adds this directory to sys.path.
    from diagnose_superset_db import load_dotenv, mask_database_url


def sanitize(database_url: str, *, confirmed: bool = False) -> int:
    from sqlalchemy import create_engine, text

    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            count = int(connection.scalar(text("SELECT COUNT(*) FROM events")) or 0)
        print(f"Database: {mask_database_url(database_url)}")
        print(f"Current events: {count}")
        if count == 0:
            print("Nothing to sanitize.")
            return 0
        if not confirmed:
            answer = input(f"Permanently delete {count} event row(s)? [y/N]: ").strip().lower()
            if answer not in {"y", "yes"}:
                print("Aborted.")
                return 1
        with engine.begin() as connection:
            if connection.dialect.name == "postgresql":
                connection.execute(text("TRUNCATE TABLE events RESTART IDENTITY"))
            else:
                connection.execute(text("DELETE FROM events"))
        print(f"Sanitized {count} event row(s); dashboards and charts were preserved.")
        return 0
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        print(f"Sanitize failed: {mask_database_url(str(exc))}")
        return 1
    finally:
        engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-y", "--yes", action="store_true", help="skip confirmation")
    args = parser.parse_args()
    load_dotenv()
    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        print("DATABASE_URL is not set.")
        return 1
    return sanitize(database_url, confirmed=args.yes)


if __name__ == "__main__":
    sys.exit(main())
