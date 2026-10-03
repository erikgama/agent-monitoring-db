# Onboarding para LLM: Health Check

> Instalação atual: `docs/SETUP.md` da raiz. Banco e LLM são definidos em
> `config/agent-monitoring.toml`; Codex, Claude Code e Kimi Code usam os mesmos
> contratos. Modelos e execuções citados no histórico são registros anteriores.

Este documento é a referência de entrada para Codex, Claude Code ou outro LLM
que precise trabalhar no Health Check. Leia também `../../../AGENTS.md`,
`../../../MEMORY.md`, `../AGENTS.md` e `../../../docs/handoffs.md` antes de
agir. Código e estado ao vivo prevalecem sobre este registro.

## Estado arquitetural atual

O Health Check usa o advisor com o LLM selecionado em
`config/agent-monitoring.toml`. O coletor não decide alertas e não chama o MCP.

```text
MySQL sakila
    ↓ coleta read-only
select_latency/results/latest.json + latest.html
    ↓ leitura direta pelo processo mysql-health-advisor
LLM configurado + advisor/rules.md
    ↓ decisão estruturada: alert | no_alert | inconclusive
se alert: coleta síncrona do relatório geral read-only
    ↓
general_report/results/report.json + report.html
    ↓ chamada direta, sem file watcher
MCP incident_raise
    └── DBA → inbox com relatório geral
              ↓ persistência confirmada
              Notification → e-mail
```

O módulo `refactor_collector/` é um fluxo separado. A cada 30 segundos ele
consulta as últimas quatro horas de `mysql.slow_log`, filtra SELECTs de
`sakila`, remove valores
literais do relatório e compara o fingerprint com o catálogo versionado. O LLM configurado
aplica `refactor_collector/rules.md`: uma query conhecida é candidata somente
com latência estritamente maior que 80 segundos. Após validação local, o
processo chama `refactor_request_raise`; o Refactor valida original e proposta
serialmente em `sakila_dev` e chama `refactor_result_raise` para o DBA.

```text
mysql.slow_log -> relatório sanitizado -> LLM configurado -> validação local
  -> MCP refactor_request_raise -> Refactor em sakila_dev
  -> MCP refactor_result_raise -> DBA + aviso Notification sem SQL literal
```

O fingerprint só entra no estado de enviados após confirmação do MCP. O MCP
também deduplica a inbox. Uma falha de entrega do resultado não repete as
consultas: o Refactor reutiliza `worker-result.json` e tenta apenas o MCP.

A interface nunca inicia esse workload por conta própria. Quando o operador
clica explicitamente em `Executar SELECTs`, o app inicia a carga de latência e,
em paralelo, executa sequencialmente a SQL original versionada
`correlated_running_total`, sem timeout artificial, até o job ser cancelado.

## Responsabilidades por componente

| Componente | Responsabilidade | Não faz |
| --- | --- | --- |
| `select_latency/collector.py` | Coleta a janela da SELECT monitorada e grava evidência | Não aplica o limite de P99 e não publica alertas |
| `advisor/rules.md` | Define em linguagem natural a regra P99 e o roteamento esperado | Não é executável e não chama processos |
| `advisor/agent.py` | Lê o HTML, chama o LLM configurado, valida a decisão, gera o relatório geral quando há alerta e publica | Não altera banco nem executa workload |
| `general_report/` | Coleta e renderiza o diagnóstico geral read-only | Não decide o alerta de latência |
| `src/alerting/mcp_publisher.py` | Inicia o MCP por stdio e chama `incident_raise` | Não decide severidade nem destinatários |
| MCP central | Revalida o contrato e roteia | Não interpreta P99 |
| Notification | Entrega no canal configurado | Não consulta banco e não decide alertas |
| DBA | Recebe e preserva a evidência encaminhada | É o único coordenador operacional |

## Decisão do advisor

- Fonte lida pelo LLM: `select_latency/results/latest.html`.
- Regra: P99 estritamente maior que `2.0` segundos.
- Provedor, modelo e esforço: seção `[llm]` de `config/agent-monitoring.toml`.
- Intervalo padrão do advisor: 15 segundos.
- P99 igual ou inferior a `2.0` segundos gera `no_alert`.
- Evidência ausente ou inconsistente gera `inconclusive`.
- O schema de saída é `advisor/analysis.schema.json`.

O LLM devolve a decisão ao processo Python `mysql-health-advisor`. O próprio
processo Python chama o MCP; nenhum processo observa
`advisor/results/latest.json` para realizar a publicação.

## Sequência de publicação

Quando o LLM decide `alert`, `advisor/agent.py` executa nesta ordem:

