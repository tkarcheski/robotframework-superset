"""Bootstrap Superset with an RF + LLM observability dashboard.

Run inside the Superset container after ``superset init`` (docker-compose wires
this automatically; the ``bootstrap`` Make target runs it on demand):

    python /app/bootstrap_dashboards.py

It is idempotent — re-running updates existing objects instead of duplicating
them. It creates, over the generic ``events`` table written by the DB sink:

  - The ``events`` table (``CREATE TABLE IF NOT EXISTS``) so a fresh stack has a
    valid target even before the first event is written. The canonical schema is
    defined in ``docs/ARCHITECTURE.md`` / the "DB sink + schema" issue; this DDL
    mirrors it and never drops or alters an existing table.
  - A Superset database connection to that PostgreSQL instance.
  - Virtual (SQL) datasets: event volume over time, latency percentiles by
    source/event_type, error rates, and LLM token/cost rollups read from the
    JSONB ``payload``.
  - Charts and one starter dashboard: "RF + LLM Observability".

This module is deliberately generic: it knows only the ``events`` schema
(``event_type, source, wall_clock, monotonic_ns, level, message, duration_ns,
payload``) and carries no product-specific tables.

LLM payload contract
--------------------
The token/cost panels read these optional keys from the ``payload`` of
``openai.response`` / ``ollama.response`` events (populated by the LLM feeds):

  - ``model``            — model id (text)
  - ``total_tokens``     — total tokens for the call (int); falls back to
                           ``prompt_tokens`` + ``completion_tokens`` (OpenAI) or
                           ``eval_count`` (Ollama) when absent
  - ``cost_usd``         — call cost in USD (numeric), if the feed computes one

Panels degrade gracefully to zero when a key is missing.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# Name of the Superset database connection this script manages. The diagnose
# script checks for the same name.
EVENTS_DB_NAME = "RF + LLM Events"

# Dashboard slug (stable identity for idempotent updates).
DASHBOARD_SLUG = "rf-llm-observability"
DASHBOARD_TITLE = "RF + LLM Observability"

# Semantic colors for conditional formatting (kept generic, not brand colors).
_COLOR_OK = {"r": 76, "g": 175, "b": 80, "a": 1}  # green
_COLOR_BAD = {"r": 244, "g": 67, "b": 54, "a": 1}  # red

# ---------------------------------------------------------------------------
# Schema — the generic events table (idempotent, never destructive).
# ---------------------------------------------------------------------------
_EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS events (
    id            BIGSERIAL PRIMARY KEY,
    event_type    TEXT        NOT NULL,
    source        TEXT        NOT NULL,
    wall_clock    TIMESTAMPTZ NOT NULL,
    monotonic_ns  BIGINT      NOT NULL,
    level         TEXT        NOT NULL,
    message       TEXT        NOT NULL DEFAULT '',
    duration_ns   BIGINT      NOT NULL DEFAULT -1,
    payload       JSONB       NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS events_wall_clock_idx ON events (wall_clock);
CREATE INDEX IF NOT EXISTS events_type_source_idx ON events (event_type, source);
"""

# Shared SQL fragment: tokens for an LLM response, tolerant of which key is set.
_TOKENS_EXPR = (
    "COALESCE("
    "NULLIF(payload->>'total_tokens','')::numeric,"
    "NULLIF(payload->>'prompt_tokens','')::numeric"
    " + NULLIF(payload->>'completion_tokens','')::numeric,"
    "NULLIF(payload->>'eval_count','')::numeric,"
    "0)"
)

_LLM_RESPONSE_TYPES = "('openai.response', 'ollama.response')"

