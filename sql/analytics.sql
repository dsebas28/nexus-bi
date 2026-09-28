-- =============================================================================
-- NEXUS BI — Analytical queries
-- -----------------------------------------------------------------------------
-- Each query answers one business question. They run on top of sql/views.sql
-- and demonstrate CTEs, window functions (LAG, NTILE, RANK, running and moving
-- frames), FILTER, percentiles, width_bucket and LATERAL joins.
--
-- Run all:  psql -d nexus_bi -f sql/analytics.sql
-- =============================================================================


-- Q1. How is revenue trending once seasonality noise is smoothed out?
--     3-month moving average and running total (window frames).
SELECT month,
       revenue,
       round(avg(revenue) OVER (ORDER BY month ROWS BETWEEN 2 PRECEDING AND CURRENT ROW), 2) AS revenue_3m_avg,
       sum(revenue) OVER (ORDER BY month)                                                    AS revenue_cumulative,
       revenue_ytd
FROM analytics.v_monthly_kpis
WHERE is_complete
ORDER BY month;


-- Q2. Which categories lead each quarter, and do the leaders change?
--     Top 3 categories per quarter by revenue (DENSE_RANK per partition).
WITH quarterly AS (
    SELECT date_trunc('quarter', purchase_date)::date AS quarter,
           category,
           sum(revenue)                               AS revenue
    FROM analytics.v_sales_items
    WHERE order_month BETWEEN (SELECT first_month FROM analytics.v_reporting_period)
                          AND (SELECT last_month  FROM analytics.v_reporting_period)
    GROUP BY 1, 2
),
ranked AS (
    SELECT quarter, category, revenue,
           dense_rank() OVER (PARTITION BY quarter ORDER BY revenue DESC) AS rank_in_quarter,
           round(revenue / sum(revenue) OVER (PARTITION BY quarter), 4)   AS share_of_quarter
    FROM quarterly
)
SELECT *
FROM ranked
WHERE rank_in_quarter <= 3
ORDER BY quarter, rank_in_quarter;


-- Q3. How concentrated is revenue? (Pareto / 80-20 analysis)
--     Share of products needed to reach 80% of revenue.
WITH ranked AS (
    SELECT product_key,
           revenue,
           sum(revenue) OVER (ORDER BY revenue DESC, product_key) / sum(revenue) OVER () AS cumulative_share,
           row_number() OVER (ORDER BY revenue DESC, product_key)                       AS product_rank,
           count(*) OVER ()                                                             AS total_products
    FROM analytics.v_product_performance
)
SELECT min(product_rank)                                              AS products_for_80pct_revenue,
       max(total_products)                                            AS total_products,
       round(min(product_rank)::numeric / max(total_products), 4)     AS share_of_catalogue
FROM ranked
WHERE cumulative_share >= 0.80;


-- Q4. Do customers come back? Monthly cohort retention.
--     Cohort = month of first purchase; cell = % of the cohort buying again N months later.
WITH first_purchase AS (
    SELECT customer_key, min(order_month) AS cohort_month
    FROM analytics.v_orders
    WHERE is_valid_sale
    GROUP BY customer_key
),
activity AS (
    SELECT DISTINCT f.cohort_month,
           o.customer_key,
           (date_part('year', age(o.order_month, f.cohort_month)) * 12
            + date_part('month', age(o.order_month, f.cohort_month)))::int AS months_since_first
    FROM analytics.v_orders o
    JOIN first_purchase f USING (customer_key)
    WHERE o.is_valid_sale
),
cohort_size AS (
    SELECT cohort_month, count(*) AS customers
    FROM first_purchase
    GROUP BY cohort_month
)
SELECT a.cohort_month,
       s.customers                                                                    AS cohort_customers,
       round(100.0 * count(*) FILTER (WHERE months_since_first = 1) / s.customers, 2) AS m1_pct,
       round(100.0 * count(*) FILTER (WHERE months_since_first = 2) / s.customers, 2) AS m2_pct,
       round(100.0 * count(*) FILTER (WHERE months_since_first = 3) / s.customers, 2) AS m3_pct,
       round(100.0 * count(*) FILTER (WHERE months_since_first = 6) / s.customers, 2) AS m6_pct,
       round(100.0 * count(DISTINCT customer_key) FILTER (WHERE months_since_first BETWEEN 1 AND 12)
             / s.customers, 2)                                                        AS any_repeat_12m_pct
