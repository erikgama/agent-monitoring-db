-- collector_domain: roles
SELECT
  CONCAT('sha256:', LEFT(SHA2(CONCAT(from_user, '@', from_host), 256), 16)) AS granted_role_id,
  CONCAT('sha256:', LEFT(SHA2(CONCAT(to_user, '@', to_host), 256), 16)) AS grantee_id,
  with_admin_option = 'Y' AS with_admin_option
FROM mysql.role_edges
ORDER BY with_admin_option DESC, granted_role_id, grantee_id;