# ---------------------------------------------------------------------------
# Virtual (SQL-backed) datasets over the events table.
# ---------------------------------------------------------------------------
_VIRTUAL_DATASETS: dict[str, str] = {
    # --- KPI singletons ---
    "kpi_total_events": """
        SELECT COUNT(*) AS total_events
        FROM events
        WHERE wall_clock >= NOW() - INTERVAL '24 hours'
    """,
    "kpi_error_count": """
        SELECT COUNT(*) AS error_count
        FROM events
        WHERE level = 'ERROR'
          AND wall_clock >= NOW() - INTERVAL '24 hours'
    """,
    "kpi_p95_latency": """
        SELECT ROUND(
            (PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY duration_ns))::numeric
                / 1000000.0, 2
        ) AS p95_ms
        FROM events
        WHERE duration_ns >= 0
          AND wall_clock >= NOW() - INTERVAL '24 hours'
    """,
    "kpi_llm_tokens": f"""
        SELECT SUM({_TOKENS_EXPR}) AS total_tokens
        FROM events
        WHERE event_type IN {_LLM_RESPONSE_TYPES}
          AND wall_clock >= NOW() - INTERVAL '24 hours'
    """,
    # --- Event volume ---
    "events_over_time": """
        SELECT
            DATE_TRUNC('minute', wall_clock) AS time_bucket,
            event_type,
            COUNT(*) AS event_count
        FROM events
        GROUP BY DATE_TRUNC('minute', wall_clock), event_type
        ORDER BY time_bucket
    """,
    "events_by_source": """
        SELECT
            source,
            COUNT(*) AS event_count
        FROM events
        GROUP BY source
        ORDER BY event_count DESC
    """,
    # --- Latency ---
    "latency_by_source": """
        SELECT
            source,
            event_type,
            COUNT(*) AS sample_count,
            ROUND(AVG(duration_ns) / 1000000.0, 2) AS avg_ms,
            ROUND(
                (PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY duration_ns))::numeric
                    / 1000000.0, 2
            ) AS p50_ms,
            ROUND(
                (PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY duration_ns))::numeric
                    / 1000000.0, 2
            ) AS p95_ms,
            ROUND(MAX(duration_ns) / 1000000.0, 2) AS max_ms
        FROM events
        WHERE duration_ns >= 0
        GROUP BY source, event_type
        ORDER BY p95_ms DESC
    """,
    # --- Error rates ---
    "error_rate_by_source": """
        SELECT
            source,
            COUNT(*) FILTER (WHERE level = 'ERROR') AS error_count,
            COUNT(*) AS total_count,
            ROUND(
                100.0 * COUNT(*) FILTER (WHERE level = 'ERROR')
                    / NULLIF(COUNT(*), 0), 2
            ) AS error_pct
        FROM events
        GROUP BY source
        ORDER BY error_pct DESC
    """,
    "error_rate_timeseries": """
        SELECT
            DATE_TRUNC('minute', wall_clock) AS time_bucket,
            source,
            ROUND(
                100.0 * COUNT(*) FILTER (WHERE level = 'ERROR')
                    / NULLIF(COUNT(*), 0), 2
            ) AS error_pct,
            COUNT(*) AS total_count
        FROM events
        GROUP BY DATE_TRUNC('minute', wall_clock), source
        ORDER BY time_bucket
    """,
    # --- LLM token / cost rollups (read from JSONB payload) ---
    "llm_token_rollup": f"""
        SELECT
            COALESCE(payload->>'model', 'unknown') AS model,
            source,
            COUNT(*) AS call_count,
            SUM({_TOKENS_EXPR}) AS total_tokens,
            SUM(COALESCE(NULLIF(payload->>'cost_usd','')::numeric, 0))
                AS total_cost_usd,
            ROUND(AVG(duration_ns) / 1000000.0, 2) AS avg_ms
        FROM events
        WHERE event_type IN {_LLM_RESPONSE_TYPES}
        GROUP BY COALESCE(payload->>'model', 'unknown'), source
        ORDER BY total_tokens DESC
    """,
    "llm_token_timeseries": f"""
        SELECT
            DATE_TRUNC('minute', wall_clock) AS time_bucket,
            COALESCE(payload->>'model', 'unknown') AS model,
            SUM({_TOKENS_EXPR}) AS tokens
        FROM events
        WHERE event_type IN {_LLM_RESPONSE_TYPES}
        GROUP BY DATE_TRUNC('minute', wall_clock),
                 COALESCE(payload->>'model', 'unknown')
        ORDER BY time_bucket
    """,
    # --- Drill-down ---
    "recent_events": """
        SELECT
            wall_clock,
            source,
            event_type,
            level,
            message,
            ROUND(duration_ns / 1000000.0, 2) AS duration_ms
        FROM events
        ORDER BY wall_clock DESC
        LIMIT 500
    """,
}


