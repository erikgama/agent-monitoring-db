-- name: deadlocks
SELECT
  ERROR_NUMBER,
  ERROR_NAME,
  SUM_ERROR_RAISED AS deadlocks
FROM performance_schema.events_errors_summary_global_by_error
WHERE ERROR_NUMBER = 1213;

-- name: lock_waits
SELECT
  r.ENGINE_TRANSACTION_ID AS waiting_transaction_id,
  r.THREAD_ID AS waiting_thread_id,
  r.OBJECT_SCHEMA AS waiting_schema,
  r.OBJECT_NAME AS waiting_object,
  r.LOCK_TYPE AS waiting_lock_type,
  r.LOCK_MODE AS waiting_lock_mode,
  b.ENGINE_TRANSACTION_ID AS blocking_transaction_id,
  b.THREAD_ID AS blocking_thread_id,
  b.LOCK_TYPE AS blocking_lock_type,
  b.LOCK_MODE AS blocking_lock_mode
FROM performance_schema.data_lock_waits w
JOIN performance_schema.data_locks r
  ON w.REQUESTING_ENGINE_LOCK_ID = r.ENGINE_LOCK_ID
JOIN performance_schema.data_locks b
  ON w.BLOCKING_ENGINE_LOCK_ID = b.ENGINE_LOCK_ID
WHERE r.OBJECT_SCHEMA = 'sakila'
LIMIT {{locks_limit}};

-- name: table_lock_waits
SELECT
  object_schema,
  object_name,
  COUNT(*) AS wait_count,
  MAX(waiting_query_secs) AS max_wait_seconds
FROM sys.schema_table_lock_waits
WHERE object_schema = 'sakila'
GROUP BY object_schema, object_name
ORDER BY wait_count DESC
LIMIT {{locks_limit}};