1. roda `collect_latest_general_report()`;
2. lê o novo `general_report/results/report.json` e `report.html`;
3. monta e valida `health_check_alert.v1`;
4. aplica o cooldown;
5. chama `McpIncidentPublisher.publish()`;
6. o cliente inicia `uv run --directory mcp mysqlconf-mcp`;
7. chama a tool MCP `incident_raise` por stdio;
8. grava a decisão e o resultado em `advisor/results/latest.json`.

O JSON do advisor é histórico latest-only, não é uma fila. O estado de cooldown
fica em `advisor/results/runtime/`. O cooldown crítico padrão é 120 segundos.

## Artefatos e fontes de verdade

- `select_latency/results/latest.html`: documento que o LLM interpreta.
- `select_latency/results/latest.json`: par estruturado da mesma coleta.
- `advisor/results/latest.json`: última decisão e resultado da publicação.
- `advisor/results/runtime/`: estado privado de cooldown.
- `refactor_collector/results/latest.html`: visualização sanitizada da coleta
  mais recente do Slow Query Log.
- `refactor_collector/results/latest.json`: par estruturado latest-only da
  mesma coleta; não contém SQL literal.
- `refactor_collector/results/runtime/refactor-state.json`: fingerprints já
  aceitos pelo MCP.
- `agents/refactor/query_refactor/advisor/results/`: jobs validados pelo MCP.
- `agents/dba/refactor-results/runtime/inbox/`: resultados do Refactor aceitos
  pelo MCP para o DBA.
- `general_report/results/report.html`: relatório geral atual para o DBA.
- `general_report/results/report.json`: par estruturado do relatório geral.
- `agents/dba/health-check-alerts/runtime/inbox/`: alertas aceitos pelo MCP.
- `contracts/health_check_alert.v1.schema.json`: contrato canônico do alerta.
- `contracts/query_refactor_request.v1.schema.json`: contrato canônico da
  solicitação ao Refactor.

Diretórios `.venv/`, `.ruff_cache/`, `__pycache__/` e `*.egg-info/` são
artefatos de ferramenta, não fontes arquiteturais.

Cada domínio do relatório geral declara `scope.kind`: `schema` significa dado
filtrado exclusivamente por `sakila`, `instance` significa métrica global da
instância e `mixed` combina os dois níveis. No domínio `locks`, esperas atuais
são de `sakila`, mas o contador de deadlocks é global. Nunca atribua conexões,
InnoDB, erros ou replicação exclusivamente ao schema.

## Validação real de 2026-09-18

- O monitor coletou uma janela real da SELECT `actor_popularity` em `sakila`.
- Luna leu o HTML e decidiu `alert` para P99 de `3.311311` segundos diante do
  limite de `2.0` segundos.
- A decisão gerou um relatório geral novo antes da chamada MCP.
- MCP respondeu `validated`, Notification respondeu `sent` e DBA respondeu
  `recorded`.
- Ciclos dentro do cooldown foram marcados como `suppressed`.
- A carga read-only concluiu baseline e calibração. A medição oficial chegou ao
  fim dos 300 segundos, mas a drenagem foi interrompida por solicitação do
  operador; por isso não existe comparação oficial final dessa execução.
- Após a interrupção, foi verificado `0` consultas adicionais ativas em
  `sakila`.
- Handoff: `../reports/2026-09-18-production-agent-validation.md`.

## Laboratório e duração

O launcher oficial é:

```sh
python3 apps/lab-console/scripts/run-health-check-lab.py --execute
```

O comando é de produção e habilita entrega real. A opção
`--measurement-seconds 300` é usada no baseline e novamente na medição oficial;
`--drain-queue` ainda espera todas as consultas enfileiradas terminarem. Assim,
o laboratório completo pode durar muito mais que cinco minutos. O launcher dá
até 120 segundos para a primeira análise do LLM configurado.

## Validação obrigatória após mudanças

```sh
uv run --extra dev python -m unittest discover -s tests -p 'test*.py' -v
uv run --extra dev ruff check src general_report tests
uv run --extra dev ruff format --check src general_report tests
```

Se a mudança atingir o app web, executar também os testes da API e
`typecheck`, `lint` e `build` do frontend. Testes reais de banco, MCP ou e-mail
exigem autorização explícita e devem registrar o resultado em um handoff.

## Limites que não podem ser removidos

- Somente `sakila`; `sakila_dev` pertence exclusivamente ao Refactor.
- SQL somente leitura e versionado.
- O LLM configurado decide; o coletor nunca avalia o threshold.
- O relatório geral só é coletado pelo advisor após uma decisão `alert`.
- Não colocar SMTP, destinatários ou lógica de entrega no Health Check.
- Não ler nem documentar credenciais.
- Não executar workload lento automaticamente.
- Não transformar `advisor/results/latest.json` em gatilho por file watcher.
