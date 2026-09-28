-- =============================================================================
-- NEXUS BI — Analytics layer (views and functions over the core schema)
-- -----------------------------------------------------------------------------
-- Business rules applied everywhere in this file
--   * A SALE is an order whose status is not 'canceled' or 'unavailable'.
--   * REVENUE is the sum of item prices of sales. Freight is reported separately
--     because it is passed through to carriers.
--   * ESTIMATED COST / PROFIT / MARGIN use the synthetic cost ratios described in
--     docs/architecture.md. They are estimates, not Olist data.
--   * The extract has incomplete edges, so "today" is not the last timestamp.
--     analytics.v_reporting_period derives the reliable window, the complete
--     months and a reference date from daily order volume (see below).
--   * Machine-learning views expose only the ACTIVE run of each model.
--
-- Re-runnable: every object is CREATE OR REPLACE. Views are dropped and recreated
-- in dependency order so column changes never fail.
-- =============================================================================

SET client_min_messages = warning;

CREATE SCHEMA IF NOT EXISTS analytics;

-- Materialized views first (they are refreshed by the pipeline after every load).
DROP MATERIALIZED VIEW IF EXISTS analytics.v_customer_summary, analytics.v_orders CASCADE;

DROP VIEW IF EXISTS
    analytics.v_model_evaluations,
    analytics.v_anomalies,
    analytics.v_sales_forecast,
    analytics.v_churn_scores,
    analytics.v_segment_summary,
    analytics.v_customer_segments,
    analytics.v_data_quality_latest,
    analytics.v_geo_city,
    analytics.v_geo_state,
    analytics.v_category_performance,
    analytics.v_product_performance,
    analytics.v_monthly_kpis,
    analytics.v_sales_items,
    analytics.v_month_coverage,
    analytics.v_reporting_period,
    analytics.v_daily_coverage
CASCADE;


-- -----------------------------------------------------------------------------
-- Period coverage
-- -----------------------------------------------------------------------------
-- The extract starts with sparse test orders (late 2016), has a gap around the
-- new year, and fades out at the end (late-August 2018 orders were only partly
-- captured: ~200 orders/day falling to 14 on Aug 29 and ~0 afterwards). Treating
-- those edges as real would invent growth at the start and a collapse at the end,
-- so the usable window is derived from the data in two steps:
--   1. Main data block: days with >= 10% of the median daily order count; a gap
--      of more than 7 such-less days starts a new block; the longest block wins
--      (gaps-and-islands).
--   2. End of reliable data: the last day of that block whose orders reach 50% of
--      the average of its previous 28 days (detects the fade-out).
-- A month is complete only when it lies entirely inside the reliable window.

CREATE VIEW analytics.v_daily_coverage AS
WITH bounds AS (
    SELECT min(purchase_date) AS first_day, max(purchase_date) AS last_day FROM core.orders
),
daily AS (
    SELECT c.date_key, count(o.order_key) AS orders
    FROM core.calendar c
    CROSS JOIN bounds b
    LEFT JOIN core.orders o ON o.purchase_date = c.date_key
    WHERE c.date_key BETWEEN b.first_day AND b.last_day
    GROUP BY c.date_key
),
threshold AS (
    SELECT 0.10 * percentile_cont(0.5) WITHIN GROUP (ORDER BY orders) AS min_orders
    FROM daily
    WHERE orders > 0
)
SELECT d.date_key,
       d.orders,
       d.orders >= t.min_orders                                                          AS above_floor,
       round(avg(d.orders) OVER (ORDER BY d.date_key ROWS BETWEEN 28 PRECEDING AND 1 PRECEDING), 1)
                                                                                         AS trailing_28d_avg
FROM daily d
CROSS JOIN threshold t;

