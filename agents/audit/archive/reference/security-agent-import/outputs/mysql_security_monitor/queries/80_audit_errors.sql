/* Falhas de conexão e comandos gerais no Enterprise Audit. */
WITH payload AS (
  SELECT CONVERT(audit_log_read(JSON_OBJECT(
    'start', JSON_OBJECT('timestamp', DATE_FORMAT(@audit_start_utc, '%Y-%m-%d %H:%i:%s')),
    'max_array_length', @audit_max_events
  )) USING utf8mb4) AS document
)
SELECT
  occurred_at_utc, event_id, event_class, event_name,
  COALESCE(connection_status, general_status) AS mysql_error_code,
  sql_command,
  CONCAT('sha256:', LEFT(SHA2(COALESCE(account_user, ''), 256), 16)) AS actor_id,
  CONCAT('sha256:', LEFT(SHA2(COALESCE(login_ip, ''), 256), 16)) AS source_id,
  CASE WHEN query_text IS NULL THEN NULL ELSE CONCAT('sha256:', LEFT(SHA2(query_text, 256), 16)) END AS sql_fingerprint
FROM payload
CROSS JOIN JSON_TABLE(document, '$[*]' COLUMNS (
  occurred_at_utc VARCHAR(32) PATH '$.timestamp',
  event_id BIGINT PATH '$.id' NULL ON EMPTY,
  event_class VARCHAR(32) PATH '$.class',
  event_name VARCHAR(32) PATH '$.event',
  connection_status INT PATH '$.connection_data.status' NULL ON EMPTY,
  general_status INT PATH '$.general_data.status' NULL ON EMPTY,
  sql_command VARCHAR(64) PATH '$.general_data.sql_command' NULL ON EMPTY,
  query_text LONGTEXT PATH '$.general_data.query' NULL ON EMPTY,
  account_user VARCHAR(255) PATH '$.account.user' NULL ON EMPTY,
  login_ip VARCHAR(255) PATH '$.login.ip' NULL ON EMPTY
)) event_row
WHERE event_class IN ('connection', 'general')
  AND COALESCE(connection_status, general_status) <> 0
ORDER BY occurred_at_utc DESC, event_id DESC;
