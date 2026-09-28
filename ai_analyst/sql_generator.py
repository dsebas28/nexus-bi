"""Schema context for SQL generation, and safe execution of validated SQL.

The model never sees credentials or raw tables: it gets a catalogue of the
analytics views (introspected from PostgreSQL, so column lists never drift from
the database) with a one-line description each, plus the live reporting period.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

import psycopg
from psycopg.types.numeric import FloatLoader

from backend.config import get_settings

from .validation import ALLOWED_RELATIONS, MAX_ROWS, validate_sql

PROMPTS = Path(__file__).resolve().parent / "prompts"
ROWS_FOR_MODEL = 50        # rows of each result sent back to the model
STATEMENT_TIMEOUT_MS = 10_000

DESCRIPTIONS = {
    "analytics.v_reporting_period": "One row: reliable data window (data_start, data_end), first/last complete month and the reference date used as 'today'.",
    "analytics.v_month_coverage": "Orders per calendar month and whether the month is complete (is_complete).",
    "analytics.v_daily_coverage": "Orders per day and whether the day is inside the reliable data floor.",
    "analytics.v_sales_items": "One row per item SOLD (canceled/unavailable orders excluded): revenue (= item price), freight, estimated cost/profit, category, product, seller, customer, purchase_date, order_month.",
    "analytics.v_orders": "One row per order, ALL statuses (filter is_valid_sale = true for sales): revenue, freight, estimated profit, items, payment, delivery_days, promised_days, is_late, review_score, state_code, city, customer_order_seq (1 = customer's first purchase).",
    "analytics.v_monthly_kpis": "Monthly KPIs (only complete months are reliable): revenue, estimated_profit/margin, orders, customers, new/returning customers, avg_order_value, revenue_growth_mom, revenue_growth_yoy (ratios, 0.05 = +5%), revenue_ytd.",
    "analytics.v_customer_summary": "One row per customer up to the reference date: orders, total_revenue (historical CLV), avg_order_value, recency_days, tenure_days, avg_days_between_orders, avg_review_score, late_deliveries, is_repeat_customer, state_code, city.",
    "analytics.v_product_performance": "One row per product (all time): units, orders, revenue, estimated profit/margin, avg_price, first/last sale, days_since_last_sale, units_last_90d, revenue ranks. Product names are anonymised (product_short_id).",
    "analytics.v_category_performance": "One row per category (all time): revenue, estimated profit/margin, units, orders, revenue_share, revenue_last_3m vs revenue_prev_3m and growth_3m (last 3 complete months vs the 3 before).",
    "analytics.v_geo_state": "One row per state (all time): revenue, estimated profit/margin, orders, customers, avg_order_value, delivery days, late_delivery_rate, avg_review_score, revenue_last_6m vs revenue_prev_6m and growth_6m.",
    "analytics.v_geo_city": "One row per city (all time): revenue, estimated profit/margin, orders, customers, revenue_last_6m vs revenue_prev_6m, growth_6m, coordinates.",
    "analytics.v_customer_segments": "RFM segment per customer (VIP, Loyal, Potential, At Risk, Lost) with recency_days, frequency, monetary and R/F/M scores.",
    "analytics.v_segment_summary": "One row per RFM segment: customers, shares, revenue, averages, the rule that defines it, and the recommended action.",
    "analytics.v_churn_scores": "Churn model output per customer: churn_probability (probability of NOT buying again within 180 days), risk_band (High/Medium/Low tertiles), top_factors (JSON array of {feature, description, impact_pts}), segment, monetary.",
    "analytics.v_sales_forecast": "Daily revenue: kind = 'actual' for history, 'forecast' for the next 8 weeks, with lower_80/upper_80 bounds on forecast rows.",
    "analytics.v_anomalies": "Detected anomalies: entity_type (day, product, customer), period, metric, observed vs expected, score, direction (up/down/mixed), method and a plain-language description.",
    "analytics.v_model_evaluations": "Metrics of every candidate model of the active ML runs (model_name, candidate, split, metric, value).",
    "analytics.v_data_quality_latest": "Data-quality checks of the latest pipeline run: check_name, category, rows_affected, action_taken.",
    "core.states": "Brazilian states: state_code, state_name, region.",
    "core.categories": "Product categories: category_key, category_name_pt, category_name_en (the English name is used everywhere else as 'category').",
    "core.calendar": "Date dimension: date_key, year, quarter, month, month_name, iso_week, day_of_week (1 = Monday), day_name, is_weekend, is_holiday, holiday_name.",
    "core.order_reviews": "Customer reviews: order_key, score (1-5), comment_title, comment_message (Portuguese free text), review_created_at.",
}

FUNCTIONS = """\
- analytics.kpi_summary(p_start date, p_end date, p_state text DEFAULT NULL, p_category_key smallint DEFAULT NULL)
  RETURNS (metric, current_value, previous_value, change_abs, change_pct): KPI comparison of a period vs the previous
  one (whole calendar months compare with the preceding calendar months). metrics: revenue, estimated_profit,
  estimated_margin, orders, customers, avg_order_value. change_pct is in percent (1.7 = +1.7%).
