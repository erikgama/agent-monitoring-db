# Escopo funcional de schemas

O fluxo Health Check → MCP → Refactor → MCP → DBA trata exclusivamente os
schemas `sakila` e `sakila_dev`.

- `sakila`: origem preservada; observação read-only e referência funcional.
- `sakila_dev`: clone de laboratório para validações read-only controladas.
- Outros schemas, inclusive `airportdb`, ficam fora de rankings, análises,
  handoffs, refatorações e conclusões.
- Métricas globais da instância podem permanecer na rotina de disponibilidade,
  mas não devem introduzir queries de outros schemas em relatórios funcionais.
- Audit apenas relata eventos atribuíveis a `sakila` ou `sakila_dev`; nenhuma
  alteração de configuração de Audit é implícita nesta política.
- Cada agente altera somente arquivos dentro de sua própria pasta.
