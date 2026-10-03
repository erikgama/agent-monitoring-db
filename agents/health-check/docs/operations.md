# Guia operacional completo

> Guia atual de instalação: README.md da raiz e docs/SETUP.md.
> Nos exemplos abaixo, defina `AGENT_MONITORING_ROOT="$(pwd)"` na raiz do clone.

Coletor de produção determinístico e somente leitura para sinais de saúde do
MySQL HeatWave.

## Limites de segurança

- recebe uma conexão DB-API **já autenticada**; não contém login, senha, host,
  certificados, `.env` ou leitura de arquivos de credenciais;
- executa somente blocos SQL versionados e incluídos na allowlist de `src/db.py`;
- restringe dados de schema a `sakila`; métricas globais descrevem apenas a
  instância;
- não executa DDL, DML, `SET GLOBAL`, `KILL`, `FLUSH` ou `TRUNCATE`;
- não persiste `QUERY_SAMPLE_TEXT`, texto SQL de sessões, logs brutos nem
  mensagens de erro do servidor;
- mantém somente `general_report/results/report.json` e
  `general_report/results/report.html`; o estado privado de alertas contém somente cinco
  campos de deduplicação, nunca os relatórios.

## Coleta ao vivo

No diretório `agents/health-check`:

```sh
uv run mysql-health-check collect
```

O comando usa o login-path aprovado, atualiza os três arquivos em
`general_report/results/` e imprime o identificador da coleta. Consulte
[CONEXAO.md](../CONEXAO.md)
para detalhes da autenticação.

## Demonstração completa em três terminais

O teste de latência utiliza três processos independentes. Inicie os terminais
na ordem abaixo e mantenha os dois primeiros abertos durante toda a execução da
carga. O MCP central não exige um quarto terminal: o advisor o inicia
automaticamente por `stdio` quando decide publicar um alerta.

### Terminal 1 — coletor de evidência

Inicie primeiro o monitor de SELECTs. Ele apenas coleta e atualiza o HTML:

```sh
cd "${AGENT_MONITORING_ROOT}/agents/health-check"

uv run mysql-health-latency monitor
```

Espere o monitor exibir `waiting_for_activity` ou uma coleta `idle` antes de
iniciar a carga. Para testar envio real, não altere destinatários ou SMTP no
Health Check: use o procedimento controlado da seção
“Integração MCP e entrega” deste README.

### Terminal 2 — advisor

```sh
cd "${AGENT_MONITORING_ROOT}/agents/health-check"
HEALTHCHECK_ALERTING_ENABLED=true \
HEALTHCHECK_ENVIRONMENT=local-test \
MCP_NOTIFICATION_ENABLED=true \
MCP_DBA_ENABLED=true \
NOTIFICATION_DELIVERY_ENABLED=false \
NOTIFICATION_EMAIL_RECIPIENTS_WARNING=warning-operator@example.invalid \
NOTIFICATION_EMAIL_RECIPIENTS_CRITICAL=critical-operator@example.invalid \
uv run mysql-health-advisor --interval-seconds 15
```

Espere `waiting_for_new_collection`. O LLM configurado lê o HTML, decide e, somente em
`alert`, aciona o MCP. O intervalo padrão é de 15 segundos.

### Terminal 3 — carga controlada do DBA

Execute a partir da raiz do repositório somente depois que os Terminais 1 e 2
estiverem prontos:

```sh
cd "${AGENT_MONITORING_ROOT}"

PYTHONDONTWRITEBYTECODE=1 \
python3 agents/dba/load-tests/sakila-read-only/sakila_read_demo_35.py \
  --execute \
  --confirm-target sakila \
  --confirm-demo LATENCY_35_SAKILA \
  --warmup-queries 10 \
  --measurement-seconds 300 \
  --calibration-seconds 20 \
  --baseline-tps 5 \
  --initial-loaded-tps 15 \
  --minimum-loaded-tps 10 \
  --maximum-loaded-tps 15 \
  --target-increase-percent 35 \
  --minimum-accepted-increase-percent 35 \
  --maximum-accepted-increase-percent 50 \
  --calibration-attempts 4 \
  --official-attempts 6 \
  --minimum-completed 300 \
  --progress-interval-seconds 30
```

Esse é o único processo que gera a carga deliberada. Ao terminar, interrompa
os Terminais 1 e 2 com `Ctrl-C`. Os relatórios do monitor ficam em
`select_latency/results/`; a decisão do agente fica em
`advisor/results/`. Quando o LLM decidir pelo alerta, o resultado do
Terminal 2 diferencia a aceitação pelo MCP, a entrega do Notification e o
registro na inbox do DBA.

