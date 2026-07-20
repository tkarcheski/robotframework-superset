"""Create the event schema and starter Robot Framework observability dashboard.

Run this inside the Superset container after ``superset init``. The bootstrap
is idempotent: existing database connections, datasets, charts, and the
dashboard are updated in place.
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
            ROUND((PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY duration_ns)
                / 1000000.0)::numeric, 3) AS p95_duration_ms
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
    "rfs_llm_usage": """
        SELECT
            DATE_TRUNC('hour', wall_clock) AS time_bucket,
            source,
            COALESCE(payload->>'model', 'unknown') AS model,
            SUM(COALESCE(NULLIF(payload->>'prompt_tokens', '')::bigint, 0))
                AS prompt_tokens,
            SUM(COALESCE(NULLIF(payload->>'completion_tokens', '')::bigint, 0))
                AS completion_tokens,
            SUM(COALESCE(NULLIF(payload->>'total_tokens', '')::bigint, 0))
                AS total_tokens,
            SUM(COALESCE(NULLIF(payload->>'cost_usd', '')::numeric, 0))
                AS cost_usd
        FROM events
        WHERE event_type IN ('openai.response', 'ollama.response')
        GROUP BY DATE_TRUNC('hour', wall_clock), source, payload->>'model'
        ORDER BY time_bucket
    """,
}

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
                "p95_duration_ms",
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

    name = "Robot Framework Events"
    database = superset_db.session.query(Database).filter_by(database_name=name).first()
    if database is None:
        database = Database(
            database_name=name,
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
    for name, sql in VIRTUAL_DATASETS.items():
        datasets[name] = int(_upsert_dataset(database_id, name, sql.strip()).id)
    return datasets


def dashboard_layout(chart_ids: dict[str, int]) -> dict[str, Any]:
    """Build a compact two-column Superset dashboard layout."""
    grid_children: list[str] = []
    layout: dict[str, Any] = {
        "DASHBOARD_VERSION_KEY": "v2",
        "ROOT_ID": {"id": "ROOT_ID", "type": "ROOT", "children": ["GRID_ID"]},
        "GRID_ID": {
            "id": "GRID_ID",
            "type": "GRID",
            "parents": ["ROOT_ID"],
            "children": grid_children,
        },
        "HEADER_ID": {
            "id": "HEADER_ID",
            "type": "HEADER",
            "meta": {"text": "RF + LLM Observability"},
        },
    }
    for index, (name, chart_id) in enumerate(chart_ids.items()):
        row_number = index // 2 + 1
        row_id = f"ROW-{row_number}"
        if row_id not in layout:
            grid_children.append(row_id)
            layout[row_id] = {
                "id": row_id,
                "type": "ROW",
                "parents": ["ROOT_ID", "GRID_ID"],
                "children": [],
                "meta": {
                    "background": "BACKGROUND_TRANSPARENT",
                    "width": 12,
                },
            }

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
                "width": 6,
                "height": 50 if index < 2 else 40,
            },
        }
    return layout


def _ensure_charts_and_dashboard(datasets: dict[str, int]) -> None:
    from superset import db as superset_db  # type: ignore[attr-defined]
    from superset.models.dashboard import Dashboard
    from superset.models.slice import Slice

    chart_ids: dict[str, int] = {}
    chart_objects: list[Any] = []
    for definition in CHART_DEFINITIONS:
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

    slug = "rf-llm-observability"
    dashboard = superset_db.session.query(Dashboard).filter_by(slug=slug).first()
    if dashboard is None:
        dashboard = Dashboard(dashboard_title="RF + LLM Observability", slug=slug)
        superset_db.session.add(dashboard)
    dashboard.dashboard_title = "RF + LLM Observability"
    dashboard.published = True
    dashboard.position_json = json.dumps(dashboard_layout(chart_ids))
    dashboard.json_metadata = json.dumps(
        {
            "native_filter_configuration": [],
            "timed_refresh_immune_slices": [],
            "refresh_frequency": 0,
        }
    )
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
