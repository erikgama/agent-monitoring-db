-- name: select_digest_counters
SELECT
  SCHEMA_NAME AS schema_name,
  DIGEST AS digest,
  DIGEST_TEXT AS digest_text,
  COUNT_STAR AS executions,
  SUM_TIMER_WAIT AS total_latency_picoseconds,
  SUM_LOCK_TIME AS total_lock_time_picoseconds,
  SUM_ROWS_EXAMINED AS rows_examined,
  SUM_ROWS_SENT AS rows_sent,
  SUM_CREATED_TMP_TABLES AS tmp_tables,
  SUM_CREATED_TMP_DISK_TABLES AS tmp_disk_tables,
  SUM_SORT_ROWS AS sort_rows,
  SUM_NO_INDEX_USED AS no_index_used,
  SUM_NO_GOOD_INDEX_USED AS no_good_index_used,
  SUM_ERRORS AS errors,
  SUM_WARNINGS AS warnings,
  FIRST_SEEN AS first_seen,
  LAST_SEEN AS last_seen
FROM performance_schema.events_statements_summary_by_digest
WHERE DIGEST IS NOT NULL
  AND SCHEMA_NAME = 'sakila'
  AND DIGEST_TEXT LIKE 'SELECT %'
  AND DIGEST_TEXT <> 'SELECT ?'
  AND DIGEST = '97eb2e3ec6c2ecefc310ed0c449384c04c59dc149f6cb31ca75e95a9a7836fe5'
ORDER BY DIGEST;

-- name: select_digest_histogram
SELECT
  h.SCHEMA_NAME AS schema_name,
  h.DIGEST AS digest,
  h.BUCKET_NUMBER AS bucket_number,
  h.BUCKET_TIMER_LOW AS bucket_timer_low,
  h.BUCKET_TIMER_HIGH AS bucket_timer_high,
  h.COUNT_BUCKET AS count_bucket
FROM performance_schema.events_statements_histogram_by_digest h
JOIN performance_schema.events_statements_summary_by_digest s
  ON s.SCHEMA_NAME = h.SCHEMA_NAME
  AND s.DIGEST = h.DIGEST
WHERE h.DIGEST IS NOT NULL
  AND h.SCHEMA_NAME = 'sakila'
  AND s.DIGEST_TEXT LIKE 'SELECT %'
  AND s.DIGEST_TEXT <> 'SELECT ?'
  AND h.DIGEST = '97eb2e3ec6c2ecefc310ed0c449384c04c59dc149f6cb31ca75e95a9a7836fe5'
  AND h.COUNT_BUCKET > 0
ORDER BY h.DIGEST, h.BUCKET_NUMBER;

-- name: percentile_capabilities
SELECT
  TABLE_NAME AS table_name,
  COLUMN_NAME AS column_name
FROM information_schema.columns
WHERE TABLE_SCHEMA = 'performance_schema'
  AND (
    (
      TABLE_NAME = 'events_statements_summary_by_digest'
      AND COLUMN_NAME IN ('QUANTILE_95', 'QUANTILE_99')
    )
    OR (
      TABLE_NAME = 'events_statements_histogram_by_digest'
      AND COLUMN_NAME IN (
        'SCHEMA_NAME',
        'DIGEST',
        'BUCKET_NUMBER',
        'BUCKET_TIMER_LOW',
        'BUCKET_TIMER_HIGH',
        'COUNT_BUCKET'
      )
    )
    OR (
      TABLE_NAME = 'events_statements_history_long'
      AND COLUMN_NAME IN (
        'THREAD_ID',
        'EVENT_ID',
        'END_EVENT_ID',
        'EVENT_NAME',
        'CURRENT_SCHEMA',
        'DIGEST',
        'TIMER_WAIT'
      )
    )
  )
ORDER BY TABLE_NAME, COLUMN_NAME;

-- name: history_consumer
SELECT
  NAME AS consumer_name,
  ENABLED AS enabled
FROM performance_schema.setup_consumers
WHERE NAME = 'events_statements_history_long';
