-- titulo: Segmentos de clientes (machine learning)
-- descripcion: Los modelos escriben sus resultados en el esquema ml. La vista analytics.v_segment_summary lee solo la ejecución activa del modelo RFM y resume cada segmento.
SELECT segment                                AS segmento,
       customers                              AS clientes,
       round(100 * customer_share, 1)         AS clientes_pct,
       revenue                                AS ingresos,
       round(100 * revenue_share, 1)          AS ingresos_pct,
       avg_revenue_per_customer               AS gasto_medio,
       avg_orders                             AS pedidos_medios,
       avg_recency_days                       AS dias_desde_ultima_compra
FROM analytics.v_segment_summary
ORDER BY display_order;