def _big_number(datasource_key: str, name: str, column: str, aggregate: str,
                subheader: str, fmt: str = ",d",
                bad_when: str | None = None) -> dict[str, Any]:
    """Build a big-number KPI chart definition."""
    params: dict[str, Any] = {
        "metric": {
            "expressionType": "SIMPLE",
            "column": {"column_name": column},
            "aggregate": aggregate,
            "label": name,
        },
        "subheader": subheader,
        "y_axis_format": fmt,
    }
    if bad_when == "gt0":
        params["conditional_formatting"] = [
            {"operator": ">", "targetValue": 0, "colorScheme": _COLOR_BAD},
            {"operator": "<=", "targetValue": 0, "colorScheme": _COLOR_OK},
        ]
    return {
        "slice_name": name,
        "viz_type": "big_number_total",
        "datasource_id_key": datasource_key,
        "params": params,
    }


def _line(datasource_key: str, name: str, metric_col: str, aggregate: str,
          groupby: list[str]) -> dict[str, Any]:
    """Build a time-series line chart definition (x_axis = time_bucket)."""
    return {
        "slice_name": name,
        "viz_type": "echarts_timeseries_line",
        "datasource_id_key": datasource_key,
        "params": {
            "metrics": [
                {
                    "expressionType": "SIMPLE",
                    "column": {"column_name": metric_col},
                    "aggregate": aggregate,
                    "label": metric_col,
                },
            ],
            "groupby": groupby,
            "x_axis": "time_bucket",
            "x_axis_time_format": "smart_date",
        },
    }


def _pie(datasource_key: str, name: str, metric_col: str,
         groupby: list[str]) -> dict[str, Any]:
    """Build a pie chart definition."""
    return {
        "slice_name": name,
        "viz_type": "pie",
        "datasource_id_key": datasource_key,
        "params": {
            "metric": {
                "expressionType": "SIMPLE",
                "column": {"column_name": metric_col},
                "aggregate": "SUM",
                "label": metric_col,
            },
            "groupby": groupby,
        },
    }


def _table(datasource_key: str, name: str, columns: list[str]) -> dict[str, Any]:
    """Build a raw-records table chart definition."""
    return {
        "slice_name": name,
        "viz_type": "table",
        "datasource_id_key": datasource_key,
        "params": {
            "query_mode": "raw",
            "all_columns": columns,
            "order_by_cols": [],
            "row_limit": 500,
        },
    }


_CHART_DEFS: list[dict[str, Any]] = [
    # KPI row
    _big_number("kpi_total_events", "Total Events (24h)", "total_events", "MAX",
                "Last 24 hours"),
    _big_number("kpi_error_count", "Errors (24h)", "error_count", "MAX",
                "level = ERROR, last 24h", bad_when="gt0"),
    _big_number("kpi_p95_latency", "P95 Latency (24h)", "p95_ms", "MAX",
                "milliseconds, last 24h", fmt=",.1f"),
    _big_number("kpi_llm_tokens", "LLM Tokens (24h)", "total_tokens", "MAX",
                "openai + ollama responses"),
    # Event volume
    _line("events_over_time", "Events Over Time", "event_count", "SUM",
          ["event_type"]),
    _pie("events_by_source", "Events by Source", "event_count", ["source"]),
    # Latency
    _table("latency_by_source", "Latency by Source & Event Type",
           ["source", "event_type", "sample_count", "avg_ms", "p50_ms",
            "p95_ms", "max_ms"]),
    # Error rates
    _line("error_rate_timeseries", "Error Rate Over Time", "error_pct", "AVG",
          ["source"]),
    _table("error_rate_by_source", "Error Rate by Source",
           ["source", "error_count", "total_count", "error_pct"]),
    # LLM
    _table("llm_token_rollup", "LLM Tokens by Model",
           ["model", "source", "call_count", "total_tokens", "total_cost_usd",
            "avg_ms"]),
    _line("llm_token_timeseries", "LLM Tokens Over Time", "tokens", "SUM",
          ["model"]),
    # Drill-down
    _table("recent_events", "Recent Events",
           ["wall_clock", "source", "event_type", "level", "message",
            "duration_ms"]),
]


