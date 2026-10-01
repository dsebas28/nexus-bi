-- titulo: Ingresos mensuales con media móvil y acumulado
-- descripcion: Funciones de ventana con marcos (ROWS BETWEEN): media móvil de 3 meses para suavizar la estacionalidad y total acumulado. Solo meses completos, según la vista analytics.v_monthly_kpis.
SELECT to_char(month, 'YYYY-MM')                                                          AS mes,
       orders                                                                            AS pedidos,
       revenue                                                                           AS ingresos,
       round(avg(revenue) OVER (ORDER BY month ROWS BETWEEN 2 PRECEDING AND CURRENT ROW), 2) AS media_movil_3m,
       sum(revenue) OVER (ORDER BY month)                                                AS acumulado
FROM analytics.v_monthly_kpis
WHERE is_complete
ORDER BY month;
