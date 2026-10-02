-- name: top_digests_modern
SELECT
  SCHEMA_NAME,
  DIGEST,
  DIGEST_TEXT,
  COUNT_STAR AS executions,
  ROUND(SUM_TIMER_WAIT / 1000000000000, 3) AS total_latency_seconds,
  ROUND(AVG_TIMER_WAIT / 1000000000000, 6) AS avg_latency_seconds,
  ROUND(QUANTILE_95 / 1000000000000, 6) AS p95_latency_seconds,
  SUM_ROWS_EXAMINED AS rows_examined,
  SUM_ROWS_SENT AS rows_sent,
  SUM_CREATED_TMP_TABLES AS tmp_tables,
  SUM_CREATED_TMP_DISK_TABLES AS tmp_disk_tables,
  SUM_SORT_ROWS AS sort_rows,
  SUM_NO_INDEX_USED AS no_index_used,
  SUM_NO_GOOD_INDEX_USED AS no_good_index_used,
  SUM_ERRORS AS errors,
  SUM_WARNINGS AS warnings,
  FIRST_SEEN,
  LAST_SEEN,
  COUNT_SECONDARY AS heatwave_secondary_executions
FROM performance_schema.events_statements_summary_by_digest
WHERE DIGEST IS NOT NULL
  AND SCHEMA_NAME = 'sakila'
ORDER BY SUM_TIMER_WAIT DESC
LIMIT {{workload_limit}};

-- name: top_digests_legacy
SELECT
  SCHEMA_NAME,
  DIGEST,
  DIGEST_TEXT,
  COUNT_STAR AS executions,
  ROUND(SUM_TIMER_WAIT / 1000000000000, 3) AS total_latency_seconds,
  ROUND(AVG_TIMER_WAIT / 1000000000000, 6) AS avg_latency_seconds,
  NULL AS p95_latency_seconds,
  SUM_ROWS_EXAMINED AS rows_examined,
  SUM_ROWS_SENT AS rows_sent,
  SUM_CREATED_TMP_TABLES AS tmp_tables,
  SUM_CREATED_TMP_DISK_TABLES AS tmp_disk_tables,
  SUM_SORT_ROWS AS sort_rows,
  SUM_NO_INDEX_USED AS no_index_used,
  SUM_NO_GOOD_INDEX_USED AS no_good_index_used,
  SUM_ERRORS AS errors,
  SUM_WARNINGS AS warnings,
  FIRST_SEEN,
  LAST_SEEN,
  NULL AS heatwave_secondary_executions
FROM performance_schema.events_statements_summary_by_digest
WHERE DIGEST IS NOT NULL
  AND SCHEMA_NAME = 'sakila'
ORDER BY SUM_TIMER_WAIT DESC
LIMIT {{workload_limit}};

-- name: aggregate
SELECT
  SUM(COUNT_STAR) AS statements,
  ROUND(SUM(SUM_TIMER_WAIT) / 1000000000000, 3) AS total_latency_seconds,
  SUM(SUM_CREATED_TMP_DISK_TABLES) AS tmp_disk_tables,
  SUM(SUM_NO_INDEX_USED) AS no_index_used,
  SUM(SUM_NO_GOOD_INDEX_USED) AS no_good_index_used,
  SUM(SUM_ERRORS) AS statement_errors,
  SUM(SUM_WARNINGS) AS statement_warnings
FROM performance_schema.events_statements_summary_by_digest
WHERE DIGEST IS NOT NULL
  AND SCHEMA_NAME = 'sakila';
