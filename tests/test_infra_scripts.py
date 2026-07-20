"""Contract tests for the extended Superset dashboard content.

``tests/test_infra.py`` pins the core engine contract (events DDL, credential
escaping, the compact fallback layout, password masking, sanitize). This file
covers the extended content layered on top: every chart resolves to a defined
dataset, the curated layout covers every chart, native filters bind through
the dataset-id placeholder, and all datasets stay generic (events schema only,
no RFC-specific tables).
"""

from __future__ import annotations

import pytest

from infra.scripts.diagnose_superset_db import _parse_db_host_port
from infra.superset.bootstrap_dashboards import (
    ALL_CHART_DEFINITIONS,
    ALL_VIRTUAL_DATASETS,
    DASHBOARD_ROWS,
    EVENTS_DATASET_PLACEHOLDER,
    NATIVE_FILTERS,
    dashboard_json_metadata,
    dashboard_layout,
    database_uri,
)


def test_every_chart_dataset_is_defined() -> None:
    known = set(ALL_VIRTUAL_DATASETS) | {"events"}
    for chart in ALL_CHART_DEFINITIONS:
        assert chart["dataset"] in known, chart["name"]


def test_chart_names_are_unique() -> None:
    names = [chart["name"] for chart in ALL_CHART_DEFINITIONS]
    assert len(names) == len(set(names))


def test_curated_rows_reference_only_defined_charts() -> None:
    chart_names = {chart["name"] for chart in ALL_CHART_DEFINITIONS}
    for row in DASHBOARD_ROWS:
        for spec in row:
            assert spec["name"] in chart_names, spec["name"]


def test_full_chart_set_gets_curated_layout_covering_every_chart() -> None:
    chart_ids = {
        str(chart["name"]): index
        for index, chart in enumerate(ALL_CHART_DEFINITIONS, 1)
    }
    layout = dashboard_layout(chart_ids)
    placed = {
        node["meta"]["sliceName"]
        for node in layout.values()
        if isinstance(node, dict) and node.get("type") == "CHART"
    }
    assert placed == set(chart_ids)
    assert len(layout["GRID_ID"]["children"]) == len(DASHBOARD_ROWS)


def test_database_uri_is_docker_internal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_USER", "rfs")
    monkeypatch.setenv("POSTGRES_PASSWORD", "changeme")
    monkeypatch.setenv("POSTGRES_DB", "rfs")
    monkeypatch.delenv("POSTGRES_HOST_INTERNAL", raising=False)
    monkeypatch.delenv("POSTGRES_INTERNAL_PORT", raising=False)
    assert database_uri() == "postgresql://rfs:changeme@postgres:5432/rfs"


def test_filter_targets_use_placeholder() -> None:
    assert len(NATIVE_FILTERS) == 4
    for configuration in NATIVE_FILTERS:
        for target in configuration.get("targets", []):
            assert target.get("datasetId") == EVENTS_DATASET_PLACEHOLDER


def test_json_metadata_substitutes_dataset_id_and_enables_cross_filters() -> None:
    metadata = dashboard_json_metadata(42)
    assert metadata["cross_filters_enabled"] is True
    filters = metadata["native_filter_configuration"]
    assert len(filters) == len(NATIVE_FILTERS)
    for configuration in filters:
        for target in configuration.get("targets", []):
            assert target["datasetId"] == 42


def test_datasets_stay_generic_over_the_events_table() -> None:
    for name, sql in ALL_VIRTUAL_DATASETS.items():
        assert "FROM events" in sql, name
        # Guard against RFC-specific schema leaking into the generic package.
        lowered = sql.lower()
        assert "test_runs" not in lowered, name
        assert "test_results" not in lowered, name
        assert "hostname" not in lowered, name


def test_round_always_operates_on_numeric() -> None:
    # PostgreSQL has no ROUND(double precision, n); every ROUND(expr, n) in
    # the dataset SQL must round an explicit ::numeric cast.
    for name, sql in ALL_VIRTUAL_DATASETS.items():
        if "ROUND(" in sql:
            assert "::numeric" in sql, name


def test_llm_datasets_tolerate_missing_token_keys() -> None:
    for name in ("rfs_llm_usage", "rfs_kpi_llm_tokens", "rfs_llm_token_rollup"):
        sql = ALL_VIRTUAL_DATASETS[name]
        for key in ("total_tokens", "prompt_tokens", "completion_tokens", "eval_count"):
            assert key in sql, (name, key)


def test_errors_kpi_has_semantic_conditional_formatting() -> None:
    (errors_kpi,) = [
        chart for chart in ALL_CHART_DEFINITIONS if chart["name"] == "Errors (24h)"
    ]
    formatting = errors_kpi["params"]["conditional_formatting"]
    assert {rule["operator"] for rule in formatting} == {">", "<="}
    assert all(rule["targetValue"] == 0 for rule in formatting)


def test_parse_db_host_port_from_url() -> None:
    assert _parse_db_host_port("postgresql://u:p@db.example:6000/x") == ("db.example", 6000)


def test_parse_db_host_port_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("POSTGRES_PORT", raising=False)
    monkeypatch.delenv("DATABASE_HOST", raising=False)
    assert _parse_db_host_port("") == ("localhost", 5433)
