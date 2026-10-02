# Histórico da revisão final

## 24/09/2026

Revisão pasta a pasta feita com leitura das regras globais e específicas,
busca de referências, verificação de imports, contratos, testes, entrypoints e
fluxos do Lab Console. Um arquivo só foi removido quando não havia consumidor
ativo e existia uma implementação oficial substituta.

### Estrutura padronizada

- Health Check passou a expor o advisor em `advisor/agent.py`, com regra,
  schema, contratos, testes e entrypoint próprios.
- Refactor recebeu `pyproject.toml`, ambiente virtual e entrypoint próprios;
  o Lab Console não depende mais do ambiente do Health Check para executá-lo.
- Audit, Refactor e Notification mantêm seus advisors junto das respectivas
  regras em português e contratos.
- Contratos copiados nas fronteiras MCP/Notification são verificados por
  testes de sincronismo.
- A arquitetura e a topologia foram atualizadas para os cinco agentes; o MCP
  é orquestrador e fronteira de validação, não um agente de decisão.

### Arquivos mortos removidos

Health Check:

- `agents/health-check/refactor_collector/README (2).md`;
- `agents/health-check/refactor_collector/LLM_QUERY_REFACTORING_PLAYBOOK.md`;
- `agents/health-check/refactor_collector/requirements.txt`.

Audit:

- `agents/audit/scripts/README.md`;
- `agents/audit/scripts/collect_security_snapshot`;
- `agents/audit/scripts/monitor_security_events`;
- `agents/audit/scripts/verify_audit_readonly`;
- `agents/audit/audit_security/sql/00_audit_preflight.sql`;
- `agents/audit/audit_security/sql/10_audit_connections.sql`;
- `agents/audit/audit_security/sql/20_audit_ddl.sql`;
- `agents/audit/audit_security/sql/30_audit_errors.sql`;
- `agents/audit/audit_security/sql/40_audit_data_access.sql`.

DBA:

- três fixtures antigas em `agents/dba/health-check-alerts/fixtures/` que
  usavam um formato anterior ao contrato atual;
- `agents/dba/health-check-alerts/validation/README.md` e
  `validate_alert.py`, uma segunda validação incompatível com a inbox atual.

Raiz e app:

- `mudanças.md`, que documentava caminhos inexistentes e um limite incorreto;
- o ramo inalcançável de `refactor.lab` no resolvedor do runner;
- link do README para um prompt inexistente;
- caches, bytecode, relatórios de teste e metadados temporários regeneráveis.

O código-fonte do antigo `agents/notification/archive/` foi removido após a
confirmação de que o runtime ativo não o importava. Os restos gerados do
diretório foram movidos, de forma recuperável, para
`~/.Trash/mysqlconf-notification-archive-20260924`.

### Correções encontradas durante o teste

- O adapter de teste real do Notification agora carrega diretamente o loader
  oficial de configuração não secreta. Antes, a importação indireta do
  orquestrador falhava fora do diretório de scripts.
- Os testes da inbox Health Check do DBA agora usam uma fixture própria no
  contrato atual; o caminho anterior apontava para um exemplo removido.
- Os botões de simulação voltaram a respeitar o monitor correspondente:
  consultas exigem Health Check ativo; DROP e ALTER exigem Audit ativo.
- Acessibilidade dos nomes dos botões e a suíte E2E foram alinhadas à interface
  atual, incluindo o menu inicial do DBA e o painel de atividade recolhido.
- `DATABASE_ACCESS.md` deixou de conter credencial em texto claro. Se o valor
  antigo ainda for válido, ele deve ser rotacionado fora deste repositório.

### Conteúdo preservado intencionalmente

- `results/latest.*`, inboxes e relatórios atuais, pois são estado operacional;
- `agents/audit/archive/` e `agents/refactor/archive/`, pois são evidência
  histórica referenciada por documentação ou testes;
- cópias de contratos nos consumidores, pois cada fronteira os revalida;
- `.maestri/roles/`, gerenciado exclusivamente pelo Maestri;
- ambientes virtuais, `node_modules` e a build `.next`, necessários para a
  execução local sem reinstalação.

### Catálogo do fluxo Refactor

- Foram removidas as três variantes `refactored` previamente armazenadas em
  `agents/health-check/refactor_collector/bad_queries_with_llm_refactor.sql`.
- O catálogo agora contém somente as SQLs originais conhecidas e versionadas;
  toda proposta passa a existir apenas quando o Advisor do Refactor a cria em
  runtime.
- Os leitores do Health Check e do Refactor foram ajustados para o novo formato
  `query`, sem pares nem resposta de referência.

### Validação

Os resultados detalhados, comandos e limites estão em
`apps/lab-console/docs/VALIDACAO.md`. A revisão executou 282 verificações
automatizadas aprovadas, além de lint, formatação, tipos, build, coletas
somente leitura, simulação SELECT controlada e um envio SMTP real.

### Remoção do fluxo manual de Refactor

- `agents/dba/rules/query-refactor-first-pass.md`: regra documental sem
  consumidor no runtime;
- `agents/dba/requests/refactor-request-template.md`: template de um handoff
  manual que não faz parte da arquitetura final;
- `agents/dba/playbooks/refactor-validation-sakila-dev.md`: playbook antigo,
  substituído pelo advisor executável e pelas regras atuais do Refactor.

As referências ativas foram atualizadas para o fluxo único
Health Check -> MCP -> Refactor -> MCP -> DBA. Os handoffs datados de 11/09
foram preservados como evidência histórica e não são consumidos pelo runtime.