# Dashboard layout — Superset uses a 12-column grid; height is in grid units.
_LAYOUT_SECTIONS: list[dict[str, Any]] = [
    {
        "charts": [
            {"name": "Total Events (24h)", "width": 3, "height": 10},
            {"name": "Errors (24h)", "width": 3, "height": 10},
            {"name": "P95 Latency (24h)", "width": 3, "height": 10},
            {"name": "LLM Tokens (24h)", "width": 3, "height": 10},
        ],
    },
    {
        "charts": [
            {"name": "Events Over Time", "width": 8, "height": 50},
            {"name": "Events by Source", "width": 4, "height": 50},
        ],
    },
    {
        "charts": [
            {"name": "Latency by Source & Event Type", "width": 12, "height": 40},
        ],
    },
    {
        "charts": [
            {"name": "Error Rate Over Time", "width": 8, "height": 50},
            {"name": "Error Rate by Source", "width": 4, "height": 50},
        ],
    },
    {
        "charts": [
            {"name": "LLM Tokens by Model", "width": 6, "height": 50},
            {"name": "LLM Tokens Over Time", "width": 6, "height": 50},
        ],
    },
    {
        "charts": [
            {"name": "Recent Events", "width": 12, "height": 50},
        ],
    },
]

# Native filters. ``__EVENTS_DS_ID__`` is substituted with the recent_events
# dataset id at runtime (it carries the raw source/event_type/level columns).
_FILTER_CONFIGS: list[dict[str, Any]] = [
    {
        "id": "NATIVE_FILTER-TIME",
        "name": "Time Range",
        "filterType": "filter_time",
        "targets": [{"datasetId": "__EVENTS_DS_ID__"}],
        "defaultDataMask": {"filterState": {"value": "Last day"}},
        "scope": {"rootPath": ["ROOT_ID"], "excluded": []},
    },
    {
        "id": "NATIVE_FILTER-SOURCE",
        "name": "Source",
        "filterType": "filter_select",
        "targets": [
            {"column": {"name": "source"}, "datasetId": "__EVENTS_DS_ID__"},
        ],
        "scope": {"rootPath": ["ROOT_ID"], "excluded": []},
    },
    {
        "id": "NATIVE_FILTER-EVENT-TYPE",
        "name": "Event Type",
        "filterType": "filter_select",
        "targets": [
            {"column": {"name": "event_type"}, "datasetId": "__EVENTS_DS_ID__"},
        ],
        "scope": {"rootPath": ["ROOT_ID"], "excluded": []},
    },
    {
        "id": "NATIVE_FILTER-LEVEL",
        "name": "Level",
        "filterType": "filter_select",
        "targets": [
            {"column": {"name": "level"}, "datasetId": "__EVENTS_DS_ID__"},
        ],
        "scope": {"rootPath": ["ROOT_ID"], "excluded": []},
    },
]


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------
def _get_database_uri() -> str:
    """Build the Docker-internal PostgreSQL URI for Superset.

    Uses the same environment variables docker-compose passes to the Superset
    containers, so it resolves ``postgres:5432`` from inside the network.
    """
    pg_user = os.getenv("POSTGRES_USER", "rfs")
    pg_pass = os.getenv("POSTGRES_PASSWORD", "changeme")
    pg_db = os.getenv("POSTGRES_DB", "rfs")
    pg_host = os.getenv("POSTGRES_HOST_INTERNAL", "postgres")
    pg_port = os.getenv("POSTGRES_INTERNAL_PORT", "5432")
    return f"postgresql://{pg_user}:{pg_pass}@{pg_host}:{pg_port}/{pg_db}"


def _split_ddl(ddl: str) -> list[str]:
    """Split a semicolon-separated DDL block into executable statements.

    Blank lines and SQL comment lines are stripped; comment-only fragments
    are dropped so they are never sent to the server.
    """
    statements: list[str] = []
    for statement in ddl.split(";"):
        executable = "\n".join(
            line
            for line in statement.splitlines()
            if line.strip() and not line.strip().startswith("--")
        ).strip()
        if executable:
            statements.append(executable)
    return statements


