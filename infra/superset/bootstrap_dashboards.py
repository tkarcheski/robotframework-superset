"""Create the event schema and the RF + LLM observability dashboard.

Run this inside the Superset container after ``superset init`` (the
``superset-init`` compose service and the ``bootstrap`` Make target both do
so). The bootstrap is idempotent: existing database connections, datasets,
charts, and the dashboard are updated in place, never duplicated.

It provisions, over the generic ``events`` table written by the DB sink:

  - The ``events`` table itself (``CREATE TABLE IF NOT EXISTS``) so a fresh
    stack has a valid target before the first event arrives.
  - A Superset database connection ("Robot Framework Events").
  - Virtual (SQL) datasets: KPI singletons, event volume, latency
    percentiles, error rates, LLM token/cost rollups, and a recent-events
    drill-down.
  - Charts over those datasets and one dashboard ("RF + LLM Observability")
    with native filters (time range, source, event type, level).

LLM payload contract
--------------------
The token/cost panels read optional keys from the ``payload`` of
``openai.response`` / ``ollama.response`` events: ``model``, ``total_tokens``
(falling back to ``prompt_tokens`` + ``completion_tokens``, then Ollama's
``eval_count``), and ``cost_usd``. Panels degrade to zero when keys are
absent.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any
from urllib.parse import quote_plus

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

# Stable identities for idempotent updates. The diagnose script checks for
# the same database-connection name.
EVENTS_DB_NAME = "Robot Framework Events"
DASHBOARD_SLUG = "rf-llm-observability"
DASHBOARD_TITLE = "RF + LLM Observability"

# Placeholder substituted with the physical events dataset id when the
# dashboard json_metadata (native filters) is built.
EVENTS_DATASET_PLACEHOLDER = "__EVENTS_DS_ID__"

# Semantic colors for KPI conditional formatting (generic, not brand colors).
_COLOR_OK = {"r": 76, "g": 175, "b": 80, "a": 1}  # green
_COLOR_BAD = {"r": 244, "g": 67, "b": 54, "a": 1}  # red

_LLM_EVENT_TYPES = "('openai.response', 'ollama.response')"

# Token count for an LLM response, tolerant of which payload key is present:
# total_tokens, then prompt + completion (OpenAI), then eval_count (Ollama).
_TOKENS_EXPR = (
    "COALESCE("
    "NULLIF(payload->>'total_tokens', '')::numeric, "
    "NULLIF(payload->>'prompt_tokens', '')::numeric"
    " + NULLIF(payload->>'completion_tokens', '')::numeric, "
    "NULLIF(payload->>'eval_count', '')::numeric, "
    "0)"
)

EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS events (
    id           BIGSERIAL PRIMARY KEY,
    event_type   TEXT        NOT NULL,
    source       TEXT        NOT NULL,
    wall_clock   TIMESTAMPTZ NOT NULL,
    monotonic_ns BIGINT      NOT NULL,
    level        TEXT        NOT NULL,
    message      TEXT        NOT NULL DEFAULT '',
    duration_ns  BIGINT      NOT NULL DEFAULT -1,
    payload      JSONB       NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS events_wall_clock_idx ON events (wall_clock);
CREATE INDEX IF NOT EXISTS events_type_source_idx ON events (event_type, source);
"""

