-- name: sys_processlist
SELECT
  conn_id AS connection_id,
  user,
  db,
  command,
  time AS running_seconds,
  state,
  statement_latency,
  lock_latency,
  rows_examined,
  rows_sent,
  rows_affected,
  tmp_tables,
  tmp_disk_tables,
  full_scan
FROM sys.processlist
WHERE command <> 'Sleep'
  AND db = 'sakila'
ORDER BY time DESC
LIMIT {{sessions_limit}};

-- name: threads_fallback
SELECT
  t.PROCESSLIST_ID AS connection_id,
  t.PROCESSLIST_USER AS user,
  t.PROCESSLIST_HOST AS host,
  t.PROCESSLIST_DB AS db,
  t.PROCESSLIST_COMMAND AS command,
  t.PROCESSLIST_TIME AS running_seconds,
  t.PROCESSLIST_STATE AS state
FROM performance_schema.threads t
WHERE t.TYPE = 'FOREGROUND'
  AND t.PROCESSLIST_COMMAND <> 'Sleep'
  AND t.PROCESSLIST_DB = 'sakila'
ORDER BY t.PROCESSLIST_TIME DESC
LIMIT {{sessions_limit}};