def _run_ddl(ddl: str) -> None:
    """Execute a semicolon-separated DDL block against the events database."""
    from sqlalchemy import create_engine, text

    engine = create_engine(_get_database_uri())
    try:
        with engine.begin() as conn:
            for statement in _split_ddl(ddl):
                conn.execute(text(statement))
    finally:
        engine.dispose()


def _create_events_table() -> None:
    """Create the events table if it does not already exist (idempotent)."""
    _run_ddl(_EVENTS_DDL)
    log.info("Ensured events table + indexes exist.")


def _ensure_database_connection() -> int | None:
    """Create or update the Superset database connection object."""
    from superset import db as superset_db  # type: ignore[import-not-found]
    from superset.models.core import Database  # type: ignore[import-not-found]

    uri = _get_database_uri()
    existing = (
        superset_db.session.query(Database)
        .filter_by(database_name=EVENTS_DB_NAME)
        .first()
    )
    if existing:
        existing.sqlalchemy_uri = uri
        superset_db.session.commit()
        log.info(f"Updated database connection: {EVENTS_DB_NAME} (id={existing.id})")
        return int(existing.id)

    new_db = Database(
        database_name=EVENTS_DB_NAME,
        sqlalchemy_uri=uri,
        expose_in_sqllab=True,
    )
    superset_db.session.add(new_db)
    superset_db.session.commit()
    log.info(f"Created database connection: {EVENTS_DB_NAME} (id={new_db.id})")
    return int(new_db.id)


def _create_datasets(db_id: int) -> dict[str, int]:
    """Create the physical events dataset and all virtual datasets.

    Returns a mapping of dataset name -> Superset dataset id.
    """
    from superset import db as superset_db  # type: ignore[import-not-found]
    from superset.connectors.sqla.models import (  # type: ignore[import-not-found]
        SqlaTable,
    )

    ids: dict[str, int] = {}

    def _register(name: str, sql: str | None) -> None:
        existing = (
            superset_db.session.query(SqlaTable)
            .filter_by(table_name=name, database_id=db_id)
            .first()
        )
        if existing:
            ids[name] = existing.id
            log.info(f"Dataset already exists: {name}")
            return
        dataset = SqlaTable(table_name=name, database_id=db_id, schema=None, sql=sql)
        superset_db.session.add(dataset)
        superset_db.session.commit()
        try:
            dataset.fetch_metadata()
            superset_db.session.commit()
        except Exception as exc:  # noqa: BLE001 - metadata probe is best-effort
            log.warning(f"fetch_metadata failed for {name}: {exc}")
        ids[name] = dataset.id
        log.info(f"Created dataset: {name} (id={dataset.id})")

    # Physical table first, then the SQL-backed virtual datasets.
    _register("events", None)
    for name, sql in _VIRTUAL_DATASETS.items():
        _register(name, sql.strip())

    return ids


def _build_position_json(chart_id_map: dict[str, int]) -> dict[str, Any]:
    """Build Superset ``position_json`` from _LAYOUT_SECTIONS."""
    layout: dict[str, Any] = {
        "DASHBOARD_VERSION_KEY": "v2",
        "ROOT_ID": {"type": "ROOT", "id": "ROOT_ID", "children": ["GRID_ID"]},
        "GRID_ID": {"type": "GRID", "id": "GRID_ID", "children": []},
        "HEADER_ID": {
            "type": "HEADER",
            "id": "HEADER_ID",
            "meta": {"text": DASHBOARD_TITLE},
        },
    }
    for row_counter, section in enumerate(_LAYOUT_SECTIONS):
        row_id = f"ROW-{row_counter}"
        row: dict[str, Any] = {
            "type": "ROW",
            "id": row_id,
            "children": [],
            "meta": {"background": "BACKGROUND_TRANSPARENT"},
        }
        layout["GRID_ID"]["children"].append(row_id)
        for chart_spec in section["charts"]:
            chart_name = chart_spec["name"]
            chart_db_id = chart_id_map.get(chart_name, 0)
            chart_key = f"CHART-{chart_db_id}"
            row["children"].append(chart_key)
            layout[chart_key] = {
                "type": "CHART",
                "id": chart_key,
                "children": [],
                "meta": {
                    "chartId": chart_db_id,
                    "width": chart_spec["width"],
                    "height": chart_spec["height"],
                    "sliceName": chart_name,
                },
            }
        layout[row_id] = row
    return layout


