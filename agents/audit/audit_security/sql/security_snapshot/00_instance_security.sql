-- collector_domain: instance_security
-- Global instance controls; deliberately omits host, server UUID and identities.
SELECT
  @@version AS mysql_version,
  @@version_comment AS mysql_edition,
  @@global.require_secure_transport AS require_secure_transport,
  @@global.default_password_lifetime AS default_password_lifetime_days,
  @@global.local_infile AS local_infile,
  @@global.skip_name_resolve AS skip_name_resolve,
  @@global.partial_revokes AS partial_revokes,
  (SELECT plugin_status FROM information_schema.plugins
    WHERE plugin_name = 'audit_log' LIMIT 1) AS audit_plugin_status,
  (SELECT plugin_library FROM information_schema.plugins
    WHERE plugin_name = 'audit_log' LIMIT 1) AS audit_plugin_library,
  (SELECT variable_value FROM performance_schema.global_variables
    WHERE variable_name = 'audit_log_format' LIMIT 1) AS audit_log_format,
  (SELECT variable_value FROM performance_schema.global_variables
    WHERE variable_name = 'audit_log_disable' LIMIT 1) AS audit_log_disable,
  (SELECT variable_value FROM performance_schema.global_variables
    WHERE variable_name = 'audit_log_strategy' LIMIT 1) AS audit_log_strategy,
  (SELECT variable_value FROM performance_schema.global_variables
    WHERE variable_name = 'audit_log_encryption' LIMIT 1) AS audit_log_encryption,
  (SELECT variable_value FROM performance_schema.global_variables
    WHERE variable_name = 'tls_version' LIMIT 1) AS tls_version;
