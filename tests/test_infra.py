"""Static contract tests for the sanitized Superset deployment assets."""

from __future__ import annotations

from sqlalchemy import create_engine, func, select

from infra.scripts.diagnose_superset_db import check_database, mask_database_url
from infra.scripts.sanitize_superset_db import sanitize
from infra.superset.bootstrap_dashboards import (
    CHART_DEFINITIONS,
    EVENTS_DDL,
    VIRTUAL_DATASETS,
    dashboard_layout,
    database_uri,
)
from robotframework_superset.event import Event
from robotframework_superset.sinks.db import DatabaseSink, events_table


def test_bootstrap_schema_matches_database_sink_contract() -> None:
    for column in {
        "event_type",
        "source",
        "wall_clock",
        "monotonic_ns",
        "level",
        "message",
        "duration_ns",
        "payload",
    }:
        assert column in EVENTS_DDL
    assert "TIMESTAMPTZ" in EVENTS_DDL
    assert "JSONB" in EVENTS_DDL
    assert "test_runs" not in EVENTS_DDL


def test_starter_dashboard_covers_required_views() -> None:
    assert set(VIRTUAL_DATASETS) == {
        "rfs_events_over_time",
        "rfs_latency_by_source",
        "rfs_error_rates",
        "rfs_llm_usage",
    }
    assert len(CHART_DEFINITIONS) == 4
    for definition in (CHART_DEFINITIONS[0], CHART_DEFINITIONS[3]):
        metric = definition["params"]["metrics"][0]
        assert metric["expressionType"] == "SIMPLE"
        assert metric["aggregate"] == "SUM"
        assert "column_name" in metric["column"]
    layout = dashboard_layout(
        {str(item["name"]): index for index, item in enumerate(CHART_DEFINITIONS, 1)}
    )
    assert layout["GRID_ID"]["children"] == ["ROW-1", "ROW-2"]
    assert layout["ROW-1"]["children"] == ["CHART-1", "CHART-2"]
    assert layout["ROW-2"]["children"] == ["CHART-3", "CHART-4"]
    for row_id in layout["GRID_ID"]["children"]:
        row = layout[row_id]
        assert row["type"] == "ROW"
        assert row["meta"]["width"] == 12
        for chart_id in row["children"]:
            assert layout[chart_id]["parents"][-1] == row_id


def test_database_uri_encodes_credentials(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("POSTGRES_USER", "robot user")
    monkeypatch.setenv("POSTGRES_PASSWORD", "p@ss/word")
    uri = database_uri()
    assert "robot+user" in uri
    assert "p%40ss%2Fword" in uri
    assert "p@ss/word" not in uri


def test_diagnostic_masks_password() -> None:
    url = "postgresql://rfs:secret-value@localhost:5433/rfs"
    assert mask_database_url(url) == "postgresql://rfs:***@localhost:5433/rfs"


def test_diagnose_and_sanitize_sqlite_event_data(tmp_path) -> None:  # type: ignore[no-untyped-def]
    database_path = tmp_path / "events.sqlite3"
    url = f"sqlite+pysqlite:///{database_path}"
    sink = DatabaseSink(url, batch_size=1)
    sink.emit(Event(event_type="test.event", source="unit"))
    sink.close()

    assert check_database(url)
    assert sanitize(url, confirmed=True) == 0

    engine = create_engine(url)
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(events_table)) == 0
    engine.dispose()
