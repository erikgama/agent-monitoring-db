-- name: replica_status
SHOW REPLICA STATUS;

-- name: connection_status
SELECT
  CHANNEL_NAME,
  SERVICE_STATE,
  LAST_ERROR_NUMBER,
  COUNT_RECEIVED_HEARTBEATS
FROM performance_schema.replication_connection_status;

-- name: applier_status
SELECT
  CHANNEL_NAME,
  SERVICE_STATE
FROM performance_schema.replication_applier_status;

-- name: group_members
SELECT MEMBER_ID, MEMBER_HOST, MEMBER_PORT,
       MEMBER_STATE, MEMBER_ROLE, MEMBER_VERSION
FROM performance_schema.replication_group_members;
