-- collector_domain: active_connections
-- Identidades are pseudonymized and non-sakila database names are suppressed.
SELECT
  CONCAT('sha256:', LEFT(SHA2(COALESCE(t.processlist_user, ''), 256), 16)) AS actor_id,
  CONCAT('sha256:', LEFT(SHA2(COALESCE(t.processlist_host, ''), 256), 16)) AS source_id,
  CONCAT('sha256:', LEFT(SHA2(COALESCE(t.processlist_id, ''), 256), 16)) AS session_id,
  CASE
    WHEN t.processlist_db = 'sakila' THEN 'sakila'
    WHEN t.processlist_db IS NULL OR t.processlist_db = '' THEN '[none]'
    ELSE '[out_of_scope]'
  END AS database_scope,
  t.processlist_command AS command_name,
  t.processlist_time AS connected_seconds,
  CASE
    WHEN s.variable_value IS NOT NULL AND s.variable_value <> '' THEN 'tls_confirmed'
    ELSE 'tls_not_observed'
  END AS transport_observation
FROM performance_schema.threads t
LEFT JOIN performance_schema.status_by_thread s
  ON s.thread_id = t.thread_id AND s.variable_name = 'Ssl_cipher'
WHERE t.type = 'FOREGROUND' AND t.processlist_id IS NOT NULL
ORDER BY t.processlist_time DESC;