-- Single-row view: the reliable data window, the complete months inside it and
-- the reference date used as "today" (the day after the last reliable day).
CREATE VIEW analytics.v_reporting_period AS
WITH covered AS (
    SELECT date_key,
           CASE WHEN date_key - lag(date_key) OVER (ORDER BY date_key) > 7 THEN 1 ELSE 0 END AS new_island
    FROM analytics.v_daily_coverage
    WHERE above_floor
),
islands AS (
    SELECT date_key, sum(new_island) OVER (ORDER BY date_key) AS island
    FROM covered
),
main_block AS (
    SELECT min(date_key) AS start_date, max(date_key) AS end_date
    FROM islands
    GROUP BY island
    ORDER BY count(*) DESC
    LIMIT 1
),
reliable_end AS (
    SELECT max(c.date_key) AS end_date
    FROM analytics.v_daily_coverage c
    CROSS JOIN main_block m
    WHERE c.date_key BETWEEN m.start_date AND m.end_date
      AND c.orders >= 0.5 * c.trailing_28d_avg
)
SELECT m.start_date                                                        AS data_start,
       e.end_date                                                          AS data_end,
       (date_trunc('month', m.start_date - 1) + interval '1 month')::date  AS first_month,
       (date_trunc('month', e.end_date + 1) - interval '1 month')::date    AS last_month,
       e.end_date + 1                                                      AS reference_date
FROM main_block m
CROSS JOIN reliable_end e;

COMMENT ON VIEW analytics.v_reporting_period IS
    'Reliable data window derived from daily order volume (see header of sql/views.sql).';

-- Orders per calendar month and whether the month is complete (entirely inside the window).
CREATE VIEW analytics.v_month_coverage AS
SELECT date_trunc('month', d.date_key)::date                    AS month,
       sum(d.orders)                                            AS orders,
       date_trunc('month', d.date_key)::date BETWEEN p.first_month AND p.last_month AS is_complete
FROM analytics.v_daily_coverage d
CROSS JOIN analytics.v_reporting_period p
GROUP BY 1, p.first_month, p.last_month;


-- -----------------------------------------------------------------------------
-- Facts
-- -----------------------------------------------------------------------------

-- One row per item sold (sales only), with product, category and estimated profit.
CREATE VIEW analytics.v_sales_items AS
SELECT i.order_key,
       i.line_number,
       o.customer_key,
       o.delivery_location_key,
       o.purchase_date,
       date_trunc('month', o.purchased_at)::date                 AS order_month,
       i.product_key,
       c.category_key,
       c.category_name_en                                        AS category,
       i.seller_key,
       i.unit_price                                              AS revenue,
       i.freight_value,
       round(i.unit_price * p.estimated_cost_ratio, 2)           AS estimated_cost,
       i.unit_price - round(i.unit_price * p.estimated_cost_ratio, 2) AS estimated_profit
FROM core.order_items i
JOIN core.orders     o USING (order_key)
JOIN core.products   p USING (product_key)
JOIN core.categories c USING (category_key)
WHERE o.order_status NOT IN ('canceled', 'unavailable');

