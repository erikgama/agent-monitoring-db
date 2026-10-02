# Instruções globais dos agentes

## Instalação portátil

- O nome do projeto é `agent-monitoring-db`. Leia `README.md` e `docs/SETUP.md`
  para instalar e verificar um clone.
- A configuração de banco e de provedor LLM pertence exclusivamente a
  `config/agent-monitoring.toml`, não versionado. Sua referência pública é
  `config/agent-monitoring.example.toml`; a biblioteca canônica está em
  `agent_monitoring/`. Esses caminhos não secretos podem ser consultados para
  operação e compatibilidade, respeitando o escopo de cada papel.
- Codex, Claude Code e Kimi Code obedecem às mesmas instruções e contratos.
  O cliente e o modelo dos advisors são escolhidos na configuração central;
  os nomes Luna/Codex em registros antigos descrevem a execução histórica.
- Bootstrap e CI usam modo demo e verificações sem banco ou entrega externa.

Estas instruções valem para todo o projeto. Cada agente deve ler este arquivo,
o `MEMORY.md` da raiz e o `AGENTS.md` do próprio papel antes de agir. Health
Check e Audit também devem ler seus respectivos `docs/LLM_ONBOARDING.md`.
Instruções mais específicas podem restringir, mas não ampliar, estes limites.

## Coordenação e fontes de verdade

- O DBA é o único coordenador operacional.
- O agente `notification` recebe somente do MCP central alertas ja validados ou
  o aviso de conclusao derivado de `query_refactor_result.v1`; entrega apenas
  ao canal configurado e nao se comunica diretamente com Health Check, Audit,
  Refactor ou DBA.
- `MEMORY.md` é a memória compartilhada; estado ao vivo e evidência atual
  prevalecem sobre registros históricos.
- Cada agente mantém scripts, evidências, resultados e relatórios dentro da
  própria pasta.
- Não criar uma pasta `shared/`. Itens comuns devem ser referenciados pelo
  caminho real, sem duplicação.
- `.maestri/roles/` é gerenciado pelo Maestri e não deve ser editado.

## Mudanças no banco

- Audit e Health Check observam e devolvem evidências; não decidem mudanças.
- O Refactor recebe trabalho exclusivamente pelo fluxo automático e versionado
  de query lenta: Health Check envia `query_refactor_request.v1` ao MCP, e o
  MCP registra a solicitação para o worker ativo do Refactor.
- Somente o DBA pode considerar e encaminhar uma mudança proposta.
- Toda proposta de índice, schema ou configuração exige evidência, impacto,
  autorização explícita, plano de validação e plano de reversão.
- Não executar DDL, DML, GRANT, REVOKE ou alteração de infraestrutura sem uma
  autorização específica e uma tarefa separada.

## Segurança

- Nunca registrar segredos em código, Markdown, logs, resultados ou handoffs.
- Não ler, copiar, mover ou exibir chaves privadas, perfis de login ou arquivos
  de credenciais.
- Usar exclusivamente o perfil de conexão já configurado, por referência de
  caminho, sem acessar seu conteúdo.
- Não alterar filtros Audit, usuários, privilégios, parâmetros, startup,
  rotação, retenção ou destino de logs durante investigações.
- Não habilitar bloqueio Audit com `abort`.

## Handoffs

- Todo trabalho entre agentes deve ser registrado conforme `docs/handoffs.md`.
- Handoffs apontam para arquivos reais, evidências, cobertura, limitações,
  decisões pendentes e próximo passo.
- Audit e o fluxo comum do Health Check respondem ao DBA. No fluxo versionado
  de query lenta, Health Check envia ao Refactor via MCP e o Refactor devolve
  `query_refactor_result.v1` exclusivamente ao DBA via MCP.

## Limites operacionais

- Health Check executa somente a coleta segura existente e grava apenas nos
  diretórios `results/` do próprio fluxo dentro de `agents/health-check/`; o workload deliberadamente lento não é
  automático. A única exceção é a simulação iniciada explicitamente pelo
  operador no botão `Executar SELECTs`: ela executa a query conhecida e
  versionada até o mesmo job ser cancelado.
- O coletor de Slow Query Log pode fazer triagem a cada 30 segundos sobre uma
  janela de quatro horas. Somente
  query conhecida, versionada e estritamente acima de 80 segundos pode gerar
  uma solicitação deduplicada ao Refactor.
- Audit opera somente leitura e mascara evidências persistidas.
- Refactor preserva literalmente o SQL original e seu contrato de resultado.
- Notification revalida `health_check_alert.v1`, `audit_security_alert.v1` ou
  `query_refactor_result.v1`, nao consulta banco, nao recalcula severidade e nao
  persiste payloads ou anexos. O aviso de Refactor nunca inclui SQL literal e
  somente e solicitado pelo MCP depois do registro do resultado no DBA.
