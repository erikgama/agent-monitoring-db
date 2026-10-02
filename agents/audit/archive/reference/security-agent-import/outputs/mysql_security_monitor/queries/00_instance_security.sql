/* Instância e controles de segurança — somente leitura. */
SELECT
  @@server_uuid AS server_uuid,
  @@version AS mysql_version,
  @@version_comment AS mysql_edition,
  @@hostname AS hostname,
  @@port AS port,
  @@global.require_secure_transport AS require_secure_transport,
  @@global.default_password_lifetime AS default_password_lifetime_days,
  @@global.local_infile AS local_infile,
  @@global.skip_name_resolve AS skip_name_resolve,
  @@global.partial_revokes AS partial_revokes,
  (SELECT plugin_status FROM information_schema.plugins
    WHERE plugin_name = 'audit_log' LIMIT 1) AS audit_plugin_status,
  (SELECT variable_value FROM performance_schema.global_variables
    WHERE variable_name = 'audit_log_format' LIMIT 1) AS audit_log_format,
  (SELECT variable_value FROM performance_schema.global_variables
    WHERE variable_name = 'audit_log_disable' LIMIT 1) AS audit_log_disable,
  EXISTS(
    SELECT 1 FROM mysql.global_grants
    WHERE user = SUBSTRING_INDEX(CURRENT_USER(), '@', 1)
      AND host = SUBSTRING_INDEX(CURRENT_USER(), '@', -1)
      AND priv = 'AUDIT_ADMIN'
  ) AS collector_has_audit_admin,
  (SELECT variable_value FROM performance_schema.global_variables
    WHERE variable_name = 'tls_version' LIMIT 1) AS tls_version;
