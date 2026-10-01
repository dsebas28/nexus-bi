-- titulo: Estados con peor logística
-- descripcion: Ranking con RANK() de los estados por porcentaje de entregas tardías (solo estados con al menos 500 pedidos), junto a los días de entrega y la nota media.
SELECT rank() OVER (ORDER BY late_delivery_rate DESC)  AS puesto,
       state_code                                      AS estado,
       state_name                                      AS nombre,
       orders                                          AS pedidos,
       avg_delivery_days                               AS dias_de_entrega,
       round(100 * late_delivery_rate, 2)              AS tardias_pct,
       avg_review_score                                AS nota_media
FROM analytics.v_geo_state
WHERE orders >= 500
ORDER BY puesto
LIMIT 12;
