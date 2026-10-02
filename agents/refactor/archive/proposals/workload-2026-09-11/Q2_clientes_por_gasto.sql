-- Nome: Clientes por gasto
-- Objetivo: Classificar clientes por gasto total, pagamentos e ticket médio.
-- Schema: sakila
-- Tipo: somente leitura
-- Frequência sugerida: validação controlada sob demanda
-- Limite: 50 clientes
-- Fontes: sakila.customer; sakila.payment
-- Lógica: espera deliberada removida; lab_delay_complete preservado como 0.
SELECT 0 AS lab_delay_complete,
       q.customer_id,
       q.store_id,
       q.payment_count,
       q.total_spent,
       q.average_payment_amount
FROM (
    SELECT c.customer_id,
           c.store_id,
           COUNT(p.payment_id) AS payment_count,
           SUM(p.amount) AS total_spent,
           AVG(p.amount) AS average_payment_amount
    FROM sakila.customer AS c
    JOIN sakila.payment AS p
      ON p.customer_id = c.customer_id
    GROUP BY c.customer_id, c.store_id
    ORDER BY total_spent DESC, payment_count DESC
    LIMIT 50
) AS q;
