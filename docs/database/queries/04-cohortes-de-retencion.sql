-- titulo: Retención por cohortes
-- descripcion: La cohorte es el mes de la primera compra; cada columna es el % de esa cohorte que volvió a comprar N meses después. Usa CTE, age() y la cláusula FILTER para pivotar sin CASE.
WITH primera_compra AS (
    SELECT customer_key, min(order_month) AS cohorte
    FROM analytics.v_orders
    WHERE is_valid_sale
    GROUP BY customer_key
),
actividad AS (
    SELECT DISTINCT p.cohorte, o.customer_key,
           (date_part('year', age(o.order_month, p.cohorte)) * 12
            + date_part('month', age(o.order_month, p.cohorte)))::int AS meses_despues
    FROM analytics.v_orders o
    JOIN primera_compra p USING (customer_key)
    WHERE o.is_valid_sale
),
tamano AS (
    SELECT cohorte, count(*) AS clientes FROM primera_compra GROUP BY cohorte
)
SELECT to_char(a.cohorte, 'YYYY-MM')                                                    AS cohorte,
       t.clientes,
       round(100.0 * count(*) FILTER (WHERE meses_despues = 1) / t.clientes, 2)         AS mes_1_pct,
       round(100.0 * count(*) FILTER (WHERE meses_despues = 2) / t.clientes, 2)         AS mes_2_pct,
       round(100.0 * count(*) FILTER (WHERE meses_despues = 3) / t.clientes, 2)         AS mes_3_pct,
       round(100.0 * count(*) FILTER (WHERE meses_despues = 6) / t.clientes, 2)         AS mes_6_pct,
       round(100.0 * count(DISTINCT customer_key) FILTER (WHERE meses_despues BETWEEN 1 AND 12)
             / t.clientes, 2)                                                           AS repite_en_12m_pct
FROM actividad a
JOIN tamano t USING (cohorte)
WHERE a.cohorte BETWEEN '2017-01-01' AND '2017-12-01'
GROUP BY a.cohorte, t.clientes
ORDER BY a.cohorte;
