/* Eventos DDL Audit; SQL literal é substituído por fingerprint. */
WITH payload AS (
  SELECT CONVERT(audit_log_read(JSON_OBJECT(
    'start', JSON_OBJECT('timestamp', DATE_FORMAT(@audit_start_utc, '%Y-%m-%d %H:%i:%s')),
    'max_array_length', @audit_max_events
  )) USING utf8mb4) AS document
)
SELECT
  occurred_at_utc, event_id, sql_command,
  CASE WHEN status_code = 0 THEN 'success' WHEN status_code IS NULL THEN 'unknown' ELSE 'failure' END AS outcome,
  status_code,
  CONCAT('sha256:', LEFT(SHA2(COALESCE(account_user, ''), 256), 16)) AS actor_id,
  CONCAT('sha256:', LEFT(SHA2(COALESCE(login_ip, ''), 256), 16)) AS source_id,
  CASE WHEN query_text IS NULL THEN NULL ELSE CONCAT('sha256:', LEFT(SHA2(query_text, 256), 16)) END AS sql_fingerprint
FROM payload
CROSS JOIN JSON_TABLE(document, '$[*]' COLUMNS (
  occurred_at_utc VARCHAR(32) PATH '$.timestamp',
  event_id BIGINT PATH '$.id' NULL ON EMPTY,
  event_class VARCHAR(32) PATH '$.class',
  event_name VARCHAR(32) PATH '$.event',
  sql_command VARCHAR(64) PATH '$.general_data.sql_command' NULL ON EMPTY,
  status_code INT PATH '$.general_data.status' NULL ON EMPTY,
  query_text LONGTEXT PATH '$.general_data.query' NULL ON EMPTY,
  account_user VARCHAR(255) PATH '$.account.user' NULL ON EMPTY,
  login_ip VARCHAR(255) PATH '$.login.ip' NULL ON EMPTY
)) event_row
WHERE event_class = 'general' AND event_name = 'status'
  AND (sql_command REGEXP '^(alter_|create_|drop_)' OR sql_command IN ('truncate', 'rename_table'))
ORDER BY occurred_at_utc DESC, event_id DESC;
