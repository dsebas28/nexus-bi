"""Customer analytics: new vs returning, frequency, spend, CLV, cohorts and profiles."""
from __future__ import annotations

from datetime import date
from typing import Any

from ..models.database import fetch_all, fetch_one
from .cache import cached


@cached
def overview(start: date, end: date, state: str | None) -> dict[str, Any]:
    """Period activity plus lifetime metrics of the customers active in the period."""
    return fetch_one("""
        WITH period_orders AS (
            SELECT o.customer_key, o.revenue, o.customer_order_seq
            FROM analytics.v_orders o
            WHERE o.is_valid_sale
              AND o.purchase_date BETWEEN %(start)s AND %(end)s
              AND (%(state)s::text IS NULL OR o.state_code = %(state)s)
        ),
        active AS (
            SELECT customer_key,
                   bool_or(customer_order_seq = 1) AS first_purchase_in_period,
                   count(*)                        AS orders,
                   sum(revenue)                    AS revenue
            FROM period_orders
            GROUP BY customer_key
        )
        SELECT count(*)                                                    AS active_customers,
               count(*) FILTER (WHERE a.first_purchase_in_period)          AS new_customers,
               count(*) FILTER (WHERE NOT a.first_purchase_in_period)      AS returning_customers,
               round(avg(a.orders), 3)                                     AS avg_orders_per_customer,
               round(avg(a.revenue), 2)                                    AS avg_spend_in_period,
               round(avg(cs.total_revenue), 2)                             AS avg_lifetime_revenue,
               round(avg(cs.total_estimated_profit), 2)                    AS avg_lifetime_estimated_profit,
               round(avg(cs.is_repeat_customer::int), 4)                   AS repeat_customer_rate
        FROM active a
        JOIN analytics.v_customer_summary cs USING (customer_key)
    """, {"start": start, "end": end, "state": state})


@cached
def base_metrics() -> dict[str, Any]:
    """Whole customer base up to the reference date."""
    return fetch_one("""
        SELECT count(*)                                          AS customers,
               count(*) FILTER (WHERE is_repeat_customer)         AS repeat_customers,
               round(avg(is_repeat_customer::int), 4)             AS repeat_rate,
               round(avg(orders), 3)                              AS avg_orders,
               round(avg(total_revenue), 2)                       AS avg_clv_revenue,
               round(avg(total_estimated_profit), 2)              AS avg_clv_estimated_profit,
               round(avg(total_revenue) FILTER (WHERE is_repeat_customer), 2)     AS avg_clv_repeat,
               round(avg(total_revenue) FILTER (WHERE NOT is_repeat_customer), 2) AS avg_clv_one_time,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY avg_days_between_orders)
                   FILTER (WHERE avg_days_between_orders > 0)     AS median_days_between_orders
        FROM analytics.v_customer_summary
    """)


@cached
def monthly_new_vs_returning(state: str | None) -> list[dict[str, Any]]:
    return fetch_all("""
        SELECT o.order_month AS month,
               count(DISTINCT o.customer_key) FILTER (WHERE o.customer_order_seq = 1)  AS new_customers,
               count(DISTINCT o.customer_key) FILTER (WHERE o.customer_order_seq > 1)  AS returning_customers,
               round(sum(o.revenue) FILTER (WHERE o.customer_order_seq > 1)
                     / nullif(sum(o.revenue), 0), 4)                                   AS returning_revenue_share
        FROM analytics.v_orders o
        JOIN analytics.v_month_coverage m ON m.month = o.order_month AND m.is_complete
        WHERE o.is_valid_sale AND (%(state)s::text IS NULL OR o.state_code = %(state)s)
        GROUP BY o.order_month
        ORDER BY o.order_month
    """, {"state": state})


@cached
def frequency_distribution() -> list[dict[str, Any]]:
    return fetch_all("""
        SELECT least(orders, 5)                       AS orders_bucket,
               count(*)                               AS customers,
               round(sum(total_revenue), 2)           AS revenue
        FROM analytics.v_customer_summary
        GROUP BY 1
        ORDER BY 1
    """)


@cached
def cohorts(max_months: int) -> list[dict[str, Any]]:
    """Share of each monthly cohort that buys again N months after the first purchase."""
    return fetch_all("""
        WITH first_purchase AS (
            SELECT customer_key, min(order_month) AS cohort_month
            FROM analytics.v_orders WHERE is_valid_sale GROUP BY customer_key
        ),
        activity AS (
            SELECT DISTINCT f.cohort_month, o.customer_key,
                   (date_part('year', age(o.order_month, f.cohort_month)) * 12
                    + date_part('month', age(o.order_month, f.cohort_month)))::int AS month_offset
            FROM analytics.v_orders o JOIN first_purchase f USING (customer_key)
            WHERE o.is_valid_sale
        ),
        sizes AS (
            SELECT cohort_month, count(*) AS cohort_customers FROM first_purchase GROUP BY cohort_month
        )
        SELECT a.cohort_month, s.cohort_customers, a.month_offset,
               count(*)                                              AS active_customers,
               round(count(*)::numeric / s.cohort_customers, 5)      AS retention
        FROM activity a
        JOIN sizes s USING (cohort_month)
        JOIN analytics.v_month_coverage m ON m.month = a.cohort_month AND m.is_complete
        WHERE a.month_offset BETWEEN 1 AND %(max_months)s
          AND (a.cohort_month + a.month_offset * interval '1 month')
              <= (SELECT last_month FROM analytics.v_reporting_period)
        GROUP BY a.cohort_month, s.cohort_customers, a.month_offset
        ORDER BY a.cohort_month, a.month_offset
    """, {"max_months": max_months})


@cached
def profile(customer_key: int) -> dict[str, Any] | None:
    return fetch_one("""
        SELECT cs.*, seg.segment, seg.rfm_code, ch.churn_probability, ch.risk_band, ch.top_factors
        FROM analytics.v_customer_summary cs
        LEFT JOIN analytics.v_customer_segments seg USING (customer_key)
        LEFT JOIN analytics.v_churn_scores ch USING (customer_key)
        WHERE cs.customer_key = %(key)s
    """, {"key": customer_key})