-- One row per order (all statuses), with item totals, payment, delivery and review.
-- MATERIALIZED: it aggregates items, payments and reviews and numbers each customer's
-- purchases with a window function, which prevents predicate push-down. Refreshed by
-- the data pipeline after every load (REFRESH MATERIALIZED VIEW).
CREATE MATERIALIZED VIEW analytics.v_orders AS
WITH items AS (
    SELECT i.order_key,
           count(*)                                          AS items,
           count(DISTINCT i.seller_key)                      AS sellers,
           sum(i.unit_price)                                 AS revenue,
           sum(i.freight_value)                              AS freight,
           sum(round(i.unit_price * p.estimated_cost_ratio, 2)) AS estimated_cost
    FROM core.order_items i
    JOIN core.products p USING (product_key)
    GROUP BY i.order_key
),
payments AS (
    SELECT order_key,
           sum(amount)                                         AS amount_paid,
           max(installments)                                   AS installments,
           (array_agg(payment_type ORDER BY amount DESC))[1]   AS main_payment_type
    FROM core.order_payments
    GROUP BY order_key
),
reviews AS (
    SELECT order_key, round(avg(score), 2) AS review_score
    FROM core.order_reviews
    GROUP BY order_key
),
sequence AS (
    -- Position of each sale in the customer's purchase history (1 = first purchase).
    SELECT order_key,
           row_number() OVER (PARTITION BY customer_key ORDER BY purchased_at, order_key) AS customer_order_seq
    FROM core.orders
    WHERE order_status NOT IN ('canceled', 'unavailable')
)
SELECT o.order_key,
       o.order_uid,
       o.customer_key,
       o.delivery_location_key,
       l.state_code,
       l.city,
       o.order_status,
       o.order_status NOT IN ('canceled', 'unavailable')        AS is_valid_sale,
       o.purchased_at,
       o.purchase_date,
       date_trunc('month', o.purchased_at)::date                AS order_month,
       coalesce(it.items, 0)                                    AS items,
       coalesce(it.sellers, 0)                                  AS sellers,
       coalesce(it.revenue, 0)                                  AS revenue,
       coalesce(it.freight, 0)                                  AS freight,
       coalesce(it.estimated_cost, 0)                           AS estimated_cost,
       coalesce(it.revenue - it.estimated_cost, 0)              AS estimated_profit,
       pay.amount_paid,
       pay.installments,
       pay.main_payment_type,
       o.delivered_customer_at::date - o.purchase_date          AS delivery_days,
       o.estimated_delivery_date - o.purchase_date              AS promised_days,
       CASE WHEN o.delivered_customer_at IS NOT NULL
            THEN o.delivered_customer_at::date > o.estimated_delivery_date END AS is_late,
       r.review_score,
       s.customer_order_seq
FROM core.orders o
JOIN core.locations l ON l.location_key = o.delivery_location_key
LEFT JOIN items    it  USING (order_key)
LEFT JOIN payments pay USING (order_key)
LEFT JOIN reviews  r   USING (order_key)
LEFT JOIN sequence s   USING (order_key);

CREATE UNIQUE INDEX ux_v_orders_order       ON analytics.v_orders (order_key);
CREATE INDEX ix_v_orders_customer           ON analytics.v_orders (customer_key);
CREATE INDEX ix_v_orders_purchase_date      ON analytics.v_orders (purchase_date) WHERE is_valid_sale;
CREATE INDEX ix_v_orders_month              ON analytics.v_orders (order_month) WHERE is_valid_sale;


-- -----------------------------------------------------------------------------
-- Time
-- -----------------------------------------------------------------------------

-- Monthly KPIs on a gap-free month series, so LAG(1) is the previous month and
-- LAG(12) is the same month one year earlier.
CREATE VIEW analytics.v_monthly_kpis AS
WITH sales AS (
    SELECT order_month                                        AS month,
           sum(revenue)                                       AS revenue,
           sum(estimated_profit)                              AS estimated_profit,
           sum(freight)                                       AS freight,
           count(*)                                           AS orders,
           count(DISTINCT customer_key)                       AS customers,
           count(DISTINCT customer_key) FILTER (WHERE customer_order_seq = 1) AS new_customers
    FROM analytics.v_orders
    WHERE is_valid_sale
    GROUP BY order_month
),
monthly AS (
    SELECT c.month,
           c.is_complete,
           coalesce(s.revenue, 0)           AS revenue,
           coalesce(s.estimated_profit, 0)  AS estimated_profit,
           coalesce(s.freight, 0)           AS freight,
           coalesce(s.orders, 0)            AS orders,
           coalesce(s.customers, 0)         AS customers,
           coalesce(s.new_customers, 0)     AS new_customers
    FROM analytics.v_month_coverage c
    LEFT JOIN sales s USING (month)
)
SELECT month,
       is_complete,
       revenue,
       estimated_profit,
       round(estimated_profit / nullif(revenue, 0), 4)                       AS estimated_margin,
       freight,
       orders,
       customers,
       new_customers,
       customers - new_customers                                             AS returning_customers,
       round(revenue / nullif(orders, 0), 2)                                 AS avg_order_value,
       -- Growth is only meaningful when both months are complete; otherwise NULL.
       CASE WHEN is_complete AND lag(is_complete) OVER w
            THEN round(revenue / nullif(lag(revenue) OVER w, 0) - 1, 4) END      AS revenue_growth_mom,
       CASE WHEN is_complete AND lag(is_complete, 12) OVER w
            THEN round(revenue / nullif(lag(revenue, 12) OVER w, 0) - 1, 4) END  AS revenue_growth_yoy,
       sum(revenue) OVER (PARTITION BY date_part('year', month) ORDER BY month) AS revenue_ytd