# Core virtual datasets (the contract pinned by tests/test_infra.py).
VIRTUAL_DATASETS: dict[str, str] = {
    "rfs_events_over_time": """
        SELECT
            DATE_TRUNC('minute', wall_clock) AS time_bucket,
            source,
            event_type,
            COUNT(*) AS event_count
        FROM events
        GROUP BY DATE_TRUNC('minute', wall_clock), source, event_type
        ORDER BY time_bucket
    """,
    "rfs_latency_by_source": """
        SELECT
            source,
            event_type,
            COUNT(*) AS sample_count,
            ROUND((AVG(duration_ns) / 1000000.0)::numeric, 3) AS avg_duration_ms,
            ROUND((PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY duration_ns)
                / 1000000.0)::numeric, 3) AS p50_duration_ms,
            ROUND((PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY duration_ns)
                / 1000000.0)::numeric, 3) AS p95_duration_ms,
            ROUND((MAX(duration_ns) / 1000000.0)::numeric, 3) AS max_duration_ms
        FROM events
        WHERE duration_ns >= 0
        GROUP BY source, event_type
    """,
    "rfs_error_rates": """
        SELECT
            source,
            event_type,
            COUNT(*) AS event_count,
            COUNT(*) FILTER (WHERE level = 'ERROR') AS error_count,
            ROUND((100.0 * COUNT(*) FILTER (WHERE level = 'ERROR')
                / NULLIF(COUNT(*), 0))::numeric, 2) AS error_rate_pct
        FROM events
        GROUP BY source, event_type
    """,
    "rfs_llm_usage": f"""
        SELECT
            DATE_TRUNC('hour', wall_clock) AS time_bucket,
            source,
            COALESCE(payload->>'model', 'unknown') AS model,
            SUM(COALESCE(NULLIF(payload->>'prompt_tokens', '')::bigint, 0))
                AS prompt_tokens,
            SUM(COALESCE(NULLIF(payload->>'completion_tokens', '')::bigint, 0))
                AS completion_tokens,
            SUM({_TOKENS_EXPR}) AS total_tokens,
            SUM(COALESCE(NULLIF(payload->>'cost_usd', '')::numeric, 0))
                AS cost_usd
        FROM events
        WHERE event_type IN {_LLM_EVENT_TYPES}
        GROUP BY DATE_TRUNC('hour', wall_clock), source, payload->>'model'
        ORDER BY time_bucket
    """,
}

# Additional virtual datasets: KPI singletons and drill-down views.
EXTRA_VIRTUAL_DATASETS: dict[str, str] = {
    "rfs_kpi_total_events": """
        SELECT COUNT(*) AS total_events
        FROM events
        WHERE wall_clock >= NOW() - INTERVAL '24 hours'
    """,
    "rfs_kpi_error_count": """
        SELECT COUNT(*) AS error_count
        FROM events
        WHERE level = 'ERROR'
          AND wall_clock >= NOW() - INTERVAL '24 hours'
    """,
    "rfs_kpi_p95_latency": """
        SELECT ROUND((PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY duration_ns)
            / 1000000.0)::numeric, 3) AS p95_duration_ms
        FROM events
        WHERE duration_ns >= 0
          AND wall_clock >= NOW() - INTERVAL '24 hours'
    """,
    "rfs_kpi_llm_tokens": f"""
        SELECT SUM({_TOKENS_EXPR}) AS total_tokens
        FROM events
        WHERE event_type IN {_LLM_EVENT_TYPES}
          AND wall_clock >= NOW() - INTERVAL '24 hours'
    """,
    "rfs_events_by_source": """
        SELECT
            source,
            COUNT(*) AS event_count
        FROM events
        GROUP BY source
        ORDER BY event_count DESC
    """,
    "rfs_error_rate_timeseries": """
        SELECT
            DATE_TRUNC('minute', wall_clock) AS time_bucket,
            source,
            COUNT(*) AS event_count,
            ROUND((100.0 * COUNT(*) FILTER (WHERE level = 'ERROR')
                / NULLIF(COUNT(*), 0))::numeric, 2) AS error_rate_pct
        FROM events
        GROUP BY DATE_TRUNC('minute', wall_clock), source
        ORDER BY time_bucket
    """,
    "rfs_llm_token_rollup": f"""
        SELECT
            COALESCE(payload->>'model', 'unknown') AS model,
            source,
            COUNT(*) AS call_count,
            SUM({_TOKENS_EXPR}) AS total_tokens,
            SUM(COALESCE(NULLIF(payload->>'cost_usd', '')::numeric, 0))
                AS total_cost_usd,
            ROUND((AVG(duration_ns) FILTER (WHERE duration_ns >= 0)
                / 1000000.0)::numeric, 3) AS avg_duration_ms
        FROM events
        WHERE event_type IN {_LLM_EVENT_TYPES}
        GROUP BY COALESCE(payload->>'model', 'unknown'), source
        ORDER BY total_tokens DESC
    """,
    "rfs_recent_events": """
        SELECT
            wall_clock,
            source,
            event_type,
            level,
            message,
            ROUND((duration_ns / 1000000.0)::numeric, 3) AS duration_ms
        FROM events
        ORDER BY wall_clock DESC
        LIMIT 500
    """,
}

