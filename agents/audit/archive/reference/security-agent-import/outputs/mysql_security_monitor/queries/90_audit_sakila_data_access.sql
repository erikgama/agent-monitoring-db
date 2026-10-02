/* Acesso a tabelas Sakila. table_access não traz status final da instrução. */
WITH payload AS (
  SELECT CONVERT(audit_log_read(JSON_OBJECT(
    'start', JSON_OBJECT('timestamp', DATE_FORMAT(@audit_start_utc, '%Y-%m-%d %H:%i:%s')),
    'max_array_length', @audit_max_events
  )) USING utf8mb4) AS document
)
SELECT
  occurred_at_utc, event_id,
  CASE WHEN table_event = 'read' AND sql_command = 'select' THEN 'SELECT' ELSE UPPER(table_event) END AS operation,
  sql_command, object_schema, object_table,
  CONCAT('sha256:', LEFT(SHA2(COALESCE(account_user, ''), 256), 16)) AS actor_id,
  CONCAT('sha256:', LEFT(SHA2(COALESCE(login_ip, ''), 256), 16)) AS source_id,
  CASE WHEN query_text IS NULL THEN NULL ELSE CONCAT('sha256:', LEFT(SHA2(query_text, 256), 16)) END AS sql_fingerprint
FROM payload
CROSS JOIN JSON_TABLE(document, '$[*]' COLUMNS (
  occurred_at_utc VARCHAR(32) PATH '$.timestamp',
  event_id BIGINT PATH '$.id' NULL ON EMPTY,
  event_class VARCHAR(32) PATH '$.class',
  table_event VARCHAR(32) PATH '$.event',
  sql_command VARCHAR(64) PATH '$.table_access_data.sql_command' NULL ON EMPTY,
  object_schema VARCHAR(255) PATH '$.table_access_data.db' NULL ON EMPTY,
  object_table VARCHAR(255) PATH '$.table_access_data.table' NULL ON EMPTY,
  query_text LONGTEXT PATH '$.table_access_data.query' NULL ON EMPTY,
  account_user VARCHAR(255) PATH '$.account.user' NULL ON EMPTY,
  login_ip VARCHAR(255) PATH '$.login.ip' NULL ON EMPTY
)) event_row
WHERE event_class = 'table_access' AND object_schema = 'sakila'
ORDER BY occurred_at_utc DESC, event_id DESC;
