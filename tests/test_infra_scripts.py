"""Tests for the portable logic in the infra scripts.

The docker/Superset bits require a running stack, but the pure helpers
(password masking, URL parsing, DDL splitting) and the dashboard definitions
are unit-testable here. Modules are loaded by path because ``infra/`` is not an
importable package.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

_INFRA = Path(__file__).resolve().parent.parent / "infra"


def _load(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


diagnose = _load(_INFRA / "scripts" / "diagnose_superset_db.py")
sanitize = _load(_INFRA / "scripts" / "sanitize_superset_db.py")
bootstrap = _load(_INFRA / "superset" / "bootstrap_dashboards.py")


# ---------------------------------------------------------------------------
# Password masking (never leak secrets in output)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("mask", [diagnose.mask_database_url, sanitize.mask_database_url])
def test_mask_hides_password(mask) -> None:
    masked = mask("postgresql://rfs:s3cret@localhost:5433/rfs")
    assert "s3cret" not in masked
    assert masked == "postgresql://rfs:****@localhost:5433/rfs"


@pytest.mark.parametrize("mask", [diagnose.mask_database_url, sanitize.mask_database_url])
def test_mask_passthrough_without_credentials(mask) -> None:
    assert mask("postgresql://localhost:5433/rfs") == "postgresql://localhost:5433/rfs"


# ---------------------------------------------------------------------------
# DATABASE_URL host/port parsing
# ---------------------------------------------------------------------------
def test_parse_db_host_port_from_url() -> None:
    host, port = diagnose._parse_db_host_port("postgresql://u:p@db.example:6000/x")
    assert host == "db.example"
    assert port == 6000


def test_parse_db_host_port_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("POSTGRES_PORT", raising=False)
    host, port = diagnose._parse_db_host_port("")
    assert host == "localhost"
    assert port == 5433


# ---------------------------------------------------------------------------
# Bootstrap: DDL splitting and internal URI
# ---------------------------------------------------------------------------
def test_split_ddl_drops_comments_and_blanks() -> None:
    stmts = bootstrap._split_ddl(
        """
        -- a comment
        CREATE TABLE IF NOT EXISTS events (id BIGSERIAL);

        -- only comment here
        CREATE INDEX IF NOT EXISTS i ON events (id);
        """
    )
    assert len(stmts) == 2
    assert stmts[0].startswith("CREATE TABLE")
    assert stmts[1].startswith("CREATE INDEX")
    assert all("--" not in s for s in stmts)


def test_get_database_uri_is_docker_internal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_USER", "rfs")
    monkeypatch.setenv("POSTGRES_PASSWORD", "changeme")
    monkeypatch.setenv("POSTGRES_DB", "rfs")
    monkeypatch.delenv("POSTGRES_HOST_INTERNAL", raising=False)
    monkeypatch.setenv("POSTGRES_INTERNAL_PORT", "5432")
    assert bootstrap._get_database_uri() == "postgresql://rfs:changeme@postgres:5432/rfs"


# ---------------------------------------------------------------------------
# Bootstrap: dashboard definitions are internally consistent and generic
# ---------------------------------------------------------------------------
def test_every_chart_datasource_is_defined() -> None:
    known = set(bootstrap._VIRTUAL_DATASETS) | {"events"}
    for chart in bootstrap._CHART_DEFS:
        assert chart["datasource_id_key"] in known, chart["slice_name"]


def test_layout_only_references_defined_charts() -> None:
    chart_names = {c["slice_name"] for c in bootstrap._CHART_DEFS}
    for section in bootstrap._LAYOUT_SECTIONS:
        for spec in section["charts"]:
            assert spec["name"] in chart_names, spec["name"]


def test_chart_names_are_unique() -> None:
    names = [c["slice_name"] for c in bootstrap._CHART_DEFS]
    assert len(names) == len(set(names))


def test_datasets_query_the_events_table_not_rfc_schema() -> None:
    for name, sql in bootstrap._VIRTUAL_DATASETS.items():
        assert "FROM events" in sql, name
        # Guard against RFC-specific schema leaking into the generic package.
        lowered = sql.lower()
        assert "test_runs" not in lowered, name
        assert "test_results" not in lowered, name
        assert "hostname" not in lowered, name


def test_filter_targets_use_placeholder() -> None:
    for fconf in bootstrap._FILTER_CONFIGS:
        for target in fconf.get("targets", []):
            assert target.get("datasetId") == "__EVENTS_DS_ID__"


def test_json_metadata_substitutes_dataset_id() -> None:
    meta = bootstrap._build_json_metadata(events_dataset_id=42)
    for fconf in meta["native_filter_configuration"]:
        for target in fconf.get("targets", []):
            assert target["datasetId"] == 42