- analytics.previous_period(p_start date, p_end date) RETURNS (prev_start, prev_end)."""


def _connect() -> psycopg.Connection:
    """Dedicated read-only role when configured (sql/roles.sql); otherwise the app role in READ ONLY transactions."""
    s = get_settings()
    user, password = (s.ai_db_user, s.ai_db_password) if s.ai_db_user and s.ai_db_password else (s.postgres_user, s.postgres_password)
    conn = psycopg.connect(host=s.postgres_host, port=s.postgres_port, dbname=s.postgres_db, user=user,
                           password=password, connect_timeout=10, application_name="nexus_bi_ai_analyst")
    conn.adapters.register_loader("numeric", FloatLoader)
    return conn


def db_role() -> str:
    s = get_settings()
    return s.ai_db_user if s.ai_db_user and s.ai_db_password else f"{s.postgres_user} (read-only transactions)"


@lru_cache(maxsize=1)
def schema_context() -> str:
    """Catalogue of allowed relations with their columns, plus reference data the model needs."""
    with _connect() as conn:
        cols = conn.execute("""
            SELECT n.nspname || '.' || c.relname AS rel, a.attname, format_type(a.atttypid, a.atttypmod)
            FROM pg_attribute a
            JOIN pg_class c ON c.oid = a.attrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE a.attnum > 0 AND NOT a.attisdropped AND n.nspname || '.' || c.relname = ANY(%s)
            ORDER BY 1, a.attnum
        """, (sorted(ALLOWED_RELATIONS),)).fetchall()
        period = conn.execute("SELECT * FROM analytics.v_reporting_period").fetchone()
        states = conn.execute("SELECT state_code || '=' || state_name FROM core.states ORDER BY 1").fetchall()
        cats = conn.execute("SELECT category_name_en FROM core.categories ORDER BY 1").fetchall()
        conn.rollback()

    by_rel: dict[str, list[str]] = {}
    for rel, col, typ in cols:
        by_rel.setdefault(rel, []).append(f"{col} {typ}")
    lines = []
    for rel in sorted(by_rel):
        lines.append(f"### {rel}\n{DESCRIPTIONS.get(rel, '')}\nColumns: {', '.join(by_rel[rel])}")
    data_start, data_end, first_month, last_month, reference = period
    return "\n\n".join([
        "## Reporting period (live values)",
        (f"Reliable data: {data_start} to {data_end}. Complete months: {first_month} to {last_month}. "
         f"Reference date ('today'): {reference}. 'This month' / 'last month' / 'current period' mean the last "
         f"complete month ({last_month}) unless the user names another period."),
        "## Table functions", FUNCTIONS,
        "## Views and tables you can query", "\n\n".join(lines),
        "## Reference values",
        "States: " + ", ".join(r[0] for r in states),
        "Categories (category / category_name_en): " + ", ".join(r[0] for r in cats),
    ])


# Views most questions need. A local model on CPU pays for every prompt token, so it gets
# this smaller catalogue (column names only) instead of the full one.
COMPACT_RELATIONS = (
    "analytics.v_reporting_period", "analytics.v_monthly_kpis", "analytics.v_sales_items", "analytics.v_orders",
    "analytics.v_category_performance", "analytics.v_product_performance", "analytics.v_geo_state",
    "analytics.v_geo_city", "analytics.v_customer_summary", "analytics.v_churn_scores", "analytics.v_segment_summary",
    "analytics.v_anomalies",
)


@lru_cache(maxsize=1)
def compact_schema_context() -> str:
    with _connect() as conn:
        cols = conn.execute("""
            SELECT n.nspname || '.' || c.relname, string_agg(a.attname, ', ' ORDER BY a.attnum)
            FROM pg_attribute a
            JOIN pg_class c ON c.oid = a.attrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE a.attnum > 0 AND NOT a.attisdropped AND n.nspname || '.' || c.relname = ANY(%s)
            GROUP BY 1 ORDER BY 1
        """, (list(COMPACT_RELATIONS),)).fetchall()
        period = conn.execute("SELECT last_month, reference_date FROM analytics.v_reporting_period").fetchone()
        conn.rollback()
    short = {rel: DESCRIPTIONS[rel].split(":")[0].split(".")[0] for rel in COMPACT_RELATIONS}
    lines = [f"- {rel} ({short[rel]}): {columns}" for rel, columns in cols]
    return "\n".join([
        f"Last complete month: {period[0]}. Reference date ('today'): {period[1]}.",
        "Function: analytics.kpi_summary(start date, end date) -> metric, current_value, previous_value, change_pct "
        "(metrics: revenue, estimated_profit, estimated_margin, orders, customers, avg_order_value; change_pct in %).",
        "Views (use schema-qualified names):",
        *lines,
    ])


@lru_cache(maxsize=1)
def system_prompt() -> str:
    return (PROMPTS / "system.md").read_text(encoding="utf-8") + "\n\n" + schema_context()


def _jsonable(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (dict, list, str, int, float, bool)) or value is None:
        return value
    return str(value)


@dataclass
class QueryStep:
    purpose: str
    sql: str
    columns: list[str] = field(default_factory=list)
    rows: list[list] = field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    error: str | None = None
    duration_ms: float = 0.0

    def for_model(self) -> str:
        """Compact JSON result for the model (first ROWS_FOR_MODEL rows)."""
        if self.error:
            return json.dumps({"error": self.error})
        return json.dumps({
            "columns": self.columns,
            "rows": self.rows[:ROWS_FOR_MODEL],
            "row_count": self.row_count,
            "rows_shown": min(self.row_count, ROWS_FOR_MODEL),
            "truncated_at": MAX_ROWS if self.truncated else None,
        }, default=str)


def run_sql(sql: str, purpose: str) -> QueryStep:
    """Validate, then execute in a READ ONLY transaction with a statement timeout."""
    step = QueryStep(purpose=purpose, sql=sql)
    started = time.perf_counter()
    try:
        validated = validate_sql(sql)
        step.sql = validated.original
        with _connect() as conn:
            conn.execute("SET TRANSACTION READ ONLY")
            conn.execute(f"SET LOCAL statement_timeout = {STATEMENT_TIMEOUT_MS}")
            cur = conn.execute(validated.executable)
            rows = cur.fetchall()
            step.columns = [d.name for d in cur.description]
            conn.rollback()
        step.truncated = len(rows) > MAX_ROWS
        step.rows = [[_jsonable(v) for v in r] for r in rows[:MAX_ROWS]]
        step.row_count = len(step.rows)
    except ValueError as exc:                      # SQLValidationError
        step.error = f"Rejected by the SQL validator: {exc}"
    except psycopg.errors.QueryCanceled:
        step.error = f"The query exceeded the {STATEMENT_TIMEOUT_MS // 1000}s time limit. Simplify it."
    except psycopg.Error as exc:
        step.error = f"PostgreSQL error: {(exc.diag.message_primary or str(exc)).strip()}"
    step.duration_ms = round((time.perf_counter() - started) * 1000, 1)
    return step