FROM monthly
WINDOW w AS (ORDER BY month);


-- The comparison period for [p_start, p_end]. Whole calendar months (e.g. Jul 1-31)
-- compare with the same number of preceding calendar months (Jun 1-30); any other
-- range compares with the preceding N days. Shared by kpi_summary and the API.
CREATE OR REPLACE FUNCTION analytics.previous_period(p_start date, p_end date)
RETURNS TABLE (prev_start date, prev_end date)
LANGUAGE sql
IMMUTABLE
AS $$
    WITH shape AS (
        SELECT p_start = date_trunc('month', p_start)::date
               AND p_end + 1 = date_trunc('month', p_end + 1)::date            AS whole_months,
               (date_part('year', age(p_end + 1, p_start)) * 12
                + date_part('month', age(p_end + 1, p_start)))::int           AS n_months
    )
    SELECT CASE WHEN whole_months THEN (p_start - n_months * interval '1 month')::date
                ELSE p_start - (p_end - p_start + 1) END,
           p_start - 1
    FROM shape;
$$;

-- KPI cards: a period compared with its previous period (analytics.previous_period),
-- with optional state and category filters. Metrics are computed at item
-- level so the category filter is exact (an order can span several categories).
CREATE OR REPLACE FUNCTION analytics.kpi_summary(
    p_start        date,
    p_end          date,
    p_state        text     DEFAULT NULL,
    p_category_key smallint DEFAULT NULL
)
RETURNS TABLE (
    metric          text,
    current_value   numeric,
    previous_value  numeric,
    change_abs      numeric,
    change_pct      numeric
)
LANGUAGE sql
STABLE
AS $$
    WITH bounds AS (
        SELECT p_start AS cur_start, p_end AS cur_end, pp.prev_start
        FROM analytics.previous_period(p_start, p_end) pp
    ),
    base AS (
        SELECT CASE WHEN s.purchase_date >= b.cur_start THEN 'current' ELSE 'previous' END AS period,
               s.order_key, s.customer_key, s.revenue, s.estimated_profit
        FROM analytics.v_sales_items s
        CROSS JOIN bounds b
        JOIN core.locations l ON l.location_key = s.delivery_location_key
        WHERE s.purchase_date BETWEEN b.prev_start AND b.cur_end
          AND (p_state IS NULL OR l.state_code = p_state)
          AND (p_category_key IS NULL OR s.category_key = p_category_key)
    ),
    agg AS (
        SELECT p.period,
               coalesce(sum(b.revenue), 0)             AS revenue,
               coalesce(sum(b.estimated_profit), 0)    AS estimated_profit,
               count(DISTINCT b.order_key)             AS orders,
               count(DISTINCT b.customer_key)          AS customers
        FROM (VALUES ('current'), ('previous')) AS p(period)
        LEFT JOIN base b USING (period)
        GROUP BY p.period
    ),
    metrics AS (
        SELECT period, m.metric, m.ord, m.value
        FROM agg
        CROSS JOIN LATERAL (VALUES
            (1, 'revenue',          revenue),
            (2, 'estimated_profit', estimated_profit),
            (3, 'estimated_margin', estimated_profit / nullif(revenue, 0)),
            (4, 'orders',           orders::numeric),
            (5, 'customers',        customers::numeric),
            (6, 'avg_order_value',  revenue / nullif(orders, 0))
        ) AS m(ord, metric, value)
    )
    SELECT c.metric,
           round(c.value, 4),
           round(p.value, 4),
           round(c.value - p.value, 4),
           round((c.value / nullif(p.value, 0) - 1) * 100, 2)
    FROM metrics c
    JOIN metrics p ON p.metric = c.metric AND p.period = 'previous'
    WHERE c.period = 'current'
    ORDER BY c.ord;
