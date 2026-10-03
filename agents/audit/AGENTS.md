# Instruções do módulo Audit

## Missão

Investigar segurança e conformidade em modo somente leitura, coletar fatos
mascarados do MySQL Enterprise Audit e devolver alertas validados ao DBA por
meio do MCP central. O escopo funcional é exclusivamente `sakila`.

Antes de alterar arquitetura, regras, coleta, publicação ou artefatos, leia
obrigatoriamente `docs/LLM_ONBOARDING.md`.

## Contexto permitido

- Leia este arquivo, `../../AGENTS.md`, `../../MEMORY.md` e
  `../../docs/handoffs.md`.
- Trabalhe somente nesta pasta e nos caminhos externos explicitamente
  autorizados.
- Não leia credenciais, memórias globais ou pastas de agentes irmãos.

## Fluxo oficial

- `audit_security/collector.py` coleta, mascara e normaliza fatos; não decide alertas.
- `audit_security/main.py` grava atomicamente `audit_security/results/latest.json` e `latest.html`.
- `audit_security/advisor/rules.md` contém as regras naturais lidas pelo LLM configurado.
- `audit_security/advisor/agent.py` lê o HTML completo, decide e chama o MCP somente em `alert`.
- `audit_security/alerting.py` valida a evidência escolhida, o contrato, a deduplicação e a
  fronteira MCP; não reavalia a regra do agente.
- `audit_security/advisor/results/` contém a decisão mais recente e o estado privado mínimo.
- `contracts/audit_security_alert.v1.schema.json` é o contrato canônico.

Os comandos oficiais são:

```sh
uv run mysql-audit-security collect
uv run mysql-audit-security monitor
uv run mysql-audit-advisor
```

Coletor e agente executam em intervalos padrão de 15 segundos. Não existe
monitor determinístico paralelo.

## Regras e segurança

- O LLM configurado é o único dono da decisão de alerta.
- O coletor não classifica severidade, não avalia regra e não chama o MCP.
- Alertas atuais cobrem somente DROP/TRUNCATE e ALTER TABLE bloqueados em
  `sakila`, conforme `audit_security/advisor/rules.md`.
- Somente SQL read-only versionado em
  `audit_security/sql/security_snapshot/` pode ser
  executado.
- Não alterar filtros Audit, usuários, privilégios, parâmetros, startup,
  rotação, retenção, banco ou infraestrutura.
- Não executar arquivos de `changes/`.
- Não gerar eventos sintéticos, DDL, DML, GRANT, REVOKE ou login inválido.
- Nunca persistir SQL literal, identidade, endereço ou segredo.
- O Audit publica somente no MCP central; não acessa SMTP e não escolhe canal
  ou destinatário.

## Coordenação

Toda evidência e conclusão responde exclusivamente ao DBA. Somente o DBA pode
considerar ou encaminhar mudanças. Handoffs seguem `../../docs/handoffs.md`.

## Validação

```sh
python3 -m unittest discover -s tests -v
ruff check .
ruff format --check .
```
