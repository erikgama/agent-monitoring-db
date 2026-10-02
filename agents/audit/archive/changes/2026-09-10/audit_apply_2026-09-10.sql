-- MySQL Enterprise Audit (plugin legado) — piloto sakila
-- Somente filtro/atribuição; não altera usuários, grants, startup ou logs.

SET @sakila_security_monitoring = '{
  "filter": {
    "log": false,
    "class": [
      {
        "name": "connection",
        "event": [
          { "name": "connect", "log": true },
          { "name": "disconnect", "log": true }
        ]
      },
      {
        "name": "table_access",
        "event": [
          {
            "name": "insert",
            "log": { "field": { "name": "table_database.str", "value": "sakila" } }
          },
          {
            "name": "update",
            "log": { "field": { "name": "table_database.str", "value": "sakila" } }
          },
          {
            "name": "delete",
            "log": { "field": { "name": "table_database.str", "value": "sakila" } }
          },
          {
            "name": "read",
            "log": { "field": { "name": "table_database.str", "value": "airportdb" } }
          }
        ]
      },
      {
        "name": "general",
        "event": {
          "name": "status",
          "log": {
            "and": [
              {
                "or": [
                  { "field": { "name": "general_command.str", "value": "Query" } },
                  { "field": { "name": "general_command.str", "value": "Execute" } }
                ]
              },
              {
                "or": [
                  { "field": { "name": "general_sql_command.str", "value": "alter_db" } },
                  { "field": { "name": "general_sql_command.str", "value": "alter_event" } },
                  { "field": { "name": "general_sql_command.str", "value": "alter_function" } },
                  { "field": { "name": "general_sql_command.str", "value": "alter_instance" } },
                  { "field": { "name": "general_sql_command.str", "value": "alter_library" } },
                  { "field": { "name": "general_sql_command.str", "value": "alter_procedure" } },
                  { "field": { "name": "general_sql_command.str", "value": "alter_resource_group" } },
                  { "field": { "name": "general_sql_command.str", "value": "alter_server" } },
                  { "field": { "name": "general_sql_command.str", "value": "alter_table" } },
                  { "field": { "name": "general_sql_command.str", "value": "alter_tablespace" } },
                  { "field": { "name": "general_sql_command.str", "value": "alter_user" } },
                  { "field": { "name": "general_sql_command.str", "value": "alter_user_default_role" } },
                  { "field": { "name": "general_sql_command.str", "value": "create_db" } },
                  { "field": { "name": "general_sql_command.str", "value": "create_event" } },
                  { "field": { "name": "general_sql_command.str", "value": "create_function" } },
                  { "field": { "name": "general_sql_command.str", "value": "create_index" } },
                  { "field": { "name": "general_sql_command.str", "value": "create_library" } },
                  { "field": { "name": "general_sql_command.str", "value": "create_masking_policy" } },
                  { "field": { "name": "general_sql_command.str", "value": "create_procedure" } },
                  { "field": { "name": "general_sql_command.str", "value": "create_resource_group" } },
                  { "field": { "name": "general_sql_command.str", "value": "create_role" } },
                  { "field": { "name": "general_sql_command.str", "value": "create_server" } },
                  { "field": { "name": "general_sql_command.str", "value": "create_spatial_reference_system" } },
                  { "field": { "name": "general_sql_command.str", "value": "create_table" } },
                  { "field": { "name": "general_sql_command.str", "value": "create_trigger" } },
                  { "field": { "name": "general_sql_command.str", "value": "create_udf" } },
                  { "field": { "name": "general_sql_command.str", "value": "create_user" } },
                  { "field": { "name": "general_sql_command.str", "value": "create_view" } },
                  { "field": { "name": "general_sql_command.str", "value": "drop_db" } },
                  { "field": { "name": "general_sql_command.str", "value": "drop_event" } },
                  { "field": { "name": "general_sql_command.str", "value": "drop_function" } },
                  { "field": { "name": "general_sql_command.str", "value": "drop_index" } },
                  { "field": { "name": "general_sql_command.str", "value": "drop_library" } },
                  { "field": { "name": "general_sql_command.str", "value": "drop_masking_policy" } },
                  { "field": { "name": "general_sql_command.str", "value": "drop_procedure" } },
                  { "field": { "name": "general_sql_command.str", "value": "drop_resource_group" } },
                  { "field": { "name": "general_sql_command.str", "value": "drop_role" } },
                  { "field": { "name": "general_sql_command.str", "value": "drop_server" } },
                  { "field": { "name": "general_sql_command.str", "value": "drop_spatial_reference_system" } },
                  { "field": { "name": "general_sql_command.str", "value": "drop_table" } },
                  { "field": { "name": "general_sql_command.str", "value": "drop_trigger" } },
                  { "field": { "name": "general_sql_command.str", "value": "drop_user" } },
                  { "field": { "name": "general_sql_command.str", "value": "drop_view" } },
                  { "field": { "name": "general_sql_command.str", "value": "grant" } },
                  { "field": { "name": "general_sql_command.str", "value": "grant_roles" } },
                  { "field": { "name": "general_sql_command.str", "value": "revoke" } },
                  { "field": { "name": "general_sql_command.str", "value": "revoke_all" } },
                  { "field": { "name": "general_sql_command.str", "value": "revoke_roles" } },
                  { "field": { "name": "general_sql_command.str", "value": "truncate" } }
                ]
              }
            ]
          }
        }
      }
    ]
  }
}';

SELECT audit_log_filter_set_filter('sakila_security_monitoring', @sakila_security_monitoring);
SELECT audit_log_filter_set_user('%', 'sakila_security_monitoring');
SELECT audit_log_filter_flush();
