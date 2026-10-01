-- titulo: Dependencia de los mejores vendedores
-- descripcion: NTILE(10) reparte a los vendedores en deciles por ingresos; una ventana sobre el agregado (sum(sum()) OVER) calcula la participación de cada decil.
WITH por_vendedor AS (
    SELECT seller_key, sum(revenue) AS ingresos
    FROM analytics.v_sales_items
    GROUP BY seller_key
),
deciles AS (
    SELECT seller_key, ingresos, ntile(10) OVER (ORDER BY ingresos DESC) AS decil
    FROM por_vendedor
)
SELECT decil,
       count(*)                                                    AS vendedores,
       sum(ingresos)                                               AS ingresos,
       round(100 * sum(ingresos) / sum(sum(ingresos)) OVER (), 2)  AS participacion_pct
FROM deciles
GROUP BY decil
ORDER BY decil;
