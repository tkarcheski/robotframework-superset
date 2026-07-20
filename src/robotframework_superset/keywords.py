"""Robot Framework keyword libraries for the robotframework-superset suites.

Two libraries, both free of any private ``rfc.*`` dependency, so the migrated
Robot suites (``robot/``) stand alone in this package:

- :class:`SupersetKeywords` — connectivity checks against the ``events``
  database that Superset reads. A public analogue of robotframework-chat's
  ``rfc.superset_keywords``, retargeted to this package's ``events`` schema
  (see ``docs/ARCHITECTURE.md`` §3): a masked URL for logging, a version
  handshake, and row counts for the tables this package owns.
- :class:`SupersetDashboardKeywords` — a framework-agnostic dashboard smoke
  check driven entirely by the Superset REST API. Dashboards are discovered
  (no hard-coded IDs) and each is confirmed to load without a 404/error, so no
  browser and no private browser/LLM keyword libraries are required.

The DB library imports SQLAlchemy lazily (the ``robotframework-superset[db]``
extra); the dashboard library uses only the standard library.
"""

from __future__ import annotations

import importlib
import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, List

from robot.api import logger
from robot.api.deco import keyword

# Tables this package's schema owns (docs/ARCHITECTURE.md §3). Kept as a tuple
# so it is easy to extend when the schema grows.
_OWNED_TABLES = ("events",)

_NOT_SET = "NOT SET"


def mask_database_url(url: str) -> str:
    """Return ``url`` with any password masked, or ``"NOT SET"`` when empty.

    ``postgresql://user:secret@host/db`` becomes
    ``postgresql://user:****@host/db``. Values without a userinfo section are
    returned unchanged. Never emit a raw ``DATABASE_URL`` to logs.
    """
    if not url:
        return _NOT_SET
    if "@" not in url:
        return url
    pre, rest = url.split("@", 1)
    if ":" not in pre:
        return url
    scheme_user = pre.rsplit(":", 1)[0]
    return f"{scheme_user}:****@{rest}"


def superset_base_url() -> str:
    """Return the Superset base URL from the environment (no trailing slash).

    ``SUPERSET_URL`` wins when set; otherwise ``http://localhost:<port>`` is
    built from ``SUPERSET_PORT`` (default ``8088``), matching ``.env.example``.
    """
    explicit = os.environ.get("SUPERSET_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")
    port = os.environ.get("SUPERSET_PORT", "8088").strip() or "8088"
    return f"http://localhost:{port}"


def parse_dashboard_ids(payload: Any) -> List[int]:
    """Extract dashboard ids from a Superset ``GET /api/v1/dashboard/`` body.

    The API returns ``{"result": [{"id": 1, ...}, ...]}``. Entries that are not
    objects or lack an integer ``id`` are ignored.
    """
    result = payload.get("result", []) if isinstance(payload, dict) else []
    ids: List[int] = []
    for item in result:
        if isinstance(item, dict) and isinstance(item.get("id"), int):
            ids.append(int(item["id"]))
    return ids


class SupersetKeywords:
    """Verify connectivity to the ``events`` database that Superset reads.

    Args:
        database_url: SQLAlchemy URL. Defaults to the ``DATABASE_URL`` env var.
            Only read; never logged unmasked.
    """

    ROBOT_LIBRARY_SCOPE = "SUITE"

    def __init__(self, database_url: str = "") -> None:
        self._database_url = database_url or os.environ.get("DATABASE_URL", "")
        self._engine: Any = None

    def _sqlalchemy(self) -> Any:
        try:
            return importlib.import_module("sqlalchemy")
        except ImportError as exc:  # pragma: no cover - depends on install extras
            raise RuntimeError(
                "SQLAlchemy is required for database checks. "
                "Install with: pip install 'robotframework-superset[db]'"
            ) from exc

    def _get_engine(self) -> Any:
        if self._engine is None:
            if not self._database_url:
                raise ValueError("DATABASE_URL is not configured. Set it in .env.")
            self._engine = self._sqlalchemy().create_engine(self._database_url)
        return self._engine

    @keyword("Get Database URL")
    def get_database_url(self) -> str:
        """Return ``DATABASE_URL`` with its password masked (``NOT SET`` if unset)."""
        return mask_database_url(self._database_url)

    @keyword("Connect To Database")
    def connect_to_database(self) -> str:
        """Open a connection and return the backend version string.

        Works against any SQLAlchemy backend; the connectivity suite targets the
        docker-compose PostgreSQL stack, but SQLite is supported for local runs.
        """
        engine = self._get_engine()
        sqlalchemy = self._sqlalchemy()
        query = (
            "SELECT sqlite_version()"
            if engine.dialect.name == "sqlite"
            else "SELECT version()"
        )
        with engine.connect() as conn:
            version = str(conn.execute(sqlalchemy.text(query)).scalar())
        logger.info(f"Connected to events database: {version}")
        return version

    @keyword("Get Table Row Counts")
    def get_table_row_counts(self) -> Dict[str, int]:
        """Return a ``{table: row_count}`` map for this package's tables.

        A count of ``-1`` means the table is missing or the query failed; each
        table is counted on its own connection so one failure never masks the
        others.
        """
        engine = self._get_engine()
        sqlalchemy = self._sqlalchemy()
        counts: Dict[str, int] = {}
        for table in _OWNED_TABLES:
            try:
                with engine.connect() as conn:
                    scalar = conn.execute(
                        sqlalchemy.text(f"SELECT COUNT(*) FROM {table}")  # noqa: S608
                    ).scalar()
                counts[table] = int(scalar or 0)
                logger.info(f"  {table}: {counts[table]} rows")
            except Exception as exc:  # noqa: BLE001 - report per-table, never abort
                logger.warn(f"  {table}: query failed - {exc}")
                counts[table] = -1
        return counts


