# Inbox DBA para Audit Security

Consumidor local e privado dos alertas `audit_security_alert.v1` que o MCP
central já validou. Ele grava somente dois resumos sanitizados e imutáveis por
evento:

```text
runtime/inbox/<detected-at>__<category>__<alert-id>/
├── alert-summary.json
└── audit-event-summary.json
```

O primeiro arquivo guarda identidade, classificação, texto explicativo e
referência do contrato. O segundo contém apenas a regra, comando normalizado,
resultado, código MySQL, schema, evidência de escopo e `event_key`. Não são
persistidos SQL literal, identidades, credenciais, host/IP, JSON/HTML completos
da coleta ou configuração de comunicação.

O `event_key` SHA-256 é a chave de idempotência. Reentregar o mesmo evento,
mesmo com outro `alert_id`, retorna `duplicate` e não cria outra pasta.

A inbox aceita os contextos `destructive_ddl` e `schema_change` do Audit. Para
`schema_change`, o resumo registra apenas a regra, a métrica
`blocked_schema_change_attempt`, o comando normalizado `alter_table`, resultado,
código MySQL, schema e `event_key`.

Esta inbox não executa SQL, análise autônoma ou correção. O recebimento é
somente evidência para posterior decisão do DBA.

## Testes

```sh
cd agents/dba/audit-security-alerts
uv run python -m unittest discover -s tests -v
uv run ruff check .
uv run ruff format --check .
```
