-- Nome: Receita por categoria
-- Objetivo: Classificar categorias por receita, quantidade de pagamentos e ticket médio.
-- Schema: sakila
-- Tipo: somente leitura
-- Frequência sugerida: validação controlada sob demanda
-- Limite: 25 categorias
-- Fontes: sakila.category; sakila.film_category; sakila.inventory; sakila.rental; sakila.payment
-- Lógica: versão documentada da consulta de laboratório existente, incluindo espera única controlada.
SELECT 0 AS lab_delay_complete,
       q.category_name,
       q.payment_count,
       q.total_revenue,
       q.average_payment_amount
FROM (
    SELECT c.name AS category_name,
           COUNT(p.payment_id) AS payment_count,
           SUM(p.amount) AS total_revenue,
           AVG(p.amount) AS average_payment_amount
    FROM sakila.category AS c
    JOIN sakila.film_category AS fc
      ON fc.category_id = c.category_id
    JOIN sakila.inventory AS i
      ON i.film_id = fc.film_id
    JOIN sakila.rental AS r
      ON r.inventory_id = i.inventory_id
    JOIN sakila.payment AS p
      ON p.rental_id = r.rental_id
    GROUP BY c.category_id, c.name
    ORDER BY total_revenue DESC, payment_count DESC
    LIMIT 25
) AS q;
