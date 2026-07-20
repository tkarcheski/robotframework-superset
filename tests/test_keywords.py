"""Tests for the Robot Framework keyword libraries (robotframework_superset.keywords).

Pure helpers and the dashboard library are tested with the standard library only.
The DB library's connectivity keywords exercise a temporary SQLite database and
skip when SQLAlchemy (the ``db`` extra) is not installed.
"""

from __future__ import annotations

import urllib.error

import pytest

from robotframework_superset.keywords import (
    SupersetDashboardKeywords,
    SupersetKeywords,
    mask_database_url,
    parse_dashboard_ids,
    superset_base_url,
)

# --------------------------------------------------------------------------
# Pure helpers
# --------------------------------------------------------------------------


def test_mask_database_url_masks_password() -> None:
    masked = mask_database_url("postgresql://rfs:secret@localhost:5433/rfs")
    assert masked == "postgresql://rfs:****@localhost:5433/rfs"
    assert "secret" not in masked


def test_mask_database_url_empty_is_not_set() -> None:
    assert mask_database_url("") == "NOT SET"


def test_mask_database_url_without_userinfo_passthrough() -> None:
    assert mask_database_url("sqlite:///tmp/events.db") == "sqlite:///tmp/events.db"


def test_mask_database_url_userinfo_without_password() -> None:
    # No ':' in the userinfo section -> nothing to mask, returned unchanged.
    assert mask_database_url("postgresql://localhost/rfs") == "postgresql://localhost/rfs"


def test_superset_base_url_prefers_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPERSET_URL", "http://superset.local:9000/")
    assert superset_base_url() == "http://superset.local:9000"


def test_superset_base_url_builds_from_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SUPERSET_URL", raising=False)
    monkeypatch.setenv("SUPERSET_PORT", "8090")
    assert superset_base_url() == "http://localhost:8090"


def test_superset_base_url_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SUPERSET_URL", raising=False)
    monkeypatch.delenv("SUPERSET_PORT", raising=False)
    assert superset_base_url() == "http://localhost:8088"


def test_parse_dashboard_ids_extracts_ints() -> None:
    payload = {"result": [{"id": 3, "name": "a"}, {"id": 5}, {"name": "no-id"}, "junk"]}
    assert parse_dashboard_ids(payload) == [3, 5]


def test_parse_dashboard_ids_handles_non_dict() -> None:
    assert parse_dashboard_ids([]) == []
    assert parse_dashboard_ids({"result": []}) == []


# --------------------------------------------------------------------------
# SupersetKeywords — DB connectivity (SQLite; skips without SQLAlchemy)
# --------------------------------------------------------------------------


def test_get_database_url_keyword_masks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    kw = SupersetKeywords(database_url="postgresql://u:pw@h/db")
    assert kw.get_database_url() == "postgresql://u:****@h/db"


def test_get_database_url_keyword_not_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert SupersetKeywords(database_url="").get_database_url() == "NOT SET"


def test_connect_requires_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValueError):
        SupersetKeywords(database_url="").connect_to_database()


def test_connect_and_row_counts(tmp_path) -> None:  # type: ignore[no-untyped-def]
    sqlalchemy = pytest.importorskip("sqlalchemy")
    url = f"sqlite:///{tmp_path / 'events.db'}"
    engine = sqlalchemy.create_engine(url)
    with engine.begin() as conn:
        conn.execute(sqlalchemy.text("CREATE TABLE events (id INTEGER PRIMARY KEY)"))
        conn.execute(sqlalchemy.text("INSERT INTO events (id) VALUES (1), (2)"))

    kw = SupersetKeywords(database_url=url)
    assert kw.connect_to_database()  # non-empty sqlite version string
    assert kw.get_table_row_counts() == {"events": 2}


def test_row_counts_missing_table(tmp_path) -> None:  # type: ignore[no-untyped-def]
    pytest.importorskip("sqlalchemy")
    url = f"sqlite:///{tmp_path / 'empty.db'}"
    assert SupersetKeywords(database_url=url).get_table_row_counts() == {"events": -1}


# --------------------------------------------------------------------------
# SupersetDashboardKeywords — REST API smoke (mocked transport)
# --------------------------------------------------------------------------


def _dash(monkeypatch: pytest.MonkeyPatch, responses: dict) -> SupersetDashboardKeywords:
    kw = SupersetDashboardKeywords(base_url="http://superset.test", username="u", password="p")

    def fake_http(method: str, path: str, **kwargs: object) -> "tuple[int, bytes]":
        return responses[path]

    monkeypatch.setattr(kw, "_http", fake_http)
    return kw


def test_list_dashboard_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    kw = _dash(
        monkeypatch,
        {
            "/api/v1/security/login": (200, b'{"access_token": "tok"}'),
            "/api/v1/dashboard/": (200, b'{"result": [{"id": 3}, {"id": 5}]}'),
        },
    )
    assert kw.list_dashboard_ids() == [3, 5]


def test_list_dashboard_ids_login_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    kw = _dash(monkeypatch, {"/api/v1/security/login": (401, b"{}")})
    with pytest.raises(RuntimeError):
        kw.list_dashboard_ids()


def test_dashboard_renders(monkeypatch: pytest.MonkeyPatch) -> None:
    kw = _dash(
        monkeypatch,
        {
            "/api/v1/security/login": (200, b'{"access_token": "tok"}'),
            "/api/v1/dashboard/3": (200, b"{}"),
            "/api/v1/dashboard/9": (404, b"{}"),
        },
    )
    assert kw.dashboard_renders(3) is True
    assert kw.dashboard_renders(9) is False


def test_get_health_status_and_reachable(monkeypatch: pytest.MonkeyPatch) -> None:
    kw = _dash(monkeypatch, {"/health": (200, b"OK")})
    assert kw.get_health_status() == "OK"
    assert kw.superset_is_reachable() is True


def test_superset_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    kw = SupersetDashboardKeywords(base_url="http://superset.test")

    def boom(method: str, path: str, **kwargs: object) -> "tuple[int, bytes]":
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(kw, "_http", boom)
    assert kw.superset_is_reachable() is False
