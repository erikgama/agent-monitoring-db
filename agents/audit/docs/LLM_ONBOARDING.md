# Onboarding para LLM: Audit Security

> Instalação atual: `docs/SETUP.md` da raiz. Banco e LLM são definidos em
> `config/agent-monitoring.toml`; Codex, Claude Code e Kimi Code usam os mesmos
> contratos. Modelos e execuções citados no histórico são registros anteriores.

Este documento é a referência de entrada para Codex, Claude Code ou outro LLM
que precise trabalhar no Audit. Leia também `../../../AGENTS.md`,
`../../../MEMORY.md`, `../AGENTS.md` e `../../../docs/handoffs.md` antes de
agir. Código e estado ao vivo prevalecem sobre este registro.

## Estado arquitetural atual

O Audit é orientado pelo agente Luna. O coletor lê o MySQL Enterprise Audit,
mas não decide alertas e não chama o MCP.

```text
MySQL Enterprise Audit via audit_log_read()
    ↓ coleta, máscara e normalização read-only
audit_security/results/latest.json + latest.html
    ↓ leitura direta pelo processo mysql-audit-advisor
Luna gpt-5.6-luna, effort low + audit_security/advisor/rules.md
    ↓ decisão estruturada: alert | no_alert | inconclusive
validação exata da evidência selecionada
    ↓ chamada direta, sem file watcher
MCP incident_raise
    └── DBA → inbox Audit
              ↓ persistência confirmada
              Notification → e-mail
```

## Responsabilidades por componente

| Componente | Responsabilidade | Não faz |
| --- | --- | --- |
| `audit_security/collector.py` | Lê, mascara e normaliza fatos do Audit | Não aplica regras, severidade ou publicação |
| `audit_security/main.py` | Executa coleta única ou contínua e grava HTML/JSON | Não chama Luna ou MCP |
| `audit_security/advisor/rules.md` | Define em linguagem natural os eventos elegíveis | Não é executável |
| `audit_security/advisor/agent.py` | Lê o HTML, chama Luna e conduz a publicação quando há alerta | Não executa SQL de mudança |
| `audit_security/alerting.py` | Confere a evidência exata, valida o contrato, deduplica e chama o MCP | Não reavalia a regra semântica |
| MCP central | Revalida e roteia o alerta | Não lê o Audit Log |
| Notification | Entrega no canal configurado | Não consulta banco e não decide alertas |
| DBA | Recebe e preserva a evidência encaminhada | É o único coordenador operacional |

## O que Luna realmente lê

Luna não acessa o Audit Log bruto. O coletor usa `audit_log_read()`, remove ou
pseudonimiza SQL, identidades e endereços e grava `audit_security/results/latest.html`. O
processo `mysql-audit-advisor` lê esse HTML completo e o combina com
`audit_security/advisor/rules.md`.

- Modelo: `gpt-5.6-luna`.
- Reasoning effort: `low`.
- Intervalo padrão do coletor e do advisor: 15 segundos.
- Eventos anteriores ao início do advisor são históricos e não alertam.
- No Lab Console, a primeira coleta valida a conexão e vira a baseline de
  inicialização; o Luna é chamado somente quando uma coleta posterior chega.
- O schema de saída é `audit_security/advisor/analysis.schema.json`.

As regras atuais permitem alertas somente para:

- DROP DATABASE, DROP TABLE ou TRUNCATE bloqueado em `sakila`;
- ALTER TABLE bloqueado em `sakila`;
- resultado `failure` com código 1044, 1142 ou 1227.

DDL bem-sucedido, outros schemas, outros códigos, eventos antigos ou evidência
incompleta não alertam.

## Quem chama o MCP

O próprio processo Python `mysql-audit-advisor` chama o MCP. Não existe file
watcher nem outro serviço lendo o JSON de decisão.

1. `audit_security/advisor/agent.py` lê `audit_security/results/latest.html` e `latest.json`;
2. inicia `codex exec` e envia HTML + regras ao Luna;
3. recebe e valida a decisão estruturada;
4. `audit_security/alerting.py` confirma que cada campo selecionado existe exatamente no
   JSON pareado, sem recalcular a regra do Luna;
5. o processo chama `McpIncidentPublisher.publish()`;
6. o cliente inicia `uv run --directory mcp mysqlconf-mcp`;
7. chama a tool `incident_raise` por stdio;
8. somente depois grava decisão e resultado em
   `audit_security/advisor/results/latest.json`.

O arquivo do advisor é histórico latest-only, não uma fila. O estado privado
de deduplicação fica em `audit_security/advisor/results/runtime/alert-state.json` e só registra
publicações aceitas pelo MCP.

## Artefatos e fontes de verdade

- `audit_security/results/latest.html`: relatório mascarado que Luna interpreta.
- `audit_security/results/latest.json`: par estruturado usado para validar a evidência.
- `audit_security/advisor/results/latest.json`: última decisão e resultado MCP.
- `audit_security/advisor/results/runtime/alert-state.json`: deduplicação de eventos aceitos.
- `contracts/audit_security_alert.v1.schema.json`: contrato canônico.
- `agents/dba/audit-security-alerts/runtime/inbox/`: persistência dos alertas
  aceitos, com `alert-summary.json` e `audit-event-summary.json`.

`resultados/`, `config/intelligence/`, caches Python e bytecode antigo não
participam do runtime atual. Não use arquivos `.pyc` para inferir arquitetura.
`changes/` contém histórico e nunca deve ser executado automaticamente.

## Validação real de 2026-09-18

- Uma tentativa controlada de DROP TABLE foi negada pelo MySQL com erro 1142.
- Uma tentativa controlada de ALTER TABLE foi negada pelo MySQL com erro 1142.
- Nenhuma mutação foi aplicada.
- Luna real leu o HTML e decidiu um alerta para cada evento.
- Para ambos, MCP respondeu `validated`, Notification respondeu `sent` e DBA
  respondeu `recorded`.
- Evidências foram gravadas em
  `agents/dba/audit-security-alerts/runtime/inbox/`.
- O coletor normaliza timestamps do MySQL Audit sem fuso para UTC com `Z`.
- O schema estruturado declara `type` explicitamente em campos `enum` e
  `const`, requisito da API do Luna.
- O subprocesso MCP usa cache `uv` temporário isolado quando nenhum cache é
  configurado.
- Handoff: `../reports/2026-09-18-agent-owned-alerting.md`.

## Validação obrigatória após mudanças

```sh
uv run --extra dev python -m unittest discover -s tests -p 'test*.py' -v
uv run --extra dev ruff check src tests
uv run --extra dev ruff format --check src tests
```

Um teste real de roteamento exige um evento controlado pertencente ao DBA,
autorização explícita e confirmação de que a operação foi negada. Nunca crie
DDL apenas para testar o Audit por iniciativa própria.

## Limites que não podem ser removidos

- Escopo funcional exclusivamente `sakila`.
- Coleta somente leitura e evidência mascarada.
- Luna é o único dono da decisão de alerta.
- O coletor nunca chama o MCP.
- O validador local confirma integridade, mas não substitui a decisão do Luna.
- Não alterar filtros Audit, usuários, privilégios, parâmetros, rotação,
  retenção ou infraestrutura.
- Não acessar SMTP nem escolher destinatários no Audit.
- Não ler nem documentar credenciais.
- Não introduzir file watcher sobre `audit_security/advisor/results/latest.json`.
