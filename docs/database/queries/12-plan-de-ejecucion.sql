-- titulo: Plan de ejecución: recencia de un cliente
-- descripcion: La recencia (días desde la última compra) alimenta el RFM y el churn. EXPLAIN ANALYZE muestra que se resuelve solo con el índice compuesto ix_orders_customer_date (customer_key, purchased_at): Index Only Scan, sin leer la tabla de 99.441 pedidos (Heap Fetches: 0).
EXPLAIN (ANALYZE, COSTS OFF, TIMING OFF, SUMMARY OFF, BUFFERS OFF)
SELECT max(purchased_at) AS ultima_compra,
       count(*)          AS pedidos
FROM core.orders
WHERE customer_key = 6746;
