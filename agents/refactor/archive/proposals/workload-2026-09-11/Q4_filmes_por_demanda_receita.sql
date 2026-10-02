-- Nome: Filmes por demanda e receita
-- Objetivo: Classificar filmes por demanda, receita e ticket médio.
-- Schema: sakila
-- Tipo: somente leitura
-- Frequência sugerida: validação controlada sob demanda
-- Limite: 50 filmes
-- Fontes: sakila.film; sakila.inventory; sakila.rental; sakila.payment
-- Lógica: espera deliberada removida; lab_delay_complete preservado como 0.
SELECT 0 AS lab_delay_complete,
       q.film_id,
       q.title,
       q.rental_count,
       q.total_revenue,
       q.average_payment_amount
FROM (
    SELECT f.film_id,
           f.title,
           COUNT(DISTINCT r.rental_id) AS rental_count,
           SUM(p.amount) AS total_revenue,
           AVG(p.amount) AS average_payment_amount
    FROM sakila.film AS f
    JOIN sakila.inventory AS i
      ON i.film_id = f.film_id
    JOIN sakila.rental AS r
      ON r.inventory_id = i.inventory_id
    JOIN sakila.payment AS p
      ON p.rental_id = r.rental_id
    GROUP BY f.film_id, f.title
    ORDER BY total_revenue DESC, rental_count DESC
    LIMIT 50
) AS q;