ALL_VIRTUAL_DATASETS: dict[str, str] = {**VIRTUAL_DATASETS, **EXTRA_VIRTUAL_DATASETS}

# Core charts (the contract pinned by tests/test_infra.py).
CHART_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "name": "Events over time",
        "dataset": "rfs_events_over_time",
        "viz_type": "echarts_timeseries_line",
        "params": {
            "x_axis": "time_bucket",
            "time_grain_sqla": "PT1M",
            "metrics": [
                {
                    "expressionType": "SIMPLE",
                    "column": {"column_name": "event_count", "type": "LONGINTEGER"},
                    "aggregate": "SUM",
                    "label": "Events",
                    "optionName": "metric_rfs_event_count",
                }
            ],
            "groupby": ["source"],
            "row_limit": 10000,
            "show_legend": True,
        },
    },
    {
        "name": "Latency by source and event",
        "dataset": "rfs_latency_by_source",
        "viz_type": "table",
        "params": {
            "all_columns": [
                "source",
                "event_type",
                "sample_count",
                "avg_duration_ms",
                "p50_duration_ms",
                "p95_duration_ms",
                "max_duration_ms",
            ],
            "order_by_cols": ['["p95_duration_ms", false]'],
            "row_limit": 1000,
        },
    },
    {
        "name": "Error rates",
        "dataset": "rfs_error_rates",
        "viz_type": "table",
        "params": {
            "all_columns": [
                "source",
                "event_type",
                "event_count",
                "error_count",
                "error_rate_pct",
            ],
            "order_by_cols": ['["error_rate_pct", false]'],
            "row_limit": 1000,
        },
    },
    {
        "name": "LLM token usage",
        "dataset": "rfs_llm_usage",
        "viz_type": "echarts_timeseries_bar",
        "params": {
            "x_axis": "time_bucket",
            "time_grain_sqla": "PT1H",
            "metrics": [
                {
                    "expressionType": "SIMPLE",
                    "column": {"column_name": "total_tokens", "type": "DECIMAL"},
                    "aggregate": "SUM",
                    "label": "Total tokens",
                    "optionName": "metric_rfs_total_tokens",
                }
            ],
            "groupby": ["model"],
            "row_limit": 10000,
            "show_legend": True,
        },
    },
)

