"""Loading: write the transformed model into PostgreSQL.

Strategy (full refresh, all-or-nothing):
  1. Within ONE transaction, truncate the core tables (reference data such as
     core.states is kept).
  2. Bulk-load each table into a temporary staging table with COPY.
  3. INSERT ... SELECT into core, resolving surrogate keys with joins on the
     natural keys (uids and address triples).
  4. Compare inserted row counts with staged row counts. Any difference (for
     example an orphan dropped by a join) aborts and rolls back the whole load.

Pipeline runs and data-quality issues are written on a separate autocommit
connection, so a failed load still leaves an audit record.
"""
from __future__ import annotations

import io
import logging
import os
from pathlib import Path

import pandas as pd
import psycopg
from dotenv import load_dotenv
from psycopg.types.json import Jsonb

from .validation import QualityReport

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Staging DDL, COPY columns and the INSERT that moves each table into core.
# Tables are listed in foreign-key dependency order.
LOAD_STEPS: list[dict[str, str]] = [
    {
        "table": "calendar",
        "staging": """date_key date, year smallint, quarter smallint, month smallint,
                      month_name text, year_month text, iso_week smallint, day_of_month smallint,
                      day_of_week smallint, day_name text, is_weekend boolean, is_holiday boolean,
                      holiday_name text""",
        "insert": """INSERT INTO core.calendar
                     SELECT date_key, year, quarter, month, month_name, year_month, iso_week,
                            day_of_month, day_of_week, day_name, is_weekend, is_holiday, holiday_name
                     FROM stg_calendar""",
    },
    {
        "table": "locations",
        "staging": "zip_code_prefix text, city text, state_code text, latitude numeric, longitude numeric",
        "insert": """INSERT INTO core.locations (zip_code_prefix, city, state_code, latitude, longitude)
                     SELECT zip_code_prefix, city, state_code, latitude, longitude
                     FROM stg_locations""",
    },
    {
        "table": "categories",
        "staging": "category_name_pt text, category_name_en text, estimated_cost_ratio numeric",
        "insert": """INSERT INTO core.categories (category_name_pt, category_name_en, estimated_cost_ratio)
                     SELECT category_name_pt, category_name_en, estimated_cost_ratio
                     FROM stg_categories""",
    },
    {
        "table": "products",
        "staging": """product_uid text, category_name_pt text, name_length smallint,
                      description_length smallint, photos_qty smallint, weight_g integer,
                      length_cm smallint, height_cm smallint, width_cm smallint,
                      estimated_cost_ratio numeric""",
        "insert": """INSERT INTO core.products (product_uid, category_key, name_length, description_length,
                                                photos_qty, weight_g, length_cm, height_cm, width_cm,
                                                estimated_cost_ratio)
                     SELECT s.product_uid, c.category_key, s.name_length, s.description_length,
                            s.photos_qty, s.weight_g, s.length_cm, s.height_cm, s.width_cm,
                            s.estimated_cost_ratio
                     FROM stg_products s
                     JOIN core.categories c ON c.category_name_pt = s.category_name_pt""",
    },
    {
        "table": "sellers",
        "staging": "seller_uid text, zip_code_prefix text, city text, state_code text",
        "insert": """INSERT INTO core.sellers (seller_uid, location_key)
                     SELECT s.seller_uid, l.location_key
                     FROM stg_sellers s
                     JOIN core.locations l USING (zip_code_prefix, city, state_code)""",
    },
    {
        "table": "customers",
        "staging": "customer_uid text, zip_code_prefix text, city text, state_code text",
        "insert": """INSERT INTO core.customers (customer_uid, location_key)
                     SELECT s.customer_uid, l.location_key
                     FROM stg_customers s
                     JOIN core.locations l USING (zip_code_prefix, city, state_code)""",
    },
    {
        "table": "orders",
        "staging": """order_uid text, customer_uid text, delivery_zip_code_prefix text,
                      delivery_city text, delivery_state_code text, order_status text,
                      purchased_at timestamp, approved_at timestamp, delivered_carrier_at timestamp,
                      delivered_customer_at timestamp, estimated_delivery_date date""",
        "insert": """INSERT INTO core.orders (order_uid, customer_key, delivery_location_key, order_status,
                                              purchased_at, approved_at, delivered_carrier_at,
                                              delivered_customer_at, estimated_delivery_date)
                     SELECT s.order_uid, c.customer_key, l.location_key, s.order_status,
                            s.purchased_at, s.approved_at, s.delivered_carrier_at,
                            s.delivered_customer_at, s.estimated_delivery_date
                     FROM stg_orders s
                     JOIN core.customers c ON c.customer_uid = s.customer_uid
                     JOIN core.locations l ON l.zip_code_prefix = s.delivery_zip_code_prefix
                                          AND l.city = s.delivery_city
                                          AND l.state_code = s.delivery_state_code""",
    },
    {
        "table": "order_items",
        "staging": """order_uid text, line_number smallint, product_uid text, seller_uid text,
                      shipping_limit_at timestamp, unit_price numeric, freight_value numeric""",
        "insert": """INSERT INTO core.order_items (order_key, line_number, product_key, seller_key,
                                                   shipping_limit_at, unit_price, freight_value)
                     SELECT o.order_key, s.line_number, p.product_key, se.seller_key,
                            s.shipping_limit_at, s.unit_price, s.freight_value
                     FROM stg_order_items s
                     JOIN core.orders   o  ON o.order_uid   = s.order_uid
                     JOIN core.products p  ON p.product_uid = s.product_uid
                     JOIN core.sellers  se ON se.seller_uid = s.seller_uid""",
    },
    {
        "table": "order_payments",
        "staging": """order_uid text, payment_sequential smallint, payment_type text,
                      installments smallint, amount numeric""",
        "insert": """INSERT INTO core.order_payments (order_key, payment_sequential, payment_type,
                                                      installments, amount)
                     SELECT o.order_key, s.payment_sequential, s.payment_type, s.installments, s.amount
                     FROM stg_order_payments s
                     JOIN core.orders o ON o.order_uid = s.order_uid""",
    },
    {
        "table": "order_reviews",
        "staging": """review_uid text, order_uid text, score smallint, comment_title text,
                      comment_message text, review_created_at timestamp, review_answered_at timestamp""",
        "insert": """INSERT INTO core.order_reviews (review_uid, order_key, score, comment_title,
                                                     comment_message, review_created_at, review_answered_at)
                     SELECT s.review_uid, o.order_key, s.score, s.comment_title, s.comment_message,
                            s.review_created_at, s.review_answered_at
                     FROM stg_order_reviews s
                     JOIN core.orders o ON o.order_uid = s.order_uid""",
    },
]

