# Agente Refactor

> Instalação atual: `docs/SETUP.md` da raiz. Banco e LLM são definidos em
> `config/agent-monitoring.toml`; Codex, Claude Code e Kimi Code usam os mesmos
> contratos. Modelos e execuções citados no histórico são registros anteriores.

Especialista em refatoração de SQL recebida exclusivamente pelo contrato MCP
versionado de query lenta. Preserva o contrato do resultado, mantém a consulta
original imutável e produz evidências de equivalência e desempenho para o DBA.

## Escopo

- `sakila`: origem somente leitura;
- `sakila_dev`: laboratório de validação somente leitura;
- nenhum DDL, DML, índice, hint ou alteração de configuração sem autorização
  específica;
- toda entrega retorna exclusivamente ao DBA.

## Estrutura

- `query_refactor/advisor/agent.py`: advisor do provedor configurado e worker da inbox;
- `query_refactor/advisor/rules.md`: regras completas do Refactor;
- `query_refactor/advisor/results/`: jobs, resultados e relatório mais recente;
- `query_refactor/mysql_client.py`: cliente MySQL restrito a `sakila` e `sakila_dev`;
- `query_refactor/mcp_publisher.py`: entrega exclusiva ao DBA pelo MCP;
- `contracts/`: contrato canônico `query_refactor_result.v1`;
- `pyproject.toml`: ambiente e comando próprios do agente;
- `tests/`: testes isolados do advisor;
- `archive/`: trabalhos, templates e laboratórios históricos;
- `skills/`: competências específicas do papel.

## Fluxo resumido

1. Receber `request.json` em um diretório de job dentro de `results/`.
2. Preservar `original.sql` literalmente.
3. Executar o provedor e modelo selecionados no TOML para produzir
   uma proposta estruturada segundo `query_refactor/advisor/rules.md`.
4. Revalidar localmente que a proposta contém somente uma consulta read-only e
   gravar `advisor.json` e `proposed.sql` no mesmo job.
5. Validar original e proposta serialmente no `sakila_dev`.
6. Comparar cabeçalho, linhas e SHA-256 do conjunto de resultados.
7. Gravar `result.json`, `state.json` e `report.md` no mesmo job.
8. Entregar `query_refactor_result.v1` exclusivamente ao DBA pelo MCP.

O advisor `query_refactor/advisor/agent.py` verifica os jobs a cada 30 segundos,
gera uma refatoração nova com o LLM configurado, valida a SQL original e a proposta no
`sakila_dev` e envia o resultado ao DBA pela tool MCP
`refactor_result_raise`. O catálogo recebido do Health Check é usado somente
para confirmar a SQL original conhecida; sua variante de recomendação não é
carregada pelo Refactor. Depois do registro no DBA, o MCP solicita ao
Notification um aviso curto de conclusao sem SQL literal. O resultado local é
persistido antes da entrega; uma repetição não repete o benchmark nem dispara
outro e-mail para o mesmo resultado.

Os controles completos e os critérios de aprovação estão em
[AGENTS.md](AGENTS.md). Os testes isolados não acessam o banco; cada trabalho
permanece integralmente em `query_refactor/advisor/results/<job>/`.

O comando oficial é:

```sh
uv run mysql-refactor-advisor monitor
```
