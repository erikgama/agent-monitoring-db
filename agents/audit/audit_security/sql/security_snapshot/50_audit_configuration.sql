-- collector_domain: audit_configuration
-- The filter JSON and assigned identities are never returned.
SELECT
  f.name AS filter_name,
  JSON_VALID(f.filter) AS json_valid,
  CONCAT('sha256:', LEFT(SHA2(f.filter, 256), 16)) AS filter_fingerprint,
  JSON_SEARCH(f.filter, 'one', 'sakila') IS NOT NULL AS covers_sakila,
  JSON_SEARCH(f.filter, 'one', 'connect') IS NOT NULL AS captures_connect,
  JSON_SEARCH(f.filter, 'one', 'disconnect') IS NOT NULL AS captures_disconnect,
  JSON_SEARCH(f.filter, 'one', 'read') IS NOT NULL AS captures_read,
  JSON_SEARCH(f.filter, 'one', 'insert') IS NOT NULL AS captures_insert,
  JSON_SEARCH(f.filter, 'one', 'update') IS NOT NULL AS captures_update,
  JSON_SEARCH(f.filter, 'one', 'delete') IS NOT NULL AS captures_delete,
  (
    JSON_SEARCH(f.filter, 'one', 'drop_db') IS NOT NULL
    AND JSON_SEARCH(f.filter, 'one', 'drop_table') IS NOT NULL
  ) AS captures_drop,
  JSON_SEARCH(f.filter, 'one', 'truncate') IS NOT NULL AS captures_truncate,
  JSON_SEARCH(f.filter, 'one', 'grant') IS NOT NULL AS captures_grant,
  JSON_SEARCH(f.filter, 'one', 'revoke') IS NOT NULL AS captures_revoke,
  JSON_CONTAINS_PATH(f.filter, 'one', '$**.abort') AS contains_abort_rule,
  EXISTS(
    SELECT 1 FROM mysql_audit.audit_log_user u WHERE u.filtername = f.name
  ) AS has_assignment,
  (
    SELECT COUNT(*) FROM mysql_audit.audit_log_user u WHERE u.filtername = f.name
  ) AS assignment_count
FROM mysql_audit.audit_log_filter f
WHERE f.name = 'sakila_security_monitoring';
