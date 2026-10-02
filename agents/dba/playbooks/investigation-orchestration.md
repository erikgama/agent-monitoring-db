# Playbook: orquestração de investigação

## 1. Selecionar a ocorrência

Use a ocorrência já registrada pelo MCP nas inboxes do DBA. Preserve o ID, a
janela temporal, a evidência disponível e as limitações do contrato recebido.

## 2. Escolher a fonte

- Use **Audit** para conexões, escritas, DDL, privilégios, ações
  administrativas e atribuição de eventos a ator, origem, horário e sessão.
- Use **Health Check** para disponibilidade, conexões, sessões, locks,
  transações longas, waits de I/O, InnoDB e top queries.
- É permitido acionar ambos em paralelo quando as perguntas forem independentes.

Para Top 3 atual, seguir a regra curta de `../AGENTS.md`.

## 3. Consolidar evidências

Receba relatórios pelos caminhos reais dos agentes. Registre janela, cobertura,
limitações e divergências. Ausência de evento não prova ausência de atividade
quando a categoria ou a janela não estiver comprovadamente coberta.

## 4. Receber o Refactor

Não crie solicitação manual. Para queries conhecidas acima do limite, o Health
Check publica `query_refactor_request.v1` no MCP e o worker ativo do Refactor
processa a inbox. Revise o `query_refactor_result.v1` registrado em
`../refactor-results/runtime/inbox/`. Não trate esperas deliberadas como ganho
produtivo.

## 5. Considerar mudança

Use `database-change-control.md`. Uma recomendação de agente não constitui
autorização para execução.
