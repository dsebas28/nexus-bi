"""Product analytics: best sellers, most profitable, low rotation, high revenue / low margin."""
from __future__ import annotations

from datetime import date
from typing import Any

from ..models.database import fetch_all
from .cache import cached
from .sales import ITEM_FILTERS

# Whitelist: user input selects a key, never an SQL fragment.
SORT_COLUMNS = {
    "revenue": "revenue DESC",
    "units": "units DESC",
    "estimated_profit": "estimated_profit DESC",
    "estimated_margin": "estimated_margin DESC",
}


@cached
def top(start: date, end: date, state: str | None, category: int | None, sort_by: str, limit: int,
        min_units: int) -> list[dict[str, Any]]:
    order = SORT_COLUMNS[sort_by]
    return fetch_all(f"""
        SELECT s.product_key,
               left(p.product_uid, 8)                                         AS product_short_id,
               s.category,
               count(*)                                                       AS units,
               count(DISTINCT s.order_key)                                    AS orders,
               sum(s.revenue)                                                 AS revenue,
               sum(s.estimated_profit)                                        AS estimated_profit,
               round(sum(s.estimated_profit) / nullif(sum(s.revenue), 0), 4)  AS estimated_margin,
               round(avg(s.revenue), 2)                                       AS avg_price
        FROM analytics.v_sales_items s
        JOIN core.locations l ON l.location_key = s.delivery_location_key
        JOIN core.products  p ON p.product_key  = s.product_key
        WHERE s.purchase_date BETWEEN %(start)s AND %(end)s AND {ITEM_FILTERS}
        GROUP BY s.product_key, p.product_uid, s.category
        HAVING count(*) >= %(min_units)s
        ORDER BY {order}, s.product_key
        LIMIT %(limit)s
    """, {"start": start, "end": end, "state": state, "category": category, "limit": limit,
          "min_units": min_units})


@cached
def low_rotation(category: int | None, min_units: int, limit: int) -> list[dict[str, Any]]:
    """Products with a real sales history (>= min_units) and no sale in the last 90 days."""
    return fetch_all("""
        SELECT p.product_key, p.product_short_id, p.category, p.units, p.revenue, p.estimated_margin,
               p.first_sale, p.last_sale, p.days_since_last_sale
        FROM analytics.v_product_performance p
        JOIN core.products pr USING (product_key)
        WHERE p.units >= %(min_units)s
          AND p.units_last_90d = 0
          AND (%(category)s::int IS NULL OR pr.category_key = %(category)s)
        ORDER BY p.revenue DESC
        LIMIT %(limit)s
    """, {"category": category, "min_units": min_units, "limit": limit})


@cached
def high_revenue_low_margin(min_units: int, limit: int) -> list[dict[str, Any]]:
    """Above-median revenue and bottom-quartile estimated margin, among products with >= min_units."""
    return fetch_all("""
        WITH eligible AS (
            SELECT * FROM analytics.v_product_performance WHERE units >= %(min_units)s
        ),
        thresholds AS (
            SELECT percentile_cont(0.5)  WITHIN GROUP (ORDER BY revenue)          AS median_revenue,
                   percentile_cont(0.25) WITHIN GROUP (ORDER BY estimated_margin) AS p25_margin
            FROM eligible
        )
        SELECT e.product_key, e.product_short_id, e.category, e.units, e.revenue, e.estimated_profit,
               e.estimated_margin, t.median_revenue, t.p25_margin
        FROM eligible e
        CROSS JOIN thresholds t
        WHERE e.revenue > t.median_revenue AND e.estimated_margin < t.p25_margin
        ORDER BY e.revenue DESC
        LIMIT %(limit)s
    """, {"min_units": min_units, "limit": limit})
