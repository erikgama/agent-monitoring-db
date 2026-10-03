# Project Agent Memory

## Purpose

This file is the project-local operating memory for the Maestri agents. It is
the source of project context that agents should read before starting work.
It does not replace `AGENTS.md`, repository documentation, source code, or
the live database as authoritative sources.

## Configuração vigente de LLM

O provedor dos advisors e do chat é selecionado em
`config/agent-monitoring.toml` (`codex`, `claude` ou `kimi`). Referências a Luna,
Sol e modelos específicos nas seções históricas abaixo descrevem execuções
anteriores; consulte a configuração local e o código antes de operar.

## Team topology

- `DBA` is the coordinator and final consumer for query-analysis work.
- O fluxo de refatoração é exclusivamente automático e versionado: Health
  Check -> MCP -> Refactor -> MCP -> DBA para queries conhecidas do Slow Query
  Log estritamente acima de 80 segundos.
- O Health Check fornece ao MCP a SQL original conhecida, evidência e contrato;
  não existe solicitação manual do DBA ao Refactor.
- `notification` is outside the DBA coordination path: it receives only a
  complete and validated `health_check_alert.v1` or
  `audit_security_alert.v1`, or a completion notice derived from a validated
  `query_refactor_result.v1`, from the central MCP and delivers only to
  operator-configured channels. It does not communicate directly with Health
  Check, Audit, Refactor or DBA.

## Query refactoring contract

- Preserve the original query verbatim; never overwrite it.
- Preserve the result contract without exception in the automated flow:
  returned columns, aliases, order, types, semantics, NULL handling,
  duplicates, filters, joins, aggregates, ordering, and pagination.
- Do not invent schema, indexes, statistics, parameters, cardinality, or
  business rules. Reject an incomplete contract instead of requesting a manual
  complement.
- Do not propose or execute index changes, optimizer hints, session changes or
  global configuration in this flow.

## Validation and safety

- The automatic flow does not execute `EXPLAIN ANALYZE` or require a manual
  plan-analysis stage.
- Never execute DDL or DML to validate a refactor without explicit approval.
- Validate both the original and refactored query in the same session context
  and, where possible, against a consistent snapshot.
- Confirm result equivalence before claiming success: result-set shape, row
  count, and values. State any validation limits clearly.

## Contrato automático de refatoração

O `query_refactor_request.v1` produzido pelo Health Check fornece:

1. identidade, origem, schema `sakila` e chave de deduplicação;
2. SQL original conhecida, seu SHA-256 e fingerprint;
3. limite, maior duração, ocorrências, linhas examinadas e enviadas;
4. identidade, horário, JSON e HTML do snapshot coletado.

O Refactor devolve `query_refactor_result.v1` ao MCP com SQL original,
proposta, prova de preservação do contrato, evidência de validação e limitações.
O MCP registra o resultado no DBA e pode solicitar ao Notification um aviso de
conclusão sem SQL literal.

## Reference documentation

- MySQL SELECT optimization: https://dev.mysql.com/doc/refman/8.4/en/select-optimization.html
- MySQL execution plans and EXPLAIN: https://dev.mysql.com/doc/refman/8.4/en/execution-plan-information.html
- MySQL optimization and indexes: https://dev.mysql.com/doc/refman/8.4/en/optimization-indexes.html
- MySQL optimizer hints: https://dev.mysql.com/doc/refman/8.4/en/optimizer-hints.html

## Estrutura operacional do projeto

- Os cinco agentes permanentes são `agents/dba`, `agents/audit`,
  `agents/health-check`, `agents/refactor` e `agents/notification`.
- O DBA coordena e revisa os resultados. Audit e Health Check produzem
  evidências para o DBA; o Refactor aceita exclusivamente
  `query_refactor_request.v1` do Health Check pelo MCP e sempre devolve o
  resultado ao DBA pelo MCP.
- O módulo Health Check oficial está integralmente em
  `agents/health-check/`; resultados novos pertencem somente ao diretório
  `results/` do fluxo que os produziu.
- O Health Check e dono dos contratos em `agents/health-check/contracts/`,
  incluindo `health_check_alert.v1` e `query_refactor_request.v1`. Notification apenas revalida
  e entrega por e-mail; o fluxo previsto e Health Check -> MCP central ->
  notification -> e-mail.
- O Audit e dono de `audit_security_alert.v1` em
  `agents/audit/contracts/audit_security_alert.v1.schema.json`; o fluxo e
  Audit Luna -> MCP central -> Notification + inbox do DBA.
- Evidências e scripts históricos de Audit ficam em `agents/audit/archive/` e
  nunca são executados automaticamente.
- Consultas históricas de laboratório com esperas deliberadas ficam em
  `agents/refactor/archive/inputs/labs/` e não são benchmark automático.
- Handoffs seguem `docs/handoffs.md`. Não existe pasta `shared/`; cada item
  comum é referenciado por seu caminho real.
- Arquivos de credencial, perfis de login e chaves privadas permanecem fora das
  pastas dos agentes e nunca devem ser lidos, copiados ou incluídos em handoffs.

## Notification: autenticacao SMTP local

- Em 2026-09-15, a autenticacao local validada usa Gmail SMTP em
  `smtp.gmail.com:587`, STARTTLS, validacao de certificado e hostname e
  remetente identico ao usuario autenticado.