## Monitor de latência da query `actor_popularity` em janela de 30 segundos

O coletor abaixo mantém seu relatório separado para leitura pelo advisor:

```sh
uv run mysql-health-latency monitor
```

O monitor é restrito ao digest
`97eb2e3ec6c2ecefc310ed0c449384c04c59dc149f6cb31ca75e95a9a7836fe5`,
correspondente à query `actor_popularity` observada em `sakila`. Ele tira um
snapshot dos contadores e histogramas do Performance Schema a cada 7 segundos.
Enquanto não há execução nova da query, ele permanece em
`waiting_for_activity` e não inicia uma janela artificial. A primeira execução
nova abre uma sessão de atividade. Ao completar a janela mínima de 30 segundos,
o relatório já fica disponível para o advisor; não existem mais fases de
60 ou 120 segundos. Depois disso, o monitor reutiliza os snapshots mais próximos
desse limite e substitui `select_latency/results/latest.json` e `latest.html`
a cada 7 segundos. Como 30 não é divisível por 7, o primeiro relatório ocorre
normalmente no ciclo de aproximadamente 35 segundos e usa os snapshots que
cobrem 28 segundos, o recorte amostrado mais próximo de 30.
`window_elapsed_seconds` e `window_alignment_error_seconds` tornam essa
diferença explícita.

Se não houver nenhuma execução nova durante 20 segundos, a sessão de atividade
é encerrada com o evento `activity_session_reset`. A consulta ao Performance
Schema continua a cada 7 segundos, mas uma nova janela só começa quando o
contador da query voltar a aumentar. Portanto, intervalos ociosos não entram no
cálculo como latência zero; após a retomada, uma nova janela mínima de 30
segundos é formada. Como a confirmação de atividade também acontece nos snapshots, o
reset é percebido no primeiro ciclo de 7 segundos após o limite ser alcançado.
Nesse momento, `latest.json` e `latest.html` são substituídos por um relatório
com `status=idle`, zero digests e sem média, P95 ou P99 anteriores. O HTML passa
a mostrar `Sem atividade`. O mesmo saneamento ocorre quando o monitor é iniciado
e não observa nenhuma execução durante o período de inatividade.

Para cada execução concluída da query-alvo, o monitor calcula o delta de
execuções e de tempo total em cada intervalo de 7 segundos. A média móvel de 30
segundos é ponderada por execução: soma do tempo dos intervalos disponíveis
dividida pela soma das execuções dos mesmos intervalos. Ela nunca é calculada
como média simples das médias parciais. O JSON inclui `interval_samples`,
permitindo conferir todos os deltas que formaram o resultado.

P95 e P99 são recalculados pela soma dos buckets dos intervalos, mas o
Performance Schema não fornece as durações individuais. Por isso os campos são
nomeados `p95_latency_upper_bound_seconds` e
`p99_latency_upper_bound_seconds`: são estimativas do limite superior do bucket,
servem como contexto. O relatório declara
`primary_latency_metric=avg_latency_seconds` e registra
`window_target_seconds=30`, `refresh_interval_seconds=7`, horários efetivos e
cobertura das estimativas.

A regra de decisão fica em `advisor/rules.md`. O coletor não lê essa regra, não
compara o P99 com threshold e não chama o MCP. Ele apenas substitui
`select_latency/results/latest.json` e `latest.html` com evidências da janela.

O LLM configurado lê o HTML completo. P99 estritamente maior que `2.0` segundos
gera a decisão `alert`; exatamente `2.0` segundos ou menos gera `no_alert`.
Relatório sem P99 válido ou inconsistente gera `inconclusive`. O schema em
`advisor/analysis.schema.json` obriga o LLM a devolver a decisão, o valor
observado, o threshold de `2.0` segundos e a qualidade da evidência.

Somente uma decisão `alert` do LLM forma um finding. Nesse momento, o advisor
executa a coleta geral somente leitura e gera `health_check_alert.v1` com
`general_report/results/report.json` e `report.html` para o DBA. O audit ID da
coleta de latência fica registrado como origem da decisão. Depois, o agente usa
`McpIncidentPublisher`. O MCP só é iniciado pelo processo do agente quando
`HEALTHCHECK_ALERTING_ENABLED=true`; sem essa variável, a decisão fica local.

Os nomes dos limites de percentil são deliberadamente
`p95_latency_upper_bound_seconds` e `p99_latency_upper_bound_seconds`, pois os
valores do Performance Schema são limites superiores estimados dos buckets. Se
os valores fornecidos vierem do relatório client-side do workload, eles não são
diretamente equivalentes a esses dois campos server-side.