# Additional charts: the KPI row, breakdowns, and the drill-down table.
EXTRA_CHART_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "name": "Total events (24h)",
        "dataset": "rfs_kpi_total_events",
        "viz_type": "big_number_total",
        "params": {
            "metric": {
                "expressionType": "SIMPLE",
                "column": {"column_name": "total_events", "type": "LONGINTEGER"},
                "aggregate": "MAX",
                "label": "Total events",
                "optionName": "metric_rfs_kpi_total_events",
            },
            "subheader": "Last 24 hours",
            "y_axis_format": ",d",
        },
    },
    {
        "name": "Errors (24h)",
        "dataset": "rfs_kpi_error_count",
        "viz_type": "big_number_total",
        "params": {
            "metric": {
                "expressionType": "SIMPLE",
                "column": {"column_name": "error_count", "type": "LONGINTEGER"},
                "aggregate": "MAX",
                "label": "Errors",
                "optionName": "metric_rfs_kpi_error_count",
            },
            "subheader": "level = ERROR, last 24 hours",
            "y_axis_format": ",d",
            "conditional_formatting": [
                {"operator": ">", "targetValue": 0, "colorScheme": _COLOR_BAD},
                {"operator": "<=", "targetValue": 0, "colorScheme": _COLOR_OK},
            ],
        },
    },
    {
        "name": "P95 latency (24h)",
        "dataset": "rfs_kpi_p95_latency",
        "viz_type": "big_number_total",
        "params": {
            "metric": {
                "expressionType": "SIMPLE",
                "column": {"column_name": "p95_duration_ms", "type": "DECIMAL"},
                "aggregate": "MAX",
                "label": "P95 latency",
                "optionName": "metric_rfs_kpi_p95_latency",
            },
            "subheader": "milliseconds, last 24 hours",
            "y_axis_format": ",.1f",
        },
    },
    {
        "name": "LLM tokens (24h)",
        "dataset": "rfs_kpi_llm_tokens",
        "viz_type": "big_number_total",
        "params": {
            "metric": {
                "expressionType": "SIMPLE",
                "column": {"column_name": "total_tokens", "type": "DECIMAL"},
                "aggregate": "MAX",
                "label": "LLM tokens",
                "optionName": "metric_rfs_kpi_llm_tokens",
            },
            "subheader": "openai + ollama responses",
            "y_axis_format": ",d",
        },
    },
    {
        "name": "Events by source",
        "dataset": "rfs_events_by_source",
        "viz_type": "pie",
        "params": {
            "metric": {
                "expressionType": "SIMPLE",
                "column": {"column_name": "event_count", "type": "LONGINTEGER"},
                "aggregate": "SUM",
                "label": "Events",
                "optionName": "metric_rfs_events_by_source",
            },
            "groupby": ["source"],
            "row_limit": 100,
            "show_legend": True,
        },
    },
    {
        "name": "Error rate over time",
        "dataset": "rfs_error_rate_timeseries",
        "viz_type": "echarts_timeseries_line",
        "params": {
            "x_axis": "time_bucket",
            "time_grain_sqla": "PT1M",
            "metrics": [
                {
                    "expressionType": "SIMPLE",
                    "column": {"column_name": "error_rate_pct", "type": "DECIMAL"},
                    "aggregate": "AVG",
                    "label": "Error rate %",
                    "optionName": "metric_rfs_error_rate_pct",
                }
            ],
            "groupby": ["source"],
            "row_limit": 10000,
            "show_legend": True,
        },
    },
    {
        "name": "LLM tokens by model",
        "dataset": "rfs_llm_token_rollup",
        "viz_type": "table",
        "params": {
            "all_columns": [
                "model",
                "source",
                "call_count",
                "total_tokens",
                "total_cost_usd",
                "avg_duration_ms",
            ],
            "order_by_cols": ['["total_tokens", false]'],
            "row_limit": 1000,
        },
    },
    {
        "name": "Recent events",
        "dataset": "rfs_recent_events",
        "viz_type": "table",
        "params": {
            "all_columns": [
                "wall_clock",
                "source",
                "event_type",
                "level",
                "message",
                "duration_ms",
            ],
            "order_by_cols": ['["wall_clock", false]'],
            "row_limit": 500,
        },
    },
)

ALL_CHART_DEFINITIONS: tuple[dict[str, Any], ...] = (
    CHART_DEFINITIONS + EXTRA_CHART_DEFINITIONS
)