$$;

COMMENT ON FUNCTION analytics.kpi_summary IS
    'KPI cards for [p_start, p_end] vs the preceding period: the same number of calendar months '
    'when the range is whole months, otherwise the same number of days. '
    'change_pct for estimated_margin is relative; use change_abs for percentage points.';


-- -----------------------------------------------------------------------------
-- Customers
-- -----------------------------------------------------------------------------

-- One row per customer with purchase history up to the reference date.
-- total_revenue is the historical Customer Lifetime Value (CLV) in revenue terms.
-- MATERIALIZED (refreshed with analytics.v_orders).
CREATE MATERIALIZED VIEW analytics.v_customer_summary AS
WITH ref AS (
    SELECT reference_date FROM analytics.v_reporting_period
),
history AS (
    SELECT o.customer_key,
           min(o.purchased_at)                              AS first_order_at,
           max(o.purchased_at)                              AS last_order_at,
           count(*)                                         AS orders,
           sum(o.revenue)                                   AS total_revenue,
           sum(o.estimated_profit)                          AS total_estimated_profit,
           sum(o.items)                                     AS items,
           round(avg(o.review_score), 2)                    AS avg_review_score,
           count(*) FILTER (WHERE o.is_late)                AS late_deliveries
    FROM analytics.v_orders o
    CROSS JOIN ref
    WHERE o.is_valid_sale
      AND o.purchase_date < ref.reference_date
    GROUP BY o.customer_key
)
SELECT c.customer_key,
       c.customer_uid,
       l.state_code,
       l.city,
       h.first_order_at,
       h.last_order_at,
       h.orders,
       h.items,
       h.total_revenue,
       h.total_estimated_profit,
       round(h.total_revenue / h.orders, 2)                           AS avg_order_value,
       ref.reference_date - h.last_order_at::date                     AS recency_days,
       ref.reference_date - h.first_order_at::date                    AS tenure_days,
       CASE WHEN h.orders > 1
            THEN round((h.last_order_at::date - h.first_order_at::date)::numeric / (h.orders - 1), 1)
       END                                                            AS avg_days_between_orders,
       h.avg_review_score,
       h.late_deliveries,
       h.orders > 1                                                   AS is_repeat_customer
FROM history h
JOIN core.customers c USING (customer_key)
JOIN core.locations l ON l.location_key = c.location_key
CROSS JOIN ref;

CREATE UNIQUE INDEX ux_v_customer_summary ON analytics.v_customer_summary (customer_key);


-- -----------------------------------------------------------------------------
-- Products and categories
-- -----------------------------------------------------------------------------

CREATE VIEW analytics.v_product_performance AS
WITH ref AS (
    SELECT reference_date FROM analytics.v_reporting_period
),
sales AS (
    SELECT s.product_key,
           count(*)                                   AS units,
           count(DISTINCT s.order_key)                AS orders,
           sum(s.revenue)                             AS revenue,
           sum(s.estimated_profit)                    AS estimated_profit,
           min(s.purchase_date)                       AS first_sale,
           max(s.purchase_date)                       AS last_sale,
           count(*) FILTER (WHERE s.purchase_date >= ref.reference_date - 90
                              AND s.purchase_date <  ref.reference_date) AS units_last_90d
    FROM analytics.v_sales_items s
    CROSS JOIN ref
    GROUP BY s.product_key
)
SELECT p.product_key,
       p.product_uid,
       left(p.product_uid, 8)                                         AS product_short_id,
       c.category_name_en                                             AS category,
       s.units,
       s.orders,
       s.revenue,
       s.estimated_profit,
       round(s.estimated_profit / nullif(s.revenue, 0), 4)            AS estimated_margin,
       round(s.revenue / s.units, 2)                                  AS avg_price,
       s.first_sale,
       s.last_sale,
       ref.reference_date - s.last_sale                               AS days_since_last_sale,
       s.units_last_90d,
       rank() OVER (ORDER BY s.revenue DESC)                          AS revenue_rank,
       rank() OVER (PARTITION BY c.category_key ORDER BY s.revenue DESC) AS revenue_rank_in_category
