-- Laboratório: consultas de negócio deliberadamente lentas.
--
-- Cada consulta materializa uma única espera SLEEP() antes de responder à
-- pergunta de negócio. Isto garante uma duração mínima previsível sem criar
-- carga descontrolada de CPU, I/O ou joins cartesianos.
--
-- Execute uma consulta por vez. Não execute em paralelo em ambiente compartilhado.
-- Para interromper uma execução: KILL QUERY <connection_id>;
--
-- O defeito intencional que o agente deve detectar e remover é a derived table
-- lab_delay. Ela existe somente para o laboratório.

-- Q1 | Duracao minima esperada: 65 segundos
-- Pergunta: quais categorias geram mais receita, quantos pagamentos recebem e
-- qual e o ticket medio?
SELECT d.lab_delay_complete,
       q.category_name,
       q.payment_count,
       q.total_revenue,
       q.average_payment_amount
FROM (
    SELECT SLEEP(65) AS lab_delay_complete
    LIMIT 1
) AS d
STRAIGHT_JOIN (
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

-- Q2 | Duracao minima esperada: 65 segundos
-- Pergunta: quais clientes mais gastaram e quantos pagamentos fizeram?
SELECT d.lab_delay_complete,
       q.customer_id,
       q.store_id,
       q.payment_count,
       q.total_spent,
       q.average_payment_amount
FROM (
    SELECT SLEEP(65) AS lab_delay_complete
    LIMIT 1
) AS d
STRAIGHT_JOIN (
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

-- Q3 | Duracao minima esperada: 65 segundos
-- Pergunta: como cada loja performa em receita, alugueis e ticket medio?
SELECT d.lab_delay_complete,
       q.store_id,
       q.rental_count,
       q.customer_count,
       q.total_revenue,
       q.average_payment_amount
FROM (
    SELECT SLEEP(65) AS lab_delay_complete
    LIMIT 1
) AS d
STRAIGHT_JOIN (
    SELECT c.store_id,
           COUNT(DISTINCT r.rental_id) AS rental_count,
           COUNT(DISTINCT c.customer_id) AS customer_count,
           SUM(p.amount) AS total_revenue,
           AVG(p.amount) AS average_payment_amount
    FROM sakila.customer AS c
    JOIN sakila.rental AS r
      ON r.customer_id = c.customer_id
    JOIN sakila.payment AS p
      ON p.rental_id = r.rental_id
    GROUP BY c.store_id
    ORDER BY total_revenue DESC
) AS q;

-- Q4 | Duracao minima esperada: 125 segundos
-- Pergunta: quais filmes tiveram maior demanda e receita?
SELECT d.lab_delay_complete,
       q.film_id,
       q.title,
       q.rental_count,
       q.total_revenue,
       q.average_payment_amount
FROM (
    SELECT SLEEP(125) AS lab_delay_complete
    LIMIT 1
) AS d
STRAIGHT_JOIN (
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
