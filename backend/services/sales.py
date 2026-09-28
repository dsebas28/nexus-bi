"""KPI cards and sales analytics (revenue, profit, margin, orders, categories, time)."""
from __future__ import annotations

from datetime import date
from typing import Any

from ..models.database import fetch_all
from .cache import cached

# Reusable filter fragment over analytics.v_sales_items (alias s) joined to core.locations (alias l).
ITEM_FILTERS = """
    (%(state)s::text IS NULL OR l.state_code = %(state)s)
    AND (%(category)s::int IS NULL OR s.category_key = %(category)s)
"""


@cached
def kpis(start: date, end: date, state: str | None, category: int | None) -> list[dict[str, Any]]:
    return fetch_all(
        "SELECT * FROM analytics.kpi_summary(%(start)s, %(end)s, %(state)s, %(category)s::smallint)",
        {"start": start, "end": end, "state": state, "category": category},
    )


@cached
def monthly(state: str | None, category: int | None, include_incomplete: bool) -> list[dict[str, Any]]:
    return fetch_all(f"""
        WITH sales AS (
            SELECT s.order_month                  AS month,
                   sum(s.revenue)                 AS revenue,
                   sum(s.estimated_profit)        AS estimated_profit,
                   count(DISTINCT s.order_key)    AS orders,
                   count(DISTINCT s.customer_key) AS customers
            FROM analytics.v_sales_items s
            JOIN core.locations l ON l.location_key = s.delivery_location_key
            WHERE {ITEM_FILTERS}
            GROUP BY s.order_month
        ),
        series AS (
            SELECT m.month, m.is_complete,
                   coalesce(s.revenue, 0)          AS revenue,
                   coalesce(s.estimated_profit, 0) AS estimated_profit,
                   coalesce(s.orders, 0)           AS orders,
                   coalesce(s.customers, 0)        AS customers
            FROM analytics.v_month_coverage m
            LEFT JOIN sales s USING (month)
        )
        SELECT month, is_complete, revenue, estimated_profit,
               round(estimated_profit / nullif(revenue, 0), 4)            AS estimated_margin,
               orders, customers,
               round(revenue / nullif(orders, 0), 2)                      AS avg_order_value,
               CASE WHEN is_complete AND lag(is_complete) OVER w
                    THEN round(revenue / nullif(lag(revenue) OVER w, 0) - 1, 4) END AS revenue_growth_mom
        FROM series
        WHERE is_complete OR %(include_incomplete)s
        WINDOW w AS (ORDER BY month)
        ORDER BY month
    """, {"state": state, "category": category, "include_incomplete": include_incomplete})


@cached
def daily(start: date, end: date, state: str | None, category: int | None) -> list[dict[str, Any]]:
    return fetch_all(f"""
        SELECT c.date_key AS day,
               coalesce(sum(s.revenue), 0)       AS revenue,
               count(DISTINCT s.order_key)       AS orders
        FROM core.calendar c
        LEFT JOIN (analytics.v_sales_items s
                   JOIN core.locations l ON l.location_key = s.delivery_location_key AND {ITEM_FILTERS})
               ON s.purchase_date = c.date_key
        WHERE c.date_key BETWEEN %(start)s AND %(end)s
        GROUP BY c.date_key
        ORDER BY c.date_key
    """, {"start": start, "end": end, "state": state, "category": category})


@cached
def by_category(start: date, end: date, prev_start: date, state: str | None,
                category: int | None) -> list[dict[str, Any]]:
    return fetch_all(f"""
        WITH base AS (
            SELECT s.*, s.purchase_date >= %(start)s AS is_current
            FROM analytics.v_sales_items s
            JOIN core.locations l ON l.location_key = s.delivery_location_key
            WHERE s.purchase_date BETWEEN %(prev_start)s AND %(end)s AND {ITEM_FILTERS}
        ),
        agg AS (
            SELECT category_key, category,
                   sum(revenue)          FILTER (WHERE is_current)     AS revenue,
                   sum(estimated_profit) FILTER (WHERE is_current)     AS estimated_profit,
                   count(*)              FILTER (WHERE is_current)     AS units,
                   count(DISTINCT order_key) FILTER (WHERE is_current) AS orders,
                   sum(revenue)          FILTER (WHERE NOT is_current) AS revenue_previous
            FROM base
            GROUP BY category_key, category
        )
        SELECT category_key, category,
               coalesce(revenue, 0)                                          AS revenue,
               coalesce(estimated_profit, 0)                                 AS estimated_profit,
               round(estimated_profit / nullif(revenue, 0), 4)               AS estimated_margin,
               coalesce(units, 0)                                            AS units,
               coalesce(orders, 0)                                           AS orders,
               round(coalesce(revenue, 0) / nullif(sum(revenue) OVER (), 0), 4) AS revenue_share,
               coalesce(revenue_previous, 0)                                 AS revenue_previous,
               round(revenue / nullif(revenue_previous, 0) - 1, 4)           AS revenue_growth
        FROM agg
        WHERE revenue > 0
        ORDER BY revenue DESC
    """, {"start": start, "end": end, "prev_start": prev_start, "state": state, "category": category})


@cached
def weekday_hour(start: date, end: date, state: str | None, category: int | None) -> list[dict[str, Any]]:
    return fetch_all(f"""
        SELECT c.day_of_week, c.day_name, extract(hour FROM o.purchased_at)::int AS hour,
               count(DISTINCT s.order_key) AS orders
        FROM analytics.v_sales_items s
        JOIN core.locations l ON l.location_key = s.delivery_location_key
        JOIN core.orders o     ON o.order_key = s.order_key
        JOIN core.calendar c   ON c.date_key = s.purchase_date
        WHERE s.purchase_date BETWEEN %(start)s AND %(end)s AND {ITEM_FILTERS}
        GROUP BY 1, 2, 3
        ORDER BY 1, 3
    """, {"start": start, "end": end, "state": state, "category": category})