- A senha de app permanece em um item de senha generica do Chaves do macOS,
  referenciado pelo servico `mysqlconf-notification-smtp` e pela conta igual a
  `SMTP_USERNAME`. O segredo e injetado somente no ambiente do processo de
  envio e nunca e documentado ou versionado.
- A configuracao nao secreta e os destinatarios ficam somente em
  `agents/notification/.notification.local.env`, arquivo local ignorado pelo
  Git e com entrega desabilitada por padrao.
- Um envio real exige simultaneamente `NOTIFICATION_DELIVERY_ENABLED=true` e
  `--send`; cada dispatch realiza no maximo uma tentativa.
- Procedimento, controles, evidencia e limitacoes estao documentados em
  `agents/notification/reports/2026-09-15-smtp-authentication.md`.

## Health Check e Audit: arquitetura orientada por Luna

- Os guias obrigatorios para novos LLMs sao
  `agents/health-check/docs/LLM_ONBOARDING.md` e
  `agents/audit/docs/LLM_ONBOARDING.md`.
- Nos dois agentes, o coletor somente coleta e grava HTML/JSON. Ele nao decide
  alertas e nao chama o MCP.
- O fluxo separado `refactor_collector` consulta somente `mysql.slow_log` para
  `sakila` a cada 30 segundos, usando uma janela de quatro horas. Luna aplica a
  regra versionada de latência
  estritamente maior que 80 segundos e o processo local valida a decisão antes
  de chamar `refactor_request_raise`. O fingerprint só é marcado após aceite
  do MCP, evitando pedidos repetidos.
- Quando o Refactor conclui, `refactor_result_raise` primeiro registra o
  resultado validado na inbox do DBA. Somente um registro novo pode solicitar
  ao Notification uma tentativa de aviso; duplicatas nao reenviam e-mail. O
  aviso nao inclui SQL literal nem autoriza producao.
- No app integrado, `Executar SELECTs` inicia explicitamente a carga de
  latência e a query conhecida `correlated_running_total` em paralelo. A query
  lenta roda sequencialmente sem timeout artificial até o operador cancelar o
  job; não é iniciada pelo monitor nem pelo coletor.
- Os processos Python `mysql-health-advisor` e `mysql-audit-advisor` leem o
  HTML, chamam `gpt-5.6-luna` com effort `low` e, apos decisao estruturada e
  validacao local, chamam diretamente `incident_raise` no MCP por stdio. Nao
  existe file watcher sobre os arquivos de decisao.
- No Health Check, P99 estritamente maior que 2.0 segundos gera alerta. Antes
  da publicacao, o advisor executa uma coleta geral read-only nova e anexa
  `general_report/results/report.json` e `report.html` ao alerta para o DBA.
- No Audit, Luna decide apenas sobre DROP/TRUNCATE e ALTER TABLE bloqueados em
  `sakila`. O validador confirma que a evidencia selecionada existe exatamente
  no JSON pareado, sem substituir a decisao semantica do agente.
- Em 2026-09-18, os dois fluxos foram validados em producao. Health Check
  observou P99 de 3.311311 segundos, gerou o relatorio geral e recebeu MCP
  `validated`, Notification `sent` e DBA `recorded`. Audit validou DROP e ALTER
  negados com erro 1142 e recebeu os mesmos resultados de roteamento.
- A carga Health Check concluiu baseline e calibracao. A medicao oficial chegou
  aos 300 segundos, mas a drenagem foi interrompida por solicitacao do
  operador; nenhuma comparacao oficial final foi produzida e depois foram
  confirmadas zero consultas adicionais ativas em `sakila`.

## Revisão final de 24/09/2026

- A convenção ativa dos especialistas é `advisor/`, `rules/`, `contracts/`,
  `tests/` e `pyproject.toml`. No Health Check, o advisor está em
  `advisor/agent.py`; Audit, Refactor e Notification mantêm o mesmo conceito
  dentro de seus pacotes.
- Refactor possui ambiente e entrypoint próprios
  (`mysql-refactor-advisor`); não deve usar o `.venv` do Health Check.
- A relação completa de remoções e preservações está em `HISTORICO.md`.
  Arquivos históricos referenciados, resultados `latest`, inboxes e contratos
  dos consumidores não são código morto.
- O Lab Console bloqueia `Iniciar consultas` até Health Check estar ativo e
  bloqueia DROP/ALTER até Audit estar ativo. A suíte E2E deve usar modo demo e
  runtime vazio; nunca deve ser apontada para o modo integrado.
- O adapter de teste real do Notification carrega
  `apps/lab-console/scripts/notification_config.py` diretamente e resolve a
  senha somente pelo Keychain dentro do runtime do Notification.
- Em 24/09/2026, 282 verificações automatizadas passaram. Também passaram as
  coletas reais somente leitura de Health Check e Audit, o worker Refactor sem
  pendências, a simulação SELECT com aquecimento e cancelamento, e um envio
  SMTP real para dois destinatários.
- Nenhum DROP ou ALTER real foi executado na revisão final: DDL exige tarefa
  separada e autorização específica. Os wrappers foram validados por dry-run,
  testes de segurança e E2E em modo demo.
- `DATABASE_ACCESS.md` foi sanitizado após a identificação de uma credencial
  em texto claro. Nunca documentar o valor; se ele ainda for válido, rotacionar
  fora do repositório.
