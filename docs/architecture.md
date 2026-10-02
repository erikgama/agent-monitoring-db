# Arquitetura dos agentes

O projeto possui cinco agentes permanentes em `agents/`: DBA, Health Check,
Audit, Refactor e Notification. O MCP central é a fronteira de validação e
transporte; ele não é um sexto agente de decisão.

`AGENTS.md` define a governança global e `MEMORY.md` registra somente o contexto
operacional compartilhado. Estado ao vivo, contratos e código atual prevalecem
sobre memória e relatórios históricos.

## Convenção estrutural

Os agentes especialistas mantêm a mesma base, adaptada à responsabilidade de
cada papel:

| Elemento | Health Check | Audit | Refactor | Notification |
| --- | --- | --- | --- | --- |
| Advisor executável | `advisor/agent.py` | `audit_security/advisor/agent.py` | `query_refactor/advisor/agent.py` | `notification/advisor/agent.py` |
| Regra em português | `advisor/rules.md` | `audit_security/advisor/rules.md` | `query_refactor/advisor/rules.md` | `notification/advisor/rules.md` |
| Contratos | `contracts/` | `contracts/` | `contracts/` | `contracts/` |
| Testes isolados | `tests/` | `tests/` | `tests/` | `tests/` |
| Ambiente próprio | `pyproject.toml` | `pyproject.toml` | `pyproject.toml` | `pyproject.toml` |

O DBA é coordenador e consumidor, por isso sua estrutura é orientada a inboxes,
análises, playbooks, workloads e relatórios. O MCP possui tools, validadores e
adaptadores de entrega, sem advisor próprio.

## Fluxos operacionais

1. Health Check e Audit coletam evidências somente leitura.
2. Seus advisors interpretam as regras e enviam somente contratos válidos ao
   MCP central.
3. O MCP revalida e entrega de forma independente ao DBA e ao Notification.
4. O Notification preserva a severidade recebida e roteia ao canal permitido;
   não consulta o banco e não recalcula severidade.
5. No fluxo versionado de query lenta, Health Check pode solicitar Refactor via
   MCP; o Refactor valida em `sakila_dev` e devolve o resultado somente ao DBA
   via MCP. O MCP pode então solicitar um aviso de conclusão ao Notification.

## Princípios

- Cada agente mantém código, resultados e evidências dentro da própria pasta.
- Não existe pasta `shared/`; contratos comuns são copiados somente nas
  fronteiras que os revalidam e têm sincronismo verificado por teste.
- Resultados `latest` são estado operacional, não código morto.
- `archive/` contém evidência histórica explicitamente fora do runtime.
- Mudanças no banco exigem tarefa separada, autorização, validação e reversão.
- Segredos nunca transitam em documentação, código, logs, resultados ou
  handoffs.
- `.maestri/roles/` é gerenciado exclusivamente pelo Maestri.