CORE_TABLES = [step["table"] for step in LOAD_STEPS]


def connect(autocommit: bool = False) -> psycopg.Connection:
    """Open a connection using the POSTGRES_* variables from the environment / .env file."""
    load_dotenv(PROJECT_ROOT / ".env")
    missing = [v for v in ("POSTGRES_HOST", "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD")
               if not os.getenv(v)]
    if missing:
        raise RuntimeError(f"Missing environment variables: {', '.join(missing)} (see .env.example)")
    return psycopg.connect(
        host=os.environ["POSTGRES_HOST"],
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        autocommit=autocommit,
        connect_timeout=10,
    )


def _copy_frame(cur: psycopg.Cursor, table: str, df: pd.DataFrame) -> None:
    """Stream a DataFrame into a table with COPY (CSV; empty field = NULL)."""
    buffer = io.StringIO()
    df.to_csv(buffer, index=False, header=False, na_rep="", date_format="%Y-%m-%d %H:%M:%S")
    columns = ", ".join(df.columns)
    with cur.copy(f"COPY {table} ({columns}) FROM STDIN WITH (FORMAT csv)") as copy:
        copy.write(buffer.getvalue())


def load_model(conn: psycopg.Connection, model: dict[str, pd.DataFrame]) -> dict[str, int]:
    """Replace the contents of the core tables with ``model``. Returns rows loaded per table."""
    loaded: dict[str, int] = {}
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("TRUNCATE " + ", ".join(f"core.{t}" for t in CORE_TABLES) + " RESTART IDENTITY CASCADE")
        for step in LOAD_STEPS:
            table, staging = step["table"], f"stg_{step['table']}"
            df = model[table]
            cur.execute(f"CREATE TEMP TABLE {staging} ({step['staging']}) ON COMMIT DROP")
            _copy_frame(cur, staging, df)
            cur.execute(step["insert"])
            if cur.rowcount != len(df):
                raise RuntimeError(
                    f"core.{table}: staged {len(df):,} rows but inserted {cur.rowcount:,}; "
                    "some natural keys did not resolve. Load rolled back.")
            loaded[table] = cur.rowcount
            log.info("Loaded core.%-15s %9s rows", table, f"{cur.rowcount:,}")
    # Refresh planner statistics after a bulk load.
    with conn.cursor() as cur:
        for table in CORE_TABLES:
            cur.execute(f"ANALYZE core.{table}")
    conn.commit()
    return loaded


# Materialized analytics views, in dependency order (see sql/views.sql).
MATERIALIZED_VIEWS = ["analytics.v_orders", "analytics.v_customer_summary"]


def refresh_analytics(conn: psycopg.Connection) -> list[str]:
    """Refresh materialized views so analytics reflect the new load. Skips views not yet created."""
    existing = {f"{r[0]}.{r[1]}" for r in conn.execute(
        "SELECT schemaname, matviewname FROM pg_matviews WHERE schemaname = 'analytics'").fetchall()}
    refreshed = []
    with conn.transaction():
        for view in MATERIALIZED_VIEWS:
            if view in existing:
                conn.execute(f"REFRESH MATERIALIZED VIEW {view}")
                refreshed.append(view)
    return refreshed


def start_run(conn: psycopg.Connection, source: str, rows_read: int) -> int:
    return conn.execute(
        "INSERT INTO ops.pipeline_runs (source, rows_read) VALUES (%s, %s) RETURNING run_id",
        (source, rows_read),
    ).fetchone()[0]


def finish_run(conn: psycopg.Connection, run_id: int, status: str,
               rows_loaded: int | None = None, error: str | None = None) -> None:
    conn.execute(
        """UPDATE ops.pipeline_runs
           SET finished_at = now(), status = %s, rows_loaded = %s, error_message = %s
           WHERE run_id = %s""",
        (status, rows_loaded, error, run_id),
    )


def save_issues(conn: psycopg.Connection, run_id: int, report: QualityReport) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO ops.data_quality_issues
                   (run_id, source_table, check_name, category, severity, rows_affected,
                    action_taken, details)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            [(run_id, i.source_table, i.check_name, i.category, i.severity, i.rows_affected,
              i.action_taken, Jsonb(i.details) if i.details else None) for i in report.issues],
        )
