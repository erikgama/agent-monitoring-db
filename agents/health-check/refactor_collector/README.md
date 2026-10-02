# Refactor Collector: etapa 1

Coletor somente leitura para observar SELECTs lentas registradas em
`mysql.slow_log`. O escopo é exclusivamente o schema `sakila`.

## O que esta etapa faz

1. Lê o estado atual do Slow Query Log sem alterar configuração.
2. Consulta as entradas das últimas quatro horas de `mysql.slow_log` para
   `sakila`.
3. Mantém somente statements `SELECT` ou `WITH`.
4. Remove comentários e valores literais antes de persistir a consulta.
5. Publica atomicamente `results/latest.json` e `results/latest.html`.

O coletor não decide se uma query deve ser refatorada e não executa consultas
capturadas. O processo separado `advisor.py` avalia a evidência coletada e,
somente para uma query conhecida e versionada estritamente acima de 80
segundos, publica `query_refactor_request.v1` no MCP.

## Execução

```sh
uv run mysql-health-refactor-collector collect
```

Leitura do snapshot mais recente, sem acessar o banco:

```sh
uv run mysql-health-refactor-collector read-latest
```

## Segurança

- Usa o mesmo login-path aprovado e TLS obrigatório do Health Check.
- O SQL executável está em `sql/` e faz parte da allowlist de `src/db.py`.
- Não habilita Slow Query Log, não altera `long_query_time` e não muda
  `log_output`.
- Não persiste SQL literal, valores sensíveis, erros brutos ou logs brutos.
- A política está em `rules/collector-policy.json`.

## Etapa seguinte ativa

O advisor aplica `rules/advisor.md`, valida sua saída localmente e chama
`refactor_request_raise`. O MCP deduplica a solicitação e a registra para o
worker ativo do Refactor. O resultado retorna exclusivamente ao DBA pelo MCP.
