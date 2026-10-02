# MySQL HeatWave Audit Security

> Instalação atual: `docs/SETUP.md` da raiz. Banco e LLM são definidos em
> `config/agent-monitoring.toml`; Codex, Claude Code e Kimi Code usam os mesmos
> contratos. Modelos e execuções citados no histórico são registros anteriores.

Fluxo somente leitura em que o coletor produz evidências mascaradas e o agente
Luna decide alertas a partir do HTML e de regras em linguagem natural.

```text
MySQL Enterprise Audit
        ↓
collector → audit_security/results/latest.json + audit_security/results/latest.html
        ↓
Luna + audit_security/advisor/rules.md
        ↓
audit_security_alert.v1
        ↓
MCP central → DBA → Notification
```

O coletor não classifica eventos, não avalia thresholds e não chama o MCP.
O código local apenas confirma que a evidência selecionada pelo agente existe
no relatório antes da publicação.

Codex, Claude Code e outros LLMs devem começar por
[docs/LLM_ONBOARDING.md](docs/LLM_ONBOARDING.md).

## Instalação

```sh
uv sync --extra dev
```

## Execução

Uma coleta:

```sh
uv run mysql-audit-security collect
```

Coleta contínua a cada 15 segundos:

```sh
uv run mysql-audit-security monitor
```

Agente Luna a cada 15 segundos:

```sh
uv run mysql-audit-advisor
```

O laboratório integrado inicia o coletor e o agente, mas não gera DDL:

```sh
python3 apps/lab-console/scripts/run-audit-lab.py --execute
```

As tentativas controladas de DROP e ALTER continuam pertencendo ao workload do
DBA e exigem confirmação explícita.

## Estrutura

- `audit_security/collector.py`: coleta e mascaramento de fatos;
- `audit_security/main.py`: coleta única ou contínua;
- `audit_security/advisor/agent.py`: decisão Luna e publicação no MCP;
- `audit_security/advisor/rules.md`: regras naturais de DROP/ALTER bloqueados;
- `audit_security/advisor/analysis.schema.json`: formato obrigatório da decisão;
- `audit_security/results/`: último JSON e HTML da coleta;
- `audit_security/advisor/results/`: última decisão e estado mínimo de deduplicação;
- `contracts/`: contrato canônico `audit_security_alert.v1`;
- `audit_security/sql/`: SQL read-only permitido;
- `archive/`: evidência e scripts históricos, nunca executados automaticamente.

## Testes

```sh
uv run python -m unittest discover -s tests -v
uv run ruff check .
uv run ruff format --check .
```

O Audit não altera filtros, usuários, privilégios, parâmetros, banco ou
infraestrutura. SQL literal, identidades e endereços são removidos ou
pseudonimizados antes da persistência e antes do Luna.
