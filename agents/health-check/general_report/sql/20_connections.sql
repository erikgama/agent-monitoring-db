-- name: top_users
SELECT user, total_connections, current_connections,
       statements, statement_latency
FROM sys.user_summary
ORDER BY current_connections DESC, total_connections DESC
LIMIT {{connections_limit}};

-- name: top_hosts
SELECT host, total_connections, current_connections,
       statements, statement_latency
FROM sys.host_summary
ORDER BY current_connections DESC, total_connections DESC
LIMIT {{connections_limit}};
