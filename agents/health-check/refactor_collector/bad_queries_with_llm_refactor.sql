/*
  Catálogo de consultas lentas conhecidas e versionadas do Sakila.
  Este arquivo contém somente as SQLs originais. A proposta é criada pelo
  Advisor do Refactor durante o processamento do pedido.
  Substitua {{MAX_EXECUTION_TIME_MS}} por um limite apropriado para staging.
*/

/* query: correlated_running_total */
SELECT /*+ MAX_EXECUTION_TIME({{MAX_EXECUTION_TIME_MS}}) */
  c.customer_id,
  p.payment_date,
  p.amount AS revenue,
  (SELECT SUM(p2.amount)
     FROM payment p2
    WHERE CAST(p2.customer_id AS CHAR) = CAST(c.customer_id AS CHAR)
      AND DATE(p2.payment_date) <= DATE(p.payment_date)) AS cumulative_revenue
FROM customer c
JOIN payment p ON CAST(p.customer_id AS CHAR) = CAST(c.customer_id AS CHAR)
WHERE DATE_FORMAT(p.payment_date, '%Y-%m') >= '2005-01';

/* query: daily_distinct_customer_pairs */
SELECT /*+ MAX_EXECUTION_TIME({{MAX_EXECUTION_TIME_MS}}) */
  DATE(p1.payment_date) AS payment_day,
  COUNT(*) AS pair_count
FROM payment p1
JOIN payment p2 ON DATE(p1.payment_date) = DATE(p2.payment_date)
             AND CAST(p1.customer_id AS CHAR) <> CAST(p2.customer_id AS CHAR)
WHERE DATE_FORMAT(p1.payment_date, '%Y-%m') >= '2005-01'
GROUP BY DATE(p1.payment_date)
ORDER BY pair_count DESC;

/* query: daily_customer_pair_ranking */
SELECT /*+ MAX_EXECUTION_TIME({{MAX_EXECUTION_TIME_MS}}) */
  DATE_FORMAT(p1.payment_date, '%Y-%m-%d') AS payment_day,
  CONCAT(p1.customer_id, '-', p2.customer_id) AS customer_pair,
  COUNT(*) AS pair_count
FROM payment p1
JOIN payment p2 ON DATE(p1.payment_date) = DATE(p2.payment_date)
WHERE DATE_FORMAT(p1.payment_date, '%Y-%m') >= '2005-01'
GROUP BY DATE_FORMAT(p1.payment_date, '%Y-%m-%d'), CONCAT(p1.customer_id, '-', p2.customer_id)
ORDER BY pair_count DESC, customer_pair;
