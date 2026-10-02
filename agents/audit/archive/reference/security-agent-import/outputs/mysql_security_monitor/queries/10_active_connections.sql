/* Conexões ativas agora. Identidades e hosts são pseudonimizados. */
SELECT
  CONCAT('sha256:', LEFT(SHA2(COALESCE(t.processlist_user, ''), 256), 16)) AS actor_id,
  CONCAT('sha256:', LEFT(SHA2(COALESCE(t.processlist_host, ''), 256), 16)) AS source_id,
  CONCAT('sha256:', LEFT(SHA2(COALESCE(t.processlist_id, ''), 256), 16)) AS session_id,
  COALESCE(t.processlist_db, '[none]') AS database_name,
  t.processlist_command AS command_name,
  t.processlist_time AS connected_seconds,
  CASE
    WHEN s.variable_value IS NOT NULL AND s.variable_value <> '' THEN 'tls_confirmed'
    WHEN t.processlist_host IS NULL OR t.processlist_host = '' THEN 'local_or_unknown'
    ELSE 'tls_not_confirmed'
  END AS transport_assessment
FROM performance_schema.threads t
LEFT JOIN performance_schema.status_by_thread s
  ON s.thread_id = t.thread_id AND s.variable_name = 'Ssl_cipher'
WHERE t.type = 'FOREGROUND' AND t.processlist_id IS NOT NULL
ORDER BY t.processlist_time DESC;