# Curated dashboard grid: KPI row on top, drill-down at the bottom.
# Superset uses a 12-column grid; height is in grid units.
DASHBOARD_ROWS: tuple[tuple[dict[str, Any], ...], ...] = (
    (
        {"name": "Total events (24h)", "width": 3, "height": 10},
        {"name": "Errors (24h)", "width": 3, "height": 10},
        {"name": "P95 latency (24h)", "width": 3, "height": 10},
        {"name": "LLM tokens (24h)", "width": 3, "height": 10},
    ),
    (
        {"name": "Events over time", "width": 8, "height": 50},
        {"name": "Events by source", "width": 4, "height": 50},
    ),
    ({"name": "Latency by source and event", "width": 12, "height": 40},),
    (
        {"name": "Error rate over time", "width": 8, "height": 50},
        {"name": "Error rates", "width": 4, "height": 50},
    ),
    (
        {"name": "LLM tokens by model", "width": 6, "height": 50},
        {"name": "LLM token usage", "width": 6, "height": 50},
    ),
    ({"name": "Recent events", "width": 12, "height": 50},),
)

# Native filters bound to the physical events dataset (raw source /
# event_type / level columns). The placeholder is substituted at runtime.
NATIVE_FILTERS: tuple[dict[str, Any], ...] = (
    {
        "id": "NATIVE_FILTER-TIME",
        "name": "Time Range",
        "filterType": "filter_time",
        "targets": [{"datasetId": EVENTS_DATASET_PLACEHOLDER}],
        "defaultDataMask": {"filterState": {"value": "Last day"}},
        "scope": {"rootPath": ["ROOT_ID"], "excluded": []},
    },
    {
        "id": "NATIVE_FILTER-SOURCE",
        "name": "Source",
        "filterType": "filter_select",
        "targets": [
            {"column": {"name": "source"}, "datasetId": EVENTS_DATASET_PLACEHOLDER},
        ],
        "scope": {"rootPath": ["ROOT_ID"], "excluded": []},
    },
    {
        "id": "NATIVE_FILTER-EVENT-TYPE",
        "name": "Event Type",
        "filterType": "filter_select",
        "targets": [
            {"column": {"name": "event_type"}, "datasetId": EVENTS_DATASET_PLACEHOLDER},
        ],
        "scope": {"rootPath": ["ROOT_ID"], "excluded": []},
    },
    {
        "id": "NATIVE_FILTER-LEVEL",
        "name": "Level",
        "filterType": "filter_select",
        "targets": [
            {"column": {"name": "level"}, "datasetId": EVENTS_DATASET_PLACEHOLDER},
        ],
        "scope": {"rootPath": ["ROOT_ID"], "excluded": []},
    },
)


def database_uri() -> str:
    """Build the Docker-internal event database URI without logging secrets."""
    user = quote_plus(os.getenv("POSTGRES_USER", "rfs"))
    password = quote_plus(os.getenv("POSTGRES_PASSWORD", "changeme"))
    host = os.getenv("POSTGRES_HOST_INTERNAL", "postgres")
    port = os.getenv("POSTGRES_INTERNAL_PORT", "5432")
    database = quote_plus(os.getenv("POSTGRES_DB", "rfs"))
    return f"postgresql://{user}:{password}@{host}:{port}/{database}"


def _run_schema_ddl() -> None:
    from sqlalchemy import create_engine, text

    engine = create_engine(database_uri())
    try:
        with engine.begin() as connection:
            for statement in EVENTS_DDL.split(";"):
                executable = statement.strip()
                if executable:
                    connection.execute(text(executable))
    finally:
        engine.dispose()
    log.info("Event schema is ready.")


def _ensure_database_connection() -> int:
    from superset import db as superset_db  # type: ignore[attr-defined]
    from superset.models.core import Database

    database = (
        superset_db.session.query(Database)
        .filter_by(database_name=EVENTS_DB_NAME)
        .first()
    )
    if database is None:
        database = Database(
            database_name=EVENTS_DB_NAME,
            sqlalchemy_uri=database_uri(),
            expose_in_sqllab=True,
        )
        superset_db.session.add(database)
    else:
        database.sqlalchemy_uri = database_uri()
        database.expose_in_sqllab = True
    superset_db.session.commit()
    log.info("Superset database connection is ready (id=%s).", database.id)
    return int(database.id)


