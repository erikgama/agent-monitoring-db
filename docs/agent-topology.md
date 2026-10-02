# Topologia dos agentes

O DBA é o único coordenador operacional. Health Check e Audit produzem
evidências; Refactor valida propostas versionadas; Notification apenas entrega;
o MCP central valida e transporta contratos.

```text
Health Check ── incident_raise ──┐
                                ├─> MCP central ──> DBA
Audit ───────── incident_raise ──┘         └──────> Notification ──> e-mail

Health Check ── query_refactor_request.v1 ──> MCP ──> Refactor
Refactor ────── query_refactor_result.v1 ────> MCP ──> DBA
                                                   └─> Notification (aviso)
```

## Limites de comunicação

- Health Check e Audit respondem ao DBA por meio do MCP.
- Refactor recebe exclusivamente o contrato versionado do Health Check pelo
  MCP e sempre devolve o resultado somente ao DBA pelo MCP.
- Notification recebe somente do MCP; não conversa diretamente com os outros
  agentes e não toma decisões de diagnóstico.
- O MCP não interpreta P99, eventos Audit ou mérito de uma refatoração; ele
  valida contratos, deduplica quando definido e chama consumidores.
