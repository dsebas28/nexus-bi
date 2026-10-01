-- titulo: Las restricciones protegen los datos
-- descripcion: Las reglas de negocio viven en la base de datos como restricciones CHECK. Una reseña con 6 estrellas se rechaza aunque el código tuviera un error (la sentencia se ejecuta dentro de una transacción que se revierte).
UPDATE core.order_reviews
SET score = 6
WHERE review_key = 1;
