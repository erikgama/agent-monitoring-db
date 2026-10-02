-- name: status
SELECT VARIABLE_NAME, VARIABLE_VALUE
FROM performance_schema.global_status
WHERE VARIABLE_NAME IN (
  'Innodb_buffer_pool_read_requests',
  'Innodb_buffer_pool_reads',
  'Innodb_buffer_pool_pages_data',
  'Innodb_buffer_pool_pages_free',
  'Innodb_buffer_pool_pages_total',
  'Innodb_buffer_pool_wait_free',
  'Innodb_data_reads',
  'Innodb_data_writes',
  'Innodb_data_read',
  'Innodb_data_written',
  'Innodb_log_waits',
  'Innodb_os_log_written',
  'Innodb_row_lock_waits',
  'Innodb_row_lock_time',
  'Innodb_row_lock_time_avg',
  'Innodb_row_lock_time_max'
);

-- name: metrics
SELECT NAME, COUNT, COUNT_RESET, TIME_ENABLED, TIME_DISABLED
FROM information_schema.INNODB_METRICS
WHERE NAME IN (
  'buffer_pool_reads',
  'buffer_pool_read_requests',
  'buffer_pool_wait_free',
  'log_waits',
  'lock_deadlocks',
  'lock_timeouts',
  'trx_rseg_history_len'
);