FROM activity a
JOIN cohort_size s USING (cohort_month)
WHERE a.cohort_month BETWEEN '2017-01-01' AND '2017-12-01'
GROUP BY a.cohort_month, s.customers
ORDER BY a.cohort_month;


-- Q5. When customers do return, how long do they take?
--     Days between consecutive purchases (LAG) summarised with percentiles.
WITH purchases AS (
    SELECT customer_key,
           purchased_at,
           purchased_at::date - lag(purchased_at::date) OVER (PARTITION BY customer_key ORDER BY purchased_at)
               AS days_since_previous
    FROM analytics.v_orders
    WHERE is_valid_sale
)
SELECT count(*)                                                                 AS repeat_purchases,
       count(*) FILTER (WHERE days_since_previous = 0)                          AS same_day_repeats,
       percentile_cont(0.25) WITHIN GROUP (ORDER BY days_since_previous)        AS p25_days,
       percentile_cont(0.50) WITHIN GROUP (ORDER BY days_since_previous)        AS median_days,
       percentile_cont(0.75) WITHIN GROUP (ORDER BY days_since_previous)        AS p75_days,
       percentile_cont(0.90) WITHIN GROUP (ORDER BY days_since_previous)        AS p90_days
FROM purchases
WHERE days_since_previous IS NOT NULL;


-- Q6. Does late delivery hurt customer satisfaction?
--     Review score and 1-star rate for on-time vs late deliveries.
SELECT CASE WHEN is_late THEN 'late' ELSE 'on time' END                AS delivery,
       count(*)                                                        AS orders,
       round(avg(review_score), 2)                                     AS avg_review,
       round(100.0 * avg((review_score <= 1)::int), 2)                 AS one_star_pct,
       round(avg(delivery_days), 1)                                    AS avg_delivery_days
FROM analytics.v_orders
WHERE is_valid_sale AND is_late IS NOT NULL AND review_score IS NOT NULL
GROUP BY 1
ORDER BY 1;


-- Q7. Where are logistics weakest?
--     States ranked by late-delivery rate (only states with >= 500 orders).
SELECT state_code,
       state_name,
       orders,
       avg_delivery_days,
       round(100 * late_delivery_rate, 2)                              AS late_pct,
       avg_review_score,
       rank() OVER (ORDER BY late_delivery_rate DESC)                  AS lateness_rank
FROM analytics.v_geo_state
WHERE orders >= 500
ORDER BY lateness_rank;


-- Q8. Which states are growing fastest (last 6 months vs previous 6)?
SELECT state_code,
       region,
       revenue_prev_6m,
       revenue_last_6m,
       round(100 * growth_6m, 2)                                       AS growth_pct,
       round(100 * revenue_last_6m / sum(revenue_last_6m) OVER (), 2)  AS share_last_6m_pct
FROM analytics.v_geo_state
WHERE revenue_prev_6m >= 10000
ORDER BY growth_6m DESC;


-- Q9. Which products sell a lot but earn little? (high revenue, low margin)
--     Above-median revenue and bottom-quartile estimated margin.
WITH thresholds AS (
    SELECT percentile_cont(0.5)  WITHIN GROUP (ORDER BY revenue)          AS median_revenue,
           percentile_cont(0.25) WITHIN GROUP (ORDER BY estimated_margin) AS p25_margin
    FROM analytics.v_product_performance
    WHERE units >= 10
)
SELECT p.product_short_id, p.category, p.units, p.revenue,
       round(100 * p.estimated_margin, 2) AS estimated_margin_pct
FROM analytics.v_product_performance p
CROSS JOIN thresholds t
WHERE p.units >= 10
  AND p.revenue > t.median_revenue
  AND p.estimated_margin < t.p25_margin
ORDER BY p.revenue DESC
LIMIT 15;


-- Q10. Which products stopped selling? (low rotation)
--      Products with a solid sales history but no sales in the last 90 days.
SELECT product_short_id,
       category,
       units,
       revenue,
       last_sale,
       days_since_last_sale
FROM analytics.v_product_performance
WHERE units >= 10
  AND units_last_90d = 0
ORDER BY revenue DESC
LIMIT 15;


-- Q11. How dependent is the marketplace on its top sellers?
--      Revenue share of each seller decile (NTILE).
WITH seller_revenue AS (
    SELECT seller_key, sum(revenue) AS revenue
    FROM analytics.v_sales_items
    GROUP BY seller_key
),
deciles AS (
    SELECT seller_key, revenue, ntile(10) OVER (ORDER BY revenue DESC) AS decile
    FROM seller_revenue
)
SELECT decile,
       count(*)                                                  AS sellers,
       sum(revenue)                                              AS revenue,
       round(100 * sum(revenue) / sum(sum(revenue)) OVER (), 2)  AS revenue_share_pct
