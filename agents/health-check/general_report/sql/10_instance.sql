-- name: variables
SELECT VARIABLE_NAME, VARIABLE_VALUE
FROM performance_schema.global_variables
WHERE VARIABLE_NAME IN (
  'max_connections',
  'innodb_buffer_pool_size',
  'innodb_redo_log_capacity',
  'tmp_table_size',
  'max_heap_table_size',
  'performance_schema'
);

-- name: status
SELECT VARIABLE_NAME, VARIABLE_VALUE
FROM performance_schema.global_status
WHERE VARIABLE_NAME IN (
  'Uptime',
  'Threads_connected',
  'Threads_running',
  'Threads_created',
  'Connections',
  'Aborted_connects',
  'Aborted_clients',
  'Created_tmp_tables',
  'Created_tmp_disk_tables',
  'Innodb_buffer_pool_read_requests',
  'Innodb_buffer_pool_reads',
  'Innodb_log_waits',
  'Innodb_os_log_written'
);
