-- collector_domain: global_privileges
SELECT
  CONCAT('sha256:', LEFT(SHA2(grantee, 256), 16)) AS grantee_id,
  privilege_type,
  is_grantable = 'YES' AS grantable,
  privilege_type IN (
    'FILE', 'SUPER', 'SYSTEM_USER', 'SYSTEM_VARIABLES_ADMIN',
    'PERSIST_RO_VARIABLES_ADMIN', 'CONNECTION_ADMIN', 'ROLE_ADMIN',
    'AUDIT_ADMIN', 'BACKUP_ADMIN', 'CLONE_ADMIN', 'SHUTDOWN'
  ) AS elevated_privilege
FROM information_schema.user_privileges
ORDER BY elevated_privilege DESC, grantable DESC, privilege_type, grantee_id;