O digest literal `SELECT ?`, usado como validação ou marcador de protocolo, é
excluído no SQL e novamente na normalização defensiva. Ele não representa uma
consulta a tabelas do `sakila` e não participa da contagem de digests exibidos.

O relatório acompanha somente a query-alvo pelo digest normalizado. Ele não
guarda SQL literal nem parâmetros reais. O máximo da janela não é inferido.
As estimativas P95 e P99 vêm dos buckets do histograma, não da subtração direta
dos valores de percentil acumulados. Um reset detectável em qualquer intervalo
de 7 segundos exclui o digest da janela; uma expulsão e recriação do mesmo
digest pode não ser distinguível e permanece como limitação explícita.

O diretório pode ser alterado com `HEALTHCHECK_SELECT_LATENCY_DIR`. O monitor
roda até receber `Ctrl-C`. Para uma validação controlada que encerra depois do
primeiro relatório publicado:

```sh
uv run mysql-health-latency monitor --max-reports 1
```

O comando `collect` continua disponível para uma coleta única com uma janela de
30 segundos:

```sh
uv run mysql-health-latency collect
```

A disponibilidade de P95, P99 e do histograma necessário para percentis da
própria janela pode ser validada, sem executar workload, com:

```sh
uv run mysql-health-latency capabilities
```

## Integração Python

O chamador cria a conexão autorizada e a entrega ao coletor:

```python
from general_report.main import build_health_snapshot, read_latest_snapshot

report = build_health_snapshot(existing_connection)
cached = read_latest_snapshot()
```

`build_health_snapshot()` assume a propriedade da conexão e sempre tenta
fechá-la em `finally`. Consultas `SELECT` recebem o hint
`MAX_EXECUTION_TIME`; o timeout de transporte deve ser configurado pela camada
que cria a conexão.

`read_latest_snapshot()` lê e valida apenas o cache local. Ele não recebe
conexão e não possui caminho de código que consulte o MySQL.

## Advisor com LLM configurado

O processo contínuo abaixo verifica se existe um novo HTML de latência e, quando
o `audit_id` muda, pede ao provedor e modelo selecionados em
`config/agent-monitoring.toml` uma decisão estruturada:

```sh
uv run mysql-health-advisor
```

O adapter central inicia o cliente autenticado para o provedor configurado.
O LLM recebe o HTML completo e `advisor/rules.md`; ele é a única camada que
decide o alerta. Quando decide `alert`, o processo do advisor valida o
contrato e chama `incident_raise` no MCP. O MCP registra JSON e HTML completos
para o DBA e somente depois solicita a entrega por e-mail ao Notification. A última decisão fica
em `advisor/results/latest.json`. `Ctrl-C` encerra o processo.

O intervalo padrão do advisor é 15 segundos, igual ao usado pelo laboratório do
app. Uma substituição explícita continua disponível para diagnóstico, sem
alterar o polling de 7 segundos do coletor:

```sh
uv run mysql-health-advisor --interval-seconds 15
```

Variáveis locais, sem segredo:

- `HEALTHCHECK_CACHE_DIR` (padrão: `./general_report/results` dentro deste módulo)
- `HEALTHCHECK_TOP_N` (padrão: política; intervalo 1–100)
- `HEALTHCHECK_QUERY_TIMEOUT_SECONDS` (padrão: política; intervalo 1–120)

`mysql-health-check collect` e `mysql-health-latency` nunca iniciam o MCP nem
publicam alertas. A variável `HEALTHCHECK_ALERTING_ENABLED=true` pertence ao
processo `mysql-health-advisor`; `HEALTHCHECK_ENVIRONMENT` deve ser um
identificador lógico seguro, nunca host ou endpoint.

## Fluxo de alertas

```text
coleta MySQL
  -> JSON e HTML do mesmo audit_id
  -> LLM configurado lê o HTML e advisor/rules.md
  -> decisão do agente e finding explicável
  -> health_check_alert.v1 validado localmente
  -> MCP stdio incident_raise
       -> DBA -> inbox técnica privada
            -> notification -> e-mail
```

O agente usa o threshold explícito em `advisor/rules.md`; ele não inventa
limites ausentes. O health check geral continua separado e usa
`general_report/rules/report-rules.json` somente para classificar seu próprio
relatório.

