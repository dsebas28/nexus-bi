-- titulo: Informe de calidad de datos
-- descripcion: El pipeline registra cada problema que detecta en ops.data_quality_issues: qué tabla, qué regla, cuántas filas y qué se hizo. El informe es una consulta, no un texto escrito a mano.
SELECT category                     AS tipo,
       action_taken                 AS accion,
       count(*)                     AS reglas,
       sum(rows_affected)           AS filas_afectadas,
       string_agg(DISTINCT source_table, ', ' ORDER BY source_table) AS tablas
FROM analytics.v_data_quality_latest
WHERE rows_affected > 0
GROUP BY category, action_taken
ORDER BY filas_afectadas DESC;