FROM sales s
JOIN core.products   p USING (product_key)
JOIN core.categories c USING (category_key)
CROSS JOIN ref;


-- Category totals plus growth of the last 3 complete months vs the 3 before.
CREATE VIEW analytics.v_category_performance AS
WITH ref AS (
    SELECT last_month FROM analytics.v_reporting_period
),
sales AS (
    SELECT s.category_key,
           s.category,
           count(*)                                 AS units,
           count(DISTINCT s.order_key)              AS orders,
           sum(s.revenue)                           AS revenue,
           sum(s.estimated_profit)                  AS estimated_profit,
           sum(s.revenue) FILTER (WHERE s.order_month >  ref.last_month - interval '3 months'
                                    AND s.order_month <= ref.last_month)                     AS revenue_last_3m,
           sum(s.revenue) FILTER (WHERE s.order_month >  ref.last_month - interval '6 months'
                                    AND s.order_month <= ref.last_month - interval '3 months') AS revenue_prev_3m
    FROM analytics.v_sales_items s
    CROSS JOIN ref
    GROUP BY s.category_key, s.category
)
SELECT category_key,
       category,
       units,
       orders,
       revenue,
       estimated_profit,
       round(estimated_profit / nullif(revenue, 0), 4)              AS estimated_margin,
       round(revenue / sum(revenue) OVER (), 4)                     AS revenue_share,
       coalesce(revenue_last_3m, 0)                                 AS revenue_last_3m,
       coalesce(revenue_prev_3m, 0)                                 AS revenue_prev_3m,
       round(revenue_last_3m / nullif(revenue_prev_3m, 0) - 1, 4)   AS growth_3m,
       rank() OVER (ORDER BY revenue DESC)                          AS revenue_rank
FROM sales;


-- -----------------------------------------------------------------------------
-- Geography
-- -----------------------------------------------------------------------------

-- Growth compares the last 6 complete months with the 6 months before them.
CREATE VIEW analytics.v_geo_state AS
WITH ref AS (
    SELECT last_month FROM analytics.v_reporting_period
),
sales AS (
    SELECT o.state_code,
           sum(o.revenue)                               AS revenue,
           sum(o.estimated_profit)                      AS estimated_profit,
           count(*)                                     AS orders,
           count(DISTINCT o.customer_key)               AS customers,
           round(avg(o.delivery_days), 1)               AS avg_delivery_days,
           round(avg(o.is_late::int), 4)                AS late_delivery_rate,
           round(avg(o.review_score), 2)                AS avg_review_score,
           round(avg(o.freight / nullif(o.revenue, 0)), 4) AS avg_freight_ratio,
           sum(o.revenue) FILTER (WHERE o.order_month >  ref.last_month - interval '6 months'
                                    AND o.order_month <= ref.last_month)                       AS revenue_last_6m,
           sum(o.revenue) FILTER (WHERE o.order_month >  ref.last_month - interval '12 months'
                                    AND o.order_month <= ref.last_month - interval '6 months') AS revenue_prev_6m
    FROM analytics.v_orders o
    CROSS JOIN ref
    WHERE o.is_valid_sale
    GROUP BY o.state_code
),
centroids AS (
    SELECT state_code, round(avg(latitude), 4) AS latitude, round(avg(longitude), 4) AS longitude
    FROM core.locations
    GROUP BY state_code
)
SELECT st.state_code,
       st.state_name,
       st.region,
       s.revenue,
       s.estimated_profit,
       round(s.estimated_profit / nullif(s.revenue, 0), 4)            AS estimated_margin,
       s.orders,
       s.customers,
       round(s.revenue / nullif(s.orders, 0), 2)                      AS avg_order_value,
       s.avg_delivery_days,
       s.late_delivery_rate,
       s.avg_review_score,
       s.avg_freight_ratio,
       coalesce(s.revenue_last_6m, 0)                                 AS revenue_last_6m,
       coalesce(s.revenue_prev_6m, 0)                                 AS revenue_prev_6m,
       round(s.revenue_last_6m / nullif(s.revenue_prev_6m, 0) - 1, 4) AS growth_6m,
       c.latitude,
       c.longitude
