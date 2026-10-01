-- titulo: Análisis de Pareto (80/20)
-- descripcion: Participación acumulada con SUM() OVER ordenado: qué porcentaje del catálogo genera el 80 % de los ingresos.
WITH ordenados AS (
    SELECT revenue,
           sum(revenue) OVER (ORDER BY revenue DESC, product_key) / sum(revenue) OVER () AS participacion_acumulada,
           row_number() OVER (ORDER BY revenue DESC, product_key)                       AS puesto,
           count(*) OVER ()                                                             AS total_productos
    FROM analytics.v_product_performance
)
SELECT min(puesto)                                                AS productos_para_el_80pct,
       max(total_productos)                                       AS productos_totales,
       round(100.0 * min(puesto) / max(total_productos), 2)       AS porcentaje_del_catalogo
FROM ordenados
WHERE participacion_acumulada >= 0.80;
