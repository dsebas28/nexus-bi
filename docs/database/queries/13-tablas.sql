-- titulo: Tablas y vistas materializadas por tamaño
-- descripcion: Estadísticas del propio PostgreSQL: filas y espacio en disco (datos más índices) de cada tabla de los esquemas core, ml y ops, y de las vistas materializadas de analytics.
SELECT n.nspname || '.' || c.relname                    AS tabla,
       CASE c.relkind WHEN 'm' THEN 'vista materializada' ELSE 'tabla' END AS tipo,
       c.reltuples::bigint                              AS filas,
       pg_size_pretty(pg_total_relation_size(c.oid))    AS tamano
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname IN ('core', 'ml', 'ops', 'analytics')
  AND c.relkind IN ('r', 'm')
ORDER BY pg_total_relation_size(c.oid) DESC
LIMIT 15;
