SELECT 0 AS lab_delay_complete,
       q.store_id,
       q.rental_count,
       q.customer_count,
       q.total_revenue,
       q.average_payment_amount
FROM (
    SELECT c.store_id,
           COUNT(DISTINCT r.rental_id) AS rental_count,
           COUNT(DISTINCT c.customer_id) AS customer_count,
           SUM(p.amount) AS total_revenue,
           AVG(p.amount) AS average_payment_amount
    FROM sakila.customer AS c
    JOIN sakila.rental AS r ON r.customer_id = c.customer_id
    JOIN sakila.payment AS p ON p.rental_id = r.rental_id
    GROUP BY c.store_id
    ORDER BY total_revenue DESC
    LIMIT 10
) AS q;