FROM deciles
GROUP BY decile
ORDER BY decile;


-- Q12. When do customers buy? Orders by weekday and hour (heatmap source).
SELECT c.day_of_week,
       c.day_name,
       extract(hour FROM o.purchased_at)::int AS hour,
       count(*)                               AS orders
FROM analytics.v_orders o
JOIN core.calendar c ON c.date_key = o.purchase_date
WHERE o.is_valid_sale
GROUP BY 1, 2, 3
ORDER BY 1, 3;


-- Q13. How do customers pay across ticket sizes?
--      Payment mix and average installments by order-value bucket (width_bucket).
WITH buckets AS (
    SELECT width_bucket(revenue, ARRAY[50, 100, 200, 500, 1000]::numeric[]) AS bucket,
           main_payment_type,
           installments
    FROM analytics.v_orders
    WHERE is_valid_sale AND revenue > 0 AND main_payment_type IS NOT NULL
)
SELECT (ARRAY['< 50', '50-100', '100-200', '200-500', '500-1000', '>= 1000'])[bucket + 1] AS order_value_brl,
       count(*)                                                                    AS orders,
       round(100.0 * avg((main_payment_type = 'credit_card')::int), 1)             AS credit_card_pct,
       round(100.0 * avg((main_payment_type = 'boleto')::int), 1)                  AS boleto_pct,
       round(avg(installments) FILTER (WHERE main_payment_type = 'credit_card'), 2) AS avg_card_installments
FROM buckets
GROUP BY bucket
ORDER BY bucket;


-- Q14. Which days broke the pattern? (preview of the anomaly-detection module)
--      Daily revenue z-score against the previous 28 days (rolling window).
WITH daily AS (
    SELECT c.date_key,
           coalesce(sum(s.revenue), 0) AS revenue
    FROM core.calendar c
    LEFT JOIN analytics.v_sales_items s ON s.purchase_date = c.date_key
    WHERE c.date_key BETWEEN (SELECT first_month FROM analytics.v_reporting_period)
                         AND (SELECT reference_date - 1 FROM analytics.v_reporting_period)
    GROUP BY c.date_key
),
scored AS (
    SELECT date_key,
           revenue,
           avg(revenue)         OVER w AS baseline_mean,
           stddev_samp(revenue) OVER w AS baseline_std
    FROM daily
    WINDOW w AS (ORDER BY date_key ROWS BETWEEN 28 PRECEDING AND 1 PRECEDING)
)
SELECT date_key,
       revenue,
       round(baseline_mean, 2)                                   AS baseline_mean,
       round((revenue - baseline_mean) / nullif(baseline_std, 0), 2) AS z_score
FROM scored
WHERE date_key >= (SELECT first_month FROM analytics.v_reporting_period) + 28
  AND abs((revenue - baseline_mean) / nullif(baseline_std, 0)) >= 3
ORDER BY abs((revenue - baseline_mean) / nullif(baseline_std, 0)) DESC
LIMIT 10;


-- Q15. RFM preview: score every customer 1-5 on each dimension (NTILE).
--      Frequency is 1 for ~97% of customers, so NTILE would split identical values
--      arbitrarily; it is scored with explicit thresholds instead. The final
--      segmentation lives in machine_learning/segmentation.
WITH scored AS (
    SELECT customer_key,
           recency_days,
           orders,
           total_revenue,
           6 - ntile(5) OVER (ORDER BY recency_days)    AS r_score,   -- recent = 5
           CASE WHEN orders >= 4 THEN 5 WHEN orders = 3 THEN 4 WHEN orders = 2 THEN 3 ELSE 1 END AS f_score,
           ntile(5) OVER (ORDER BY total_revenue)        AS m_score    -- high spend = 5
    FROM analytics.v_customer_summary
)
SELECT r_score, f_score,
       count(*)                          AS customers,
       round(avg(total_revenue), 2)      AS avg_revenue,
       round(avg(recency_days))          AS avg_recency_days
FROM scored
GROUP BY r_score, f_score
ORDER BY r_score DESC, f_score DESC;


-- Q16. KPI cards for the last complete month vs the month before (function call).
SELECT *
FROM analytics.kpi_summary(
    (SELECT last_month FROM analytics.v_reporting_period),
    (SELECT (last_month + interval '1 month - 1 day')::date FROM analytics.v_reporting_period)
);