O estado privado padrão fica em `advisor/results/runtime/alert-state.json` e guarda apenas
`dedupe_key`, `last_severity`, `last_sent_at`, `last_audit_id` e
`occurrence_count`. Condições novas são publicadas; repetições são suprimidas
por 15 minutos em `warning` e 2 minutos em `critical`; escalonamentos para
`critical` são imediatos. Uma coleta sem a condição remove o estado local e não
envia aviso de resolução. Os cooldowns, que não são thresholds de saúde, podem
ser ajustados com:

- `HEALTHCHECK_ALERT_WARNING_COOLDOWN_SECONDS`;
- `HEALTHCHECK_ALERT_CRITICAL_COOLDOWN_SECONDS`.

`published_to_mcp=true` significa que o MCP aceitou o contrato. Isso é separado
de `delivery_status`, que informa o resultado do agente `notification`. Assim,
`accepted=true` com `delivery_status=dry_run` confirma o fluxo técnico, mas
também confirma que nenhum e-mail foi entregue.

O cliente stdio repassa ao subprocesso MCP somente as variáveis necessárias às
fronteiras de roteamento e o ambiente básico seguro do SDK. Variáveis alheias,
inclusive credenciais do banco, não atravessam essa fronteira. Cada evento do
agente expõe `publication_status`, `published_count`, `mcp_status`,
`delivery_status`, `email_delivered` e `dba_status`; o JSON e o HTML completos
não são impressos.

Para uma execução local integrada com entrega real já provisionada pelo
operador, o launcher Python carrega diretamente a configuração não secreta de
`../notification/.notification.local.env`. O segredo permanece no Chaves do
macOS e é resolvido somente pelo runtime do Notification. O fluxo oficial é:

```sh
python3 apps/lab-console/scripts/run-health-check-lab.py --execute
```

Sem `MCP_NOTIFICATION_ENABLED=true`, a rota de e-mail retorna
`not_configured`, desde que o DBA já tenha confirmado a persistência. Sem
`MCP_DBA_ENABLED=true`, a rota do DBA retorna `not_configured`, o incidente não
é aceito e Notification retorna `not_attempted`. Sem a habilitação explícita de
entrega, o Notification permanece em `dry_run`.

O Health Check não escolhe destinatários, não implementa SMTP, não envia e-mail
e não grava diretamente no DBA. O MCP entrega o mesmo alerta validado à inbox
privada em `agents/dba/health-check-alerts/runtime/inbox/`. Isso não abre
tarefas nem executa correções no MySQL. A integração é local por stdio, sem HTTP
e sem servidor MCP neste agente.

## Testes

```sh
uv run python -m unittest discover -s tests -v
uv run ruff check advisor general_report select_latency src/alerting \
  tests/test_advisor.py tests/test_alert_publishing.py tests/test_mcp_integration.py \
  tests/test_mcp_publisher.py tests/test_select_latency.py \
  tests/test_alert_contract.py
uv run ruff format --check advisor general_report src/alerting \
  select_latency \
  tests/test_advisor.py tests/test_alert_publishing.py tests/test_mcp_integration.py \
  tests/test_mcp_publisher.py tests/test_select_latency.py \
  tests/test_alert_contract.py
```

Os testes usam conexões falsas; não acessam o banco.

O teste de integração real inicia o MCP pelo comando oficial e força
`MCP_NOTIFICATION_ENABLED=true` com `NOTIFICATION_DELIVERY_ENABLED=false`
somente no subprocesso do teste. Os endereços `.invalid` usados ali são dados
fictícios exigidos pela validação do agente `notification`, não configuração de
produção:

```sh
HEALTHCHECK_RUN_MCP_INTEGRATION=1 \
  uv run python -m unittest tests.test_mcp_integration -v
```

## Arquivos de saída

- `general_report/results/report.json`: fonte de verdade completa da coleta mais recente;
- `general_report/results/report.html`: visualização estática derivada do mesmo objeto
  validado;
- `logs/collector.log`: eventos operacionais mínimos, sem SQL ou erros brutos.

Os dois arquivos do relatório são preparados, sincronizados e substituídos por
rename atômico. Se a publicação falhar, o código tenta restaurar o conjunto
anterior.

## Contrato e publicação

O contrato de entrega está em
[`alert-contract.md`](../alerts/alert-contract.md), com JSON Schema,
validações de integridade e um exemplo do alerta do advisor. O publicador
`McpIncidentPublisher` valida novamente o envelope, resolve com segurança a
raiz do repositório, descobre `incident_raise` e envia `{ "alert": payload }`
como objeto pelo SDK MCP. JSON e HTML completos nunca são impressos em logs.