def _upsert_dataset(database_id: int, name: str, sql: str | None = None) -> Any:
    from superset import db as superset_db  # type: ignore[attr-defined]
    from superset.connectors.sqla.models import SqlaTable

    dataset = (
        superset_db.session.query(SqlaTable)
        .filter_by(table_name=name, database_id=database_id)
        .first()
    )
    if dataset is None:
        dataset = SqlaTable(
            table_name=name,
            database_id=database_id,
            schema="public",
            sql=sql,
        )
        superset_db.session.add(dataset)
        superset_db.session.commit()
    elif dataset.sql != sql:
        dataset.sql = sql
        superset_db.session.commit()
    try:
        dataset.fetch_metadata()
        superset_db.session.commit()
    except Exception as exc:  # metadata can lag immediately after first boot
        superset_db.session.rollback()
        log.warning("Metadata refresh failed for %s: %s", name, exc)
    log.info("Dataset is ready: %s (id=%s).", name, dataset.id)
    return dataset


def _ensure_datasets(database_id: int) -> dict[str, int]:
    datasets = {"events": int(_upsert_dataset(database_id, "events").id)}
    for name, sql in ALL_VIRTUAL_DATASETS.items():
        datasets[name] = int(_upsert_dataset(database_id, name, sql.strip()).id)
    return datasets


def _layout_scaffold() -> dict[str, Any]:
    return {
        "DASHBOARD_VERSION_KEY": "v2",
        "ROOT_ID": {"id": "ROOT_ID", "type": "ROOT", "children": ["GRID_ID"]},
        "GRID_ID": {
            "id": "GRID_ID",
            "type": "GRID",
            "parents": ["ROOT_ID"],
            "children": [],
        },
        "HEADER_ID": {
            "id": "HEADER_ID",
            "type": "HEADER",
            "meta": {"text": DASHBOARD_TITLE},
        },
    }


def _add_row(layout: dict[str, Any], row_number: int) -> str:
    row_id = f"ROW-{row_number}"
    layout["GRID_ID"]["children"].append(row_id)
    layout[row_id] = {
        "id": row_id,
        "type": "ROW",
        "parents": ["ROOT_ID", "GRID_ID"],
        "children": [],
        "meta": {"background": "BACKGROUND_TRANSPARENT", "width": 12},
    }
    return row_id


def _add_chart(
    layout: dict[str, Any],
    row_id: str,
    name: str,
    chart_id: int,
    width: int,
    height: int,
) -> None:
    node_id = f"CHART-{chart_id}"
    layout[row_id]["children"].append(node_id)
    layout[node_id] = {
        "id": node_id,
        "type": "CHART",
        "parents": ["ROOT_ID", "GRID_ID", row_id],
        "children": [],
        "meta": {
            "chartId": chart_id,
            "sliceName": name,
            "width": width,
            "height": height,
        },
    }


def _curated_layout(chart_ids: dict[str, int]) -> dict[str, Any]:
    layout = _layout_scaffold()
    placed: set[str] = set()
    row_number = 0
    for row_specs in DASHBOARD_ROWS:
        row_number += 1
        row_id = _add_row(layout, row_number)
        for spec in row_specs:
            name = str(spec["name"])
            _add_chart(layout, row_id, name, chart_ids[name], spec["width"], spec["height"])
            placed.add(name)
    # Any chart missing from the curated grid still gets a slot, two per row.
    for index, name in enumerate(sorted(set(chart_ids) - placed)):
        if index % 2 == 0:
            row_number += 1
            row_id = _add_row(layout, row_number)
        _add_chart(layout, row_id, name, chart_ids[name], 6, 40)
    return layout


def _two_column_layout(chart_ids: dict[str, int]) -> dict[str, Any]:
    layout = _layout_scaffold()
    for index, (name, chart_id) in enumerate(chart_ids.items()):
        row_number = index // 2 + 1
        row_id = f"ROW-{row_number}"
        if row_id not in layout:
            _add_row(layout, row_number)
        _add_chart(layout, row_id, name, chart_id, 6, 50 if index < 2 else 40)
    return layout


