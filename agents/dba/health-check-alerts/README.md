# Inbox técnica de alertas MySQL

Esta pasta é o consumidor privado do DBA para alertas já validados pelo MCP
central. O fluxo é:

```text
Health Check -> MCP incident_raise -> DBA Health Check inbox
```

Cada alerta aceito gera uma pasta imutável diretamente em `runtime/inbox/`:

```text
<detected-at>__<categoria>__<alert-id>/
├── alert.json
├── latest.json
└── latest.html
```

- `alert.json` preserva a referência estruturada do alerta e aponta para os
  dois arquivos de evidência, sem duplicar seus conteúdos;
- `latest.json` contém o JSON da coleta que originou o alerta;
- `latest.html` contém a visualização HTML da mesma coleta.

Os três artefatos pertencem ao mesmo `audit_id`. O diretório usa timestamp UTC,
categoria e `alert_id`, permitindo ordenar os incidentes sem sobrescrever um
alerta anterior. Uma repetição com o mesmo `alert_id` é tratada como duplicata.

O runtime é ignorado pelo Git. Não há LLM, SQL, consulta ao MySQL, destinatário,
SMTP, execução de workload ou ação corretiva neste consumidor. O recebimento é
somente uma evidência de entrada para investigação posterior do DBA.

O MCP continua sendo responsável pela validação completa do contrato
`health_check_alert.v1`. Este consumidor aceita somente esse contrato e aplica
novamente guardas de identidade, categoria, pareamento de
`audit_id` e escopo exclusivo do schema `sakila` antes de persistir. A pasta é
preparada fora do nome final e publicada como uma única unidade para evitar um
incidente parcialmente visível.

A fixture sintetica em `fixtures/query-latency-critical.json` reproduz o
formato atual recebido pelo consumidor. A validacao local pertence ao proprio
codigo da inbox e aos testes, sem uma segunda implementacao paralela do
contrato.

Alertas `audit_security_alert.v1` pertencem à inbox separada
`../audit-security-alerts/`, que grava apenas resumos sanitizados.

## Testes

Na pasta `agents/dba`:

```sh
python3 -m unittest tests.test_health_check_alerts -v
```