def _build_json_metadata(events_dataset_id: int) -> dict[str, Any]:
    """Build dashboard ``json_metadata`` with native filters bound to the data."""
    filters = []
    for fconf in _FILTER_CONFIGS:
        f = json.loads(json.dumps(fconf))  # deep copy
        for target in f.get("targets", []):
            if target.get("datasetId") == "__EVENTS_DS_ID__":
                target["datasetId"] = events_dataset_id
        filters.append(f)
    return {
        "native_filter_configuration": filters,
        "chart_configuration": {},
        "cross_filters_enabled": True,
    }


def _create_charts_and_dashboard(datasets: dict[str, int]) -> None:
    """Create charts and the consolidated observability dashboard."""
    from superset import db as superset_db  # type: ignore[import-not-found]
    from superset.models.dashboard import (  # type: ignore[import-not-found]
        Dashboard,
    )
    from superset.models.slice import Slice  # type: ignore[import-not-found]

    if not datasets:
        log.warning("No datasets found; skipping chart creation.")
        return

    chart_id_map: dict[str, int] = {}
    for chart_def in _CHART_DEFS:
        ds_key = chart_def["datasource_id_key"]
        if ds_key not in datasets:
            log.warning(
                f"Skipping chart '{chart_def['slice_name']}': "
                f"dataset '{ds_key}' not found."
            )
            continue
        slice_name = chart_def["slice_name"]
        existing = (
            superset_db.session.query(Slice).filter_by(slice_name=slice_name).first()
        )
        if existing:
            chart_id_map[slice_name] = existing.id
            log.info(f"Chart already exists: {slice_name}")
            continue
        chart = Slice(
            slice_name=slice_name,
            viz_type=chart_def["viz_type"],
            datasource_id=datasets[ds_key],
            datasource_type="table",
            params=json.dumps(chart_def["params"]),
        )
        superset_db.session.add(chart)
        superset_db.session.commit()
        chart_id_map[slice_name] = chart.id
        log.info(f"Created chart: {slice_name} (id={chart.id})")

    position = _build_position_json(chart_id_map)
    metadata = _build_json_metadata(datasets.get("recent_events", 0))
    slices = [
        s
        for cid in chart_id_map.values()
        if (s := superset_db.session.get(Slice, cid)) is not None
    ]

    existing_dash = (
        superset_db.session.query(Dashboard).filter_by(slug=DASHBOARD_SLUG).first()
    )
    if existing_dash:
        existing_dash.position_json = json.dumps(position)
        existing_dash.json_metadata = json.dumps(metadata)
        existing_dash.slices = slices
        superset_db.session.commit()
        log.info(f"Updated dashboard: {DASHBOARD_TITLE} (id={existing_dash.id})")
        return

    dashboard = Dashboard(
        dashboard_title=DASHBOARD_TITLE,
        slug=DASHBOARD_SLUG,
        published=True,
        position_json=json.dumps(position),
        json_metadata=json.dumps(metadata),
    )
    dashboard.slices = slices
    superset_db.session.add(dashboard)
    superset_db.session.commit()
    log.info(f"Created dashboard: {DASHBOARD_TITLE} (id={dashboard.id})")


def bootstrap() -> None:
    """Run the full bootstrap sequence inside the Superset app context."""
    try:
        from superset.app import create_app  # type: ignore[import-not-found]
    except ImportError:
        log.error("Superset is not installed. Run inside the Superset container.")
        sys.exit(1)

    app = create_app()
    with app.app_context():
        _create_events_table()
        db_id = _ensure_database_connection()
        if db_id is None:
            log.error("Failed to create database connection.")
            sys.exit(1)
        datasets = _create_datasets(db_id)
        _create_charts_and_dashboard(datasets)

    log.info("Bootstrap complete.")


if __name__ == "__main__":
    bootstrap()
