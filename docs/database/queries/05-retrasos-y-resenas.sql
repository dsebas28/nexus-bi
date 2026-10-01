-- titulo: ¿Los retrasos en la entrega afectan las reseñas?
-- descripcion: Compara los pedidos entregados a tiempo con los entregados tarde: nota media, porcentaje de reseñas de 1 estrella y días de entrega.
SELECT CASE WHEN is_late THEN 'tarde' ELSE 'a tiempo' END        AS entrega,
       count(*)                                                  AS pedidos,
       round(avg(review_score), 2)                               AS nota_media,
       round(100.0 * avg((review_score <= 1)::int), 2)           AS una_estrella_pct,
       round(avg(delivery_days), 1)                              AS dias_de_entrega
FROM analytics.v_orders
WHERE is_valid_sale AND is_late IS NOT NULL AND review_score IS NOT NULL
GROUP BY 1
ORDER BY 1;
