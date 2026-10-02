-- name: slow_log_settings
SELECT
  CASE
    WHEN @@GLOBAL.slow_query_log = 1 THEN 'YES'
    ELSE 'NO'
  END AS slow_query_log_enabled,
  @@GLOBAL.log_output AS log_output,
  ROUND(@@GLOBAL.long_query_time, 6) AS long_query_time_seconds;

-- name: slow_queries
SELECT
  CAST(start_time AS CHAR) AS started_at_mysql,
  ROUND(TIME_TO_SEC(query_time), 6) AS query_time_seconds,
  ROUND(TIME_TO_SEC(lock_time), 6) AS lock_time_seconds,
  rows_sent,
  rows_examined,
  CAST(db AS CHAR) AS schema_name,
  REGEXP_REPLACE(
    CONVERT(sql_text USING utf8mb4),
    '[[:space:]]+',
    ' '
  ) AS sql_text
FROM mysql.slow_log
WHERE db = 'sakila'
  AND start_time >= UTC_TIMESTAMP(6)
    - INTERVAL {{slow_log_lookback_minutes}} MINUTE
  AND CONVERT(sql_text USING utf8mb4)
    REGEXP '^[[:space:]]*(SELECT|WITH)[[:space:]]'
ORDER BY query_time DESC, start_time DESC
LIMIT {{slow_log_limit}};