FROM core.states st
JOIN sales s USING (state_code)
LEFT JOIN centroids c USING (state_code);


CREATE VIEW analytics.v_geo_city AS
WITH ref AS (
    SELECT last_month FROM analytics.v_reporting_period
),
sales AS (
    SELECT o.state_code,
           o.city,
           sum(o.revenue)                   AS revenue,
           sum(o.estimated_profit)          AS estimated_profit,
           count(*)                         AS orders,
           count(DISTINCT o.customer_key)   AS customers,
           sum(o.revenue) FILTER (WHERE o.order_month >  ref.last_month - interval '6 months'
                                    AND o.order_month <= ref.last_month)                       AS revenue_last_6m,
           sum(o.revenue) FILTER (WHERE o.order_month >  ref.last_month - interval '12 months'
                                    AND o.order_month <= ref.last_month - interval '6 months') AS revenue_prev_6m
    FROM analytics.v_orders o
    CROSS JOIN ref
    WHERE o.is_valid_sale
    GROUP BY o.state_code, o.city
),
centroids AS (
    -- A city spans several zip prefixes: use the median of their coordinates.
    SELECT state_code, city,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY latitude)  AS latitude,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY longitude) AS longitude
    FROM core.locations
    WHERE latitude IS NOT NULL
    GROUP BY state_code, city
)
SELECT s.state_code,
       s.city,
       s.revenue,
       s.estimated_profit,
       round(s.estimated_profit / nullif(s.revenue, 0), 4)            AS estimated_margin,
       s.orders,
       s.customers,
       coalesce(s.revenue_last_6m, 0)                                 AS revenue_last_6m,
       coalesce(s.revenue_prev_6m, 0)                                 AS revenue_prev_6m,
       round(s.revenue_last_6m / nullif(s.revenue_prev_6m, 0) - 1, 4) AS growth_6m,
       round(c.latitude::numeric, 5)                                  AS latitude,
       round(c.longitude::numeric, 5)                                 AS longitude
FROM sales s
LEFT JOIN centroids c USING (state_code, city);


-- -----------------------------------------------------------------------------
-- Data quality
-- -----------------------------------------------------------------------------

-- Issues of the latest successful pipeline run.
CREATE VIEW analytics.v_data_quality_latest AS
SELECT r.run_id,
       r.finished_at,
       r.rows_read,
       r.rows_loaded,
       i.source_table,
       i.check_name,
       i.category,
       i.severity,
       i.rows_affected,
       i.action_taken,
       i.details
FROM ops.pipeline_runs r
JOIN ops.data_quality_issues i USING (run_id)
WHERE r.run_id = (SELECT max(run_id) FROM ops.pipeline_runs WHERE status = 'success');


-- -----------------------------------------------------------------------------
-- Machine learning (active run of each model)
-- -----------------------------------------------------------------------------

CREATE VIEW analytics.v_customer_segments AS
SELECT s.customer_key,
       c.customer_uid,
       l.state_code,
       l.city,
       s.segment,
       s.recency_days,
       s.frequency,
       s.monetary,
       s.r_score,
       s.f_score,
       s.m_score,
       s.r_score::text || s.f_score::text || s.m_score::text AS rfm_code,
       s.run_id
