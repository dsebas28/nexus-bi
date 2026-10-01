-- titulo: Función SQL de KPIs: un periodo contra el anterior
-- descripcion: analytics.kpi_summary() es una función SQL (STABLE) que devuelve una tabla: compara cualquier periodo con el anterior, con filtros opcionales de estado y categoría. Aquí, el segundo trimestre de 2018 en São Paulo frente al primero.
SELECT metric                     AS metrica,
       round(current_value, 2)    AS valor_actual,
       round(previous_value, 2)   AS valor_anterior,
       round(change_abs, 2)       AS cambio,
       round(change_pct, 2)       AS cambio_pct
FROM analytics.kpi_summary('2018-04-01', '2018-06-30', 'SP');
