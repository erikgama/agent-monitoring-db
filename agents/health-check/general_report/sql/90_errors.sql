-- name: errors
SELECT
  ERROR_NUMBER,
  ERROR_NAME,
  SQL_STATE,
  SUM_ERROR_RAISED AS error_count,
  SUM_ERROR_HANDLED AS handled_count,
  FIRST_SEEN,
  LAST_SEEN
FROM performance_schema.events_errors_summary_global_by_error
WHERE SUM_ERROR_RAISED > 0
ORDER BY SUM_ERROR_RAISED DESC
LIMIT {{errors_limit}};
