/* Capacidade do Enterprise Audit, sem expor o JSON do filtro. */
SELECT
  table_schema,
  table_name,
  table_type
FROM information_schema.tables
WHERE table_name IN ('audit_log_filter', 'audit_log_user')
ORDER BY table_schema, table_name;