class SupersetDashboardKeywords:
    """Framework-agnostic Superset dashboard smoke checks via the REST API.

    No browser and no hard-coded dashboard IDs: dashboards are discovered
    through ``GET /api/v1/dashboard/`` and each is confirmed to load without an
    error. Configuration comes from the environment (``SUPERSET_URL`` /
    ``SUPERSET_PORT`` and the admin credentials in ``.env.example``).

    Args:
        base_url: Superset base URL; defaults to :func:`superset_base_url`.
        username: Admin user; defaults to ``SUPERSET_ADMIN_USER`` /
            ``SUPERSET_USER`` / ``admin``.
        password: Admin password; defaults to ``SUPERSET_ADMIN_PASSWORD`` /
            ``SUPERSET_PASSWORD``. Never logged.
        timeout: Per-request HTTP timeout in seconds.
    """

    ROBOT_LIBRARY_SCOPE = "SUITE"

    def __init__(
        self,
        base_url: str = "",
        username: str = "",
        password: str = "",
        timeout: float = 30.0,
    ) -> None:
        self._base_url = (base_url or superset_base_url()).rstrip("/")
        self._username = username or os.environ.get(
            "SUPERSET_ADMIN_USER", os.environ.get("SUPERSET_USER", "admin")
        )
        self._password = password or os.environ.get(
            "SUPERSET_ADMIN_PASSWORD", os.environ.get("SUPERSET_PASSWORD", "")
        )
        self._timeout = float(timeout)
        self._token = ""

    # -- low-level HTTP (standard library only) --------------------------

    def _http(
        self,
        method: str,
        path: str,
        *,
        data: "bytes | None" = None,
        headers: "Dict[str, str] | None" = None,
    ) -> "tuple[int, bytes]":
        """Perform one HTTP request; return ``(status, body)``.

        HTTP error responses are returned (status + body) rather than raised so
        callers can assert on the status code; transport errors still raise.
        """
        req = urllib.request.Request(f"{self._base_url}{path}", data=data, method=method)
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        for key, value in (headers or {}).items():
            req.add_header(key, value)
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:  # noqa: S310
                return int(resp.status), resp.read()
        except urllib.error.HTTPError as exc:
            return int(exc.code), exc.read()

    def _login(self) -> str:
        if self._token:
            return self._token
        body = json.dumps(
            {
                "username": self._username,
                "password": self._password,
                "provider": "db",
                "refresh": True,
            }
        ).encode("utf-8")
        status, payload = self._http("POST", "/api/v1/security/login", data=body)
        if status != 200:
            raise RuntimeError(
                f"Superset login failed (HTTP {status}) at {self._base_url}. "
                "Check SUPERSET_ADMIN_USER / SUPERSET_ADMIN_PASSWORD."
            )
        token = json.loads(payload.decode("utf-8")).get("access_token", "")
        if not token:
            raise RuntimeError("Superset login returned no access_token.")
        self._token = str(token)
        return self._token

    def _auth_headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self._login()}"}

    # -- keywords --------------------------------------------------------

    @keyword("Superset Is Reachable")
    def superset_is_reachable(self) -> bool:
        """Return ``True`` if the Superset ``/health`` endpoint answers with 200."""
        try:
            status, _ = self._http("GET", "/health")
        except (urllib.error.URLError, OSError) as exc:
            logger.info(f"Superset not reachable at {self._base_url}: {exc}")
            return False
        return status == 200

    @keyword("Get Health Status")
    def get_health_status(self) -> str:
        """Return the body of the Superset ``/health`` endpoint (``OK`` when up)."""
        status, payload = self._http("GET", "/health")
        text = payload.decode("utf-8", "replace").strip()
        logger.info(f"Superset /health -> HTTP {status}: {text}")
        return text

    @keyword("List Dashboard Ids")
    def list_dashboard_ids(self) -> List[int]:
        """Discover dashboard ids via ``GET /api/v1/dashboard/`` (no hard-coded IDs)."""
        status, payload = self._http(
            "GET", "/api/v1/dashboard/", headers=self._auth_headers()
        )
        if status != 200:
            raise RuntimeError(f"Dashboard listing failed (HTTP {status}).")
        ids = parse_dashboard_ids(json.loads(payload.decode("utf-8")))
        logger.info(f"Discovered {len(ids)} dashboard(s): {ids}")
        return ids

    @keyword("Dashboard Renders")
    def dashboard_renders(self, dashboard_id: int) -> bool:
        """Return ``True`` if ``GET /api/v1/dashboard/<id>`` loads without an error."""
        status, _ = self._http(
            "GET",
            f"/api/v1/dashboard/{int(dashboard_id)}",
            headers=self._auth_headers(),
        )
        ok = status == 200
        logger.info(
            f"Dashboard {dashboard_id}: HTTP {status} ({'ok' if ok else 'error'})"
        )
        return ok