def dashboard_layout(chart_ids: dict[str, int]) -> dict[str, Any]:
    """Build the dashboard ``position_json`` grid.

    When every chart named in :data:`DASHBOARD_ROWS` is present, the curated
    grid is used (KPI row on top, drill-down at the bottom). A partial chart
    set falls back to a compact two-column layout so the dashboard stays
    usable during incremental bootstraps.
    """
    curated_names = {spec["name"] for row in DASHBOARD_ROWS for spec in row}
    if curated_names <= set(chart_ids):
        return _curated_layout(chart_ids)
    return _two_column_layout(chart_ids)


def dashboard_json_metadata(events_dataset_id: int) -> dict[str, Any]:
    """Build dashboard ``json_metadata`` with native filters bound to data."""
    filters: list[dict[str, Any]] = json.loads(json.dumps(NATIVE_FILTERS))
    for configuration in filters:
        for target in configuration.get("targets", []):
            if target.get("datasetId") == EVENTS_DATASET_PLACEHOLDER:
                target["datasetId"] = events_dataset_id
    return {
        "native_filter_configuration": filters,
        "cross_filters_enabled": True,
        "chart_configuration": {},
        "timed_refresh_immune_slices": [],
        "refresh_frequency": 0,
    }


def _ensure_charts_and_dashboard(datasets: dict[str, int]) -> None:
    from superset import db as superset_db  # type: ignore[attr-defined]
    from superset.models.dashboard import Dashboard
    from superset.models.slice import Slice

    chart_ids: dict[str, int] = {}
    chart_objects: list[Any] = []
    for definition in ALL_CHART_DEFINITIONS:
        name = str(definition["name"])
        dataset_id = datasets[str(definition["dataset"])]
        params = dict(definition["params"])
        params.update(
            {
                "datasource": f"{dataset_id}__table",
                "viz_type": definition["viz_type"],
                "adhoc_filters": [],
            }
        )
        chart = superset_db.session.query(Slice).filter_by(slice_name=name).first()
        if chart is None:
            chart = Slice(slice_name=name)
            superset_db.session.add(chart)
        chart.viz_type = definition["viz_type"]
        chart.datasource_id = dataset_id
        chart.datasource_type = "table"
        chart.params = json.dumps(params)
        superset_db.session.commit()
        chart_ids[name] = int(chart.id)
        chart_objects.append(chart)
        log.info("Chart is ready: %s (id=%s).", name, chart.id)

    dashboard = (
        superset_db.session.query(Dashboard).filter_by(slug=DASHBOARD_SLUG).first()
    )
    if dashboard is None:
        dashboard = Dashboard(dashboard_title=DASHBOARD_TITLE, slug=DASHBOARD_SLUG)
        superset_db.session.add(dashboard)
    dashboard.dashboard_title = DASHBOARD_TITLE
    dashboard.published = True
    dashboard.position_json = json.dumps(dashboard_layout(chart_ids))
    dashboard.json_metadata = json.dumps(dashboard_json_metadata(datasets["events"]))
    dashboard.slices = chart_objects
    superset_db.session.commit()
    log.info("Dashboard is ready: %s (id=%s).", dashboard.dashboard_title, dashboard.id)


def bootstrap() -> None:
    """Run the complete idempotent bootstrap inside a Superset app context."""
    try:
        from superset.app import create_app
    except ImportError:
        log.error("Superset is not installed; run this script inside its container.")
        raise SystemExit(1) from None

    app = create_app()
    with app.app_context():
        _run_schema_ddl()
        database_id = _ensure_database_connection()
        datasets = _ensure_datasets(database_id)
        _ensure_charts_and_dashboard(datasets)
    log.info("Superset bootstrap complete.")


if __name__ == "__main__":
    try:
        bootstrap()
    except Exception:
        log.exception("Superset bootstrap failed.")
        sys.exit(1)
