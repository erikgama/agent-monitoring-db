-- collector_domain: accounts
-- Authentication material is never selected.
SELECT
  CONCAT('sha256:', LEFT(SHA2(CONCAT(user, '@', host), 256), 16)) AS account_id,
  CONCAT('sha256:', LEFT(SHA2(user, 256), 16)) AS actor_id,
  user = '' AS anonymous_user,
  host IN ('%', '') OR INSTR(host, '%') > 0 AS wildcard_or_broad_host,
  host NOT IN ('localhost', '127.0.0.1', '::1') AS remote_host_scope,
  plugin AS authentication_plugin,
  authentication_string <> '' AS credential_material_present,
  password_lifetime AS password_lifetime_days,
  password_expired = 'Y' AS password_expired,
  account_locked = 'Y' AS account_locked,
  ssl_type AS tls_requirement,
  max_user_connections
FROM mysql.user
ORDER BY anonymous_user DESC, wildcard_or_broad_host DESC, account_id;
