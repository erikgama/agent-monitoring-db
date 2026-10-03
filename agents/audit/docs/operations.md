# Operação do Audit Security

## Responsabilidades

O coletor executa somente as consultas read-only versionadas, mascara SQL,
identidades e endereços, e substitui atomicamente:

- `audit_security/results/latest.json`;
- `audit_security/results/latest.html`.

Ele não aplica regras de incidente, não define severidade e não chama o MCP.

O agente em `audit_security/advisor/agent.py` verifica um relatório novo a cada 15 segundos,
envia ao LLM configurado o HTML completo e `audit_security/advisor/rules.md`, valida que a evidência
selecionada existe no JSON pareado e publica o contrato
`audit_security_alert.v1` no MCP. `no_alert` e `inconclusive` não publicam.

## Comandos

```sh
uv run mysql-audit-security collect
uv run mysql-audit-security monitor --interval-seconds 15
uv run mysql-audit-advisor --interval-seconds 15
```

O monitor do primeiro comando contínuo é somente um loop de coleta. Não existe
um segundo motor de regras.

## Evidência e privacidade

- O escopo funcional é somente `sakila`.
- Eventos recebem `event_key` estável e identificadores pseudonimizados.
- SQL literal não é persistido nem enviado ao LLM.
- O agente pode selecionar apenas uma linha existente no domínio `audit_ddl`.
- Eventos anteriores ao início do agente não são publicados.
- Um evento aceito pelo MCP é registrado no estado privado para não ser
  publicado novamente.

## Alertas atuais

As regras naturais cobrem apenas:

- DROP DATABASE, DROP TABLE ou TRUNCATE em `sakila`, negado por permissão;
- ALTER TABLE em `sakila`, negado por permissão.

Os detalhes canônicos ficam somente em `audit_security/advisor/rules.md`.

## Laboratório

```sh
python3 apps/lab-console/scripts/run-audit-lab.py --execute
```

Esse comando inicia coletor e advisor. As tentativas controladas pertencem ao DBA:

```sh
python3 apps/lab-console/scripts/run-audit-drop-lab.py --execute
python3 apps/lab-console/scripts/run-audit-alter-lab.py --execute
```

O Audit não gera DDL para testar a si próprio.

## Validação

```sh
uv run python -m unittest discover -s tests -v
uv run ruff check .
uv run ruff format --check .
```
