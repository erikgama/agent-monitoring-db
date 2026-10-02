-- name: instance_identity
SELECT
  VERSION() AS mysql_version,
  @@version_comment AS version_comment,
  @@performance_schema AS performance_schema_enabled;

-- name: sources
SELECT table_schema, table_name
FROM information_schema.tables
WHERE (table_schema = 'performance_schema' AND table_name IN (
         'events_statements_summary_by_digest',
         'events_errors_summary_global_by_error',
         'data_locks',
         'data_lock_waits',
         'threads',
         'global_variables',
         'global_status',
         'replication_connection_status',
         'replication_applier_status',
         'replication_group_members'
       ))
   OR (table_schema = 'sys' AND table_name IN (
         'user_summary',
         'host_summary',
         'processlist',
         'schema_table_lock_waits',
         'schema_unused_indexes',
         'schema_redundant_indexes'
       ))
   OR (table_schema = 'information_schema' AND table_name = 'INNODB_METRICS');

-- name: consumers
SELECT NAME, ENABLED
FROM performance_schema.setup_consumers
WHERE NAME IN (
  'events_statements_current',
  'events_statements_history',
  'events_statements_history_long',
  'statements_digest'
);

-- name: digest_columns
SELECT COLUMN_NAME
FROM information_schema.columns
WHERE TABLE_SCHEMA = 'performance_schema'
  AND TABLE_NAME = 'events_statements_summary_by_digest'
  AND COLUMN_NAME IN ('QUANTILE_95', 'COUNT_SECONDARY');