FROM ml.customer_segments s
JOIN ml.model_runs r      ON r.run_id = s.run_id AND r.is_active
JOIN core.customers c     USING (customer_key)
JOIN core.locations l     ON l.location_key = c.location_key;

-- One row per segment: size, value, behaviour, and the rule and recommendation behind it.
CREATE VIEW analytics.v_segment_summary AS
SELECT d.segment,
       d.display_order,
       count(s.customer_key)                                               AS customers,
       round(count(s.customer_key)::numeric / sum(count(s.customer_key)) OVER (), 4) AS customer_share,
       coalesce(sum(s.monetary), 0)                                        AS revenue,
       round(coalesce(sum(s.monetary), 0) / sum(sum(s.monetary)) OVER (), 4) AS revenue_share,
       round(avg(s.monetary), 2)                                           AS avg_revenue_per_customer,
       round(avg(s.frequency), 2)                                          AS avg_orders,
       round(avg(s.recency_days))                                          AS avg_recency_days,
       d.rule,
       d.description,
       d.recommendation
FROM ml.segment_definitions d
JOIN ml.model_runs r ON r.run_id = d.run_id AND r.is_active
LEFT JOIN ml.customer_segments s ON s.run_id = d.run_id AND s.segment = d.segment
GROUP BY d.segment, d.display_order, d.rule, d.description, d.recommendation;

CREATE VIEW analytics.v_churn_scores AS
SELECT cs.customer_key,
       c.customer_uid,
       l.state_code,
       l.city,
       cs.churn_probability,
       cs.risk_band,
       cs.top_factors,
       seg.segment,
       seg.recency_days,
       seg.frequency,
       seg.monetary
FROM ml.churn_scores cs
JOIN ml.model_runs r   ON r.run_id = cs.run_id AND r.is_active
JOIN core.customers c  USING (customer_key)
JOIN core.locations l  ON l.location_key = c.location_key
LEFT JOIN analytics.v_customer_segments seg USING (customer_key);

-- Actual daily revenue of the reliable window followed by the forecast.
CREATE VIEW analytics.v_sales_forecast AS
SELECT c.date_key                        AS day,
       'actual'::text                    AS kind,
       coalesce(sum(s.revenue), 0)       AS revenue,
       NULL::numeric                     AS lower_80,
       NULL::numeric                     AS upper_80
FROM core.calendar c
CROSS JOIN analytics.v_reporting_period p
LEFT JOIN analytics.v_sales_items s ON s.purchase_date = c.date_key
WHERE c.date_key >= p.first_month AND c.date_key < p.reference_date
GROUP BY c.date_key
UNION ALL
SELECT f.forecast_date, 'forecast', f.forecast, f.lower_80, f.upper_80
FROM ml.sales_forecast f
JOIN ml.model_runs r ON r.run_id = f.run_id AND r.is_active;

CREATE VIEW analytics.v_anomalies AS
SELECT a.anomaly_id,
       a.entity_type,
       a.entity_key,
       CASE a.entity_type
           WHEN 'product'  THEN (SELECT left(p.product_uid, 8) FROM core.products p WHERE p.product_key = a.entity_key)
           WHEN 'customer' THEN (SELECT left(c.customer_uid, 8) FROM core.customers c WHERE c.customer_key = a.entity_key)
       END                                                   AS entity_label,
       a.period_start,
       a.period_end,
       a.metric,
       a.observed,
       a.expected,
       a.score,
       a.direction,
       a.method,
       a.description
FROM ml.anomalies a
JOIN ml.model_runs r ON r.run_id = a.run_id AND r.is_active;

CREATE VIEW analytics.v_model_evaluations AS
SELECT r.model_name,
       r.algorithm AS selected_algorithm,
       r.trained_at,
       e.candidate,
       e.candidate = r.algorithm AS is_selected,
       e.split,
       e.metric,
       e.value
FROM ml.model_evaluations e
JOIN ml.model_runs r ON r.run_id = e.run_id AND r.is_active;
