"""Geographic analytics: sales, customers, growth and profitability by state and city."""
from __future__ import annotations

from datetime import date
from typing import Any

from ..models.database import fetch_all
from .cache import cached


@cached
def states(start: date, end: date, prev_start: date, category: int | None) -> list[dict[str, Any]]:
    """Sales metrics honour the category filter; delivery metrics are order-level (all categories)."""
    return fetch_all("""
        WITH items AS (
            SELECT l.state_code, s.revenue, s.estimated_profit, s.order_key, s.customer_key,
                   s.purchase_date >= %(start)s AS is_current
            FROM analytics.v_sales_items s
            JOIN core.locations l ON l.location_key = s.delivery_location_key
            WHERE s.purchase_date BETWEEN %(prev_start)s AND %(end)s
              AND (%(category)s::int IS NULL OR s.category_key = %(category)s)
        ),
        sales AS (
            SELECT state_code,
                   sum(revenue)          FILTER (WHERE is_current)          AS revenue,
                   sum(estimated_profit) FILTER (WHERE is_current)          AS estimated_profit,
                   count(DISTINCT order_key)    FILTER (WHERE is_current)   AS orders,
                   count(DISTINCT customer_key) FILTER (WHERE is_current)   AS customers,
                   sum(revenue)          FILTER (WHERE NOT is_current)      AS revenue_previous
            FROM items GROUP BY state_code
        ),
        delivery AS (
            SELECT state_code,
                   round(avg(delivery_days), 1)      AS avg_delivery_days,
                   round(avg(is_late::int), 4)       AS late_delivery_rate,
                   round(avg(review_score), 2)       AS avg_review_score
            FROM analytics.v_orders
            WHERE is_valid_sale AND purchase_date BETWEEN %(start)s AND %(end)s
            GROUP BY state_code
        )
        SELECT st.state_code, st.state_name, st.region,
               coalesce(s.revenue, 0)                                      AS revenue,
               coalesce(s.estimated_profit, 0)                             AS estimated_profit,
               round(s.estimated_profit / nullif(s.revenue, 0), 4)         AS estimated_margin,
               coalesce(s.orders, 0)                                       AS orders,
               coalesce(s.customers, 0)                                    AS customers,
               round(s.revenue / nullif(s.orders, 0), 2)                   AS avg_order_value,
               coalesce(s.revenue_previous, 0)                             AS revenue_previous,
               round(s.revenue / nullif(s.revenue_previous, 0) - 1, 4)     AS revenue_growth,
               d.avg_delivery_days, d.late_delivery_rate, d.avg_review_score,
               g.latitude, g.longitude
        FROM core.states st
        LEFT JOIN sales s    USING (state_code)
        LEFT JOIN delivery d USING (state_code)
        LEFT JOIN analytics.v_geo_state g USING (state_code)
        ORDER BY revenue DESC
    """, {"start": start, "end": end, "prev_start": prev_start, "category": category})


@cached
def cities(start: date, end: date, prev_start: date, state: str | None, category: int | None,
           limit: int) -> list[dict[str, Any]]:
    return fetch_all("""
        WITH items AS (
            SELECT l.state_code, l.city, s.revenue, s.estimated_profit, s.order_key, s.customer_key,
                   s.purchase_date >= %(start)s AS is_current
            FROM analytics.v_sales_items s
            JOIN core.locations l ON l.location_key = s.delivery_location_key
            WHERE s.purchase_date BETWEEN %(prev_start)s AND %(end)s
              AND (%(state)s::text IS NULL OR l.state_code = %(state)s)
              AND (%(category)s::int IS NULL OR s.category_key = %(category)s)
        ),
        agg AS (
            SELECT state_code, city,
                   sum(revenue)          FILTER (WHERE is_current)        AS revenue,
                   sum(estimated_profit) FILTER (WHERE is_current)        AS estimated_profit,
                   count(DISTINCT order_key)    FILTER (WHERE is_current) AS orders,
                   count(DISTINCT customer_key) FILTER (WHERE is_current) AS customers,
                   sum(revenue)          FILTER (WHERE NOT is_current)    AS revenue_previous
            FROM items GROUP BY state_code, city
        )
        SELECT a.state_code, a.city,
               a.revenue, a.estimated_profit,
               round(a.estimated_profit / nullif(a.revenue, 0), 4)        AS estimated_margin,
               a.orders, a.customers,
               coalesce(a.revenue_previous, 0)                             AS revenue_previous,
               round(a.revenue / nullif(a.revenue_previous, 0) - 1, 4)     AS revenue_growth,
               g.latitude, g.longitude
        FROM agg a
        LEFT JOIN analytics.v_geo_city g USING (state_code, city)
        WHERE a.revenue > 0
        ORDER BY a.revenue DESC
        LIMIT %(limit)s
    """, {"start": start, "end": end, "prev_start": prev_start, "state": state, "category": category,
          "limit": limit})
