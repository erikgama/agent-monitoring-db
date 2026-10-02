/* Eventos Enterprise Audit de conexão na janela configurada pelo coletor. */
WITH payload AS (
  SELECT CONVERT(audit_log_read(JSON_OBJECT(
    'start', JSON_OBJECT('timestamp', DATE_FORMAT(@audit_start_utc, '%Y-%m-%d %H:%i:%s')),
    'max_array_length', @audit_max_events
  )) USING utf8mb4) AS document
)
SELECT
  occurred_at_utc, event_id, connection_event,
  CASE WHEN status_code = 0 THEN 'success' WHEN status_code IS NULL THEN 'unknown' ELSE 'failure' END AS outcome,
  status_code, connection_type,
  CASE WHEN account_user IS NULL OR account_user = '' THEN NULL ELSE CONCAT('sha256:', LEFT(SHA2(account_user, 256), 16)) END AS actor_id,
  CASE WHEN login_ip IS NULL OR login_ip = '' THEN NULL ELSE CONCAT('sha256:', LEFT(SHA2(login_ip, 256), 16)) END AS source_id
FROM payload
CROSS JOIN JSON_TABLE(document, '$[*]' COLUMNS (
  occurred_at_utc VARCHAR(32) PATH '$.timestamp',
  event_id BIGINT PATH '$.id' NULL ON EMPTY,
  event_class VARCHAR(32) PATH '$.class',
  connection_event VARCHAR(64) PATH '$.event',
  status_code INT PATH '$.connection_data.status' NULL ON EMPTY,
  connection_type VARCHAR(64) PATH '$.connection_data.connection_type' NULL ON EMPTY,
  account_user VARCHAR(255) PATH '$.account.user' NULL ON EMPTY,
  login_ip VARCHAR(255) PATH '$.login.ip' NULL ON EMPTY
)) event_row
WHERE event_class = 'connection'
ORDER BY occurred_at_utc DESC, event_id DESC;
