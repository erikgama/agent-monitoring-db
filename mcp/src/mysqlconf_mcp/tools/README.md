# Tools do MCP Central

O MCP Central expõe três tools pelo transporte STDIO. Elas validam contratos e
roteiam mensagens; nenhuma executa SQL nem aplica alterações no banco.

| Tool | Publicador | Destino | Contrato de entrada |
| --- | --- | --- | --- |
| `incident_raise` | Health Check ou Audit | DBA, depois Notification | `health_check_alert.v1` ou `audit_security_alert.v1` |
| `refactor_request_raise` | Health Check | Refactor | `query_refactor_request.v1` |
| `refactor_result_raise` | Refactor | DBA e Notification | `query_refactor_result.v1` |

## Implementação

- `incidents.py`: implementa `incident_raise`.
- `refactor_workflow.py`: implementa `refactor_request_raise` e
  `refactor_result_raise`.
- `../server.py`: registra as três tools no FastMCP e inicia o transporte
  STDIO.

Os schemas aceitos ficam em `../contracts/`.
