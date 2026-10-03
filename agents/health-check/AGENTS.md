# Health Check: instruções operacionais

- Leia este `AGENTS.md` antes de executar qualquer tarefa.
- Leia obrigatoriamente `docs/LLM_ONBOARDING.md` antes de alterar arquitetura,
  regras, coleta, publicação, laboratório ou artefatos do Health Check.
- Use como contexto somente esta pasta, `../../AGENTS.md`, `../../MEMORY.md`,
  `../../docs/handoffs.md` e caminhos exatos fornecidos pelo DBA.
- Não consulte memórias globais, outros projetos ou pastas de agentes irmãos.
- Entregue toda resposta diretamente e exclusivamente ao DBA.

## Missão

Coletar e interpretar sinais read-only de saúde e performance do MySQL
HeatWave e devolver evidência atual ao DBA, sem realizar mudanças no banco.

## Implementação oficial

- `general_report/`: coletor, avaliação, SQL, regras, cache, renderização e resultados do relatório geral.
- `general_report/rules/report-rules.json`: regras de coleta, retenção, limites e avaliação do relatório geral.
- `src/`: bibliotecas compartilhadas de coleta, conexão e publicação de alertas.
- `select_latency/sql/`: blocos SQL read-only do monitor SELECT, versionados e protegidos por allowlist.
- `advisor/rules.md`: regra operacional lida pelo LLM configurado para decidir sobre o HTML de latência.
- `pyproject.toml`: entrypoints Python para coleta, monitor e advisor.
- `advisor/`: agente executável, regra completa, schema e resultados das decisões do LLM configurado.
- `refactor_collector/`: coleta sanitizada e latest-only de SELECTs lentas de
  `mysql.slow_log`, restrita ao schema `sakila`; o LLM configurado aplica a regra de
  80 segundos e publica no MCP somente candidatos conhecidos e versionados.
- `policy.json`: escopo e timeout compartilhados das coletas.
- `tests/`: validação simulada, segurança SQL e publicação atômica.
- `general_report/results/report.json`: fonte de verdade da coleta mais recente.
- `general_report/results/report.html`: visualização estática do mesmo snapshot.
- `select_latency/results/`: JSON, HTML e estado privado do monitor SELECT.
- `advisor/results/`: decisão mais recente do advisor e estado privado de cooldown.
- `logs/collector.log`: log operacional sanitizado.
- `alerts/`: documentação e exemplo do alerta P99.
- `contracts/`: schemas canônicos `health_check_alert.v1` e
  `query_refactor_request.v1`.
- `src/alert_contract.py`: validação local do contrato.
- `src/alerting/`: cooldown, estado mínimo e cliente stdio usado pelo advisor para
  chamar a tool MCP `incident_raise` quando ele decidir pelo alerta P99.
- Após uma decisão `alert`, o processo do advisor executa a coleta geral read-only existente e
  envia ao MCP o JSON e o HTML atualizados do relatório geral para o DBA; o
  audit ID da latência permanece como origem da decisão.
- `README.md` e `CONEXAO.md`: operação e autenticação.

Não existe implementação paralela ou diretório de fase. O comando oficial é:

```sh
uv run mysql-health-check collect
```

## Escopo e segurança

- Limitar dados por schema a `sakila`; métricas globais descrevem apenas a
  instância.
- Executar somente os blocos existentes nos diretórios `sql/` de cada fluxo, validados pela allowlist de
  `src/db.py`.
- Não executar DDL, DML, configuração, Audit ou workload deliberadamente lento.
- Não acessar, copiar ou exibir credenciais. A autenticação usa apenas a
  referência descrita em `CONEXAO.md`, com TLS obrigatório.
- Não persistir SQL literal de sessões, `QUERY_SAMPLE_TEXT`, erros brutos ou
  segredos.
- Cada fluxo substitui somente seu próprio cache latest-only em `general_report/results/`,
  `select_latency/results/`, `advisor/results/` ou
  `refactor_collector/results/`.
- A publicação opcional alcança somente o MCP central por stdio. O Health Check
  não implementa e-mail, destinatários, fila, integração DBA ou ações
  corretivas.
- `refactor_collector/advisor.py` roda por padrão a cada 30 segundos. Ele não
  escreve uma refatoração: somente chama `refactor_request_raise` após validar
  query conhecida com latência estritamente maior que 80 segundos. O estado
  persistente e a inbox MCP impedem reenvio do mesmo fingerprint.

## Interpretação

- Contadores de Performance Schema, erros, deadlocks e índices sem uso são
  acumulados desde reset/restart; isoladamente não representam janela diária.
- Distinguir digests observados de queries efetivamente executadas em workload
  controlado.
- Antes de chamar um digest de “mais lento”, considerar duração, execuções,
  `FIRST_SEEN` e `LAST_SEEN`.
- Não recomendar nem executar mudanças; devolver evidência e limitações ao DBA.

## Top 3 sob demanda

1. Fazer uma coleta nova no momento do pedido.
2. Retornar as três primeiras entradas de `domains.workload.top_digests`, já
   ordenadas por latência acumulada e restritas a `sakila`.
3. Informar timestamp, execuções, soma, média, p95, `FIRST_SEEN` e `LAST_SEEN`.
4. Declarar a natureza acumulada dos contadores e qualquer lacuna observada.

## Validação

Executar a suíte com:

```sh
python3 -m unittest discover -s tests -v
```
