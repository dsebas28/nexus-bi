-- titulo: Las 3 categorías líderes de cada trimestre
-- descripcion: Dos CTE encadenadas y DENSE_RANK() por partición: el ranking se reinicia en cada trimestre y la participación se calcula sobre el total de ese trimestre.
WITH trimestral AS (
    SELECT date_trunc('quarter', purchase_date)::date AS trimestre,
           category                                   AS categoria,
           sum(revenue)                               AS ingresos
    FROM analytics.v_sales_items
    WHERE order_month BETWEEN (SELECT first_month FROM analytics.v_reporting_period)
                          AND (SELECT last_month  FROM analytics.v_reporting_period)
    GROUP BY 1, 2
),
ranking AS (
    SELECT trimestre, categoria, ingresos,
           dense_rank() OVER (PARTITION BY trimestre ORDER BY ingresos DESC)        AS puesto,
           round(100 * ingresos / sum(ingresos) OVER (PARTITION BY trimestre), 2)   AS participacion_pct
    FROM trimestral
)
SELECT to_char(trimestre, 'YYYY-"T"Q') AS trimestre, puesto, categoria, ingresos, participacion_pct
FROM ranking
WHERE puesto <= 3
ORDER BY ranking.trimestre, puesto;
