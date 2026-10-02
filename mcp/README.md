# MySQL Conf MCP Central

> Guia atual de instalação: README.md da raiz e docs/SETUP.md.
> Nos exemplos abaixo, defina `AGENT_MONITORING_ROOT="$(pwd)"` na raiz do clone.

Servidor MCP local que recebe alertas estruturados do Health Check e do Audit
Security, valida seus contratos, persiste primeiro na inbox técnica privada do
DBA e somente depois permite o encaminhamento ao Notification.

As tools expostas são `incident_raise`, `refactor_request_raise` e
`refactor_result_raise`. O transporte é MCP sobre `stdio`: o processo não abre
porta, não oferece API HTTP e normalmente é iniciado sob demanda pelo produtor.

O fluxo de query lenta é:

```text
Health Check -> refactor_request_raise -> inbox do Refactor
Refactor -> refactor_result_raise -> inbox do DBA
                                   -> Notification -> e-mail de conclusao
```

O pedido é deduplicado por query e fingerprint. O resultado é deduplicado por
query e `request_id`; somente o primeiro registro pode produzir um aviso. O
e-mail informa a conclusão e métricas resumidas, sem SQL literal. Nenhuma das
tools aplica a proposta em produção.

## Arquitetura

```text
MySQL HeatWave
   |                 coleta e decisão determinística
   +-> Health Check ----------------------------------+
   |                                                  |
   +-> Enterprise Audit -> Audit Security ------------+-> MCP incident_raise
                                                            |
                                      validação contratual  |
                                                            +-> DBA -> inbox privada
                                                                      |
                                                                      +-> Notification -> e-mail
```

Responsabilidades:

- **Health Check** coleta sinais de saúde e performance em modo read-only,
  decide findings e severidade por regras determinísticas e produz
  `health_check_alert.v1`.
- **Audit Security** coleta e normaliza evidências do Enterprise Audit em modo
  read-only, decide findings e severidade por regras determinísticas e produz
  `audit_security_alert.v1`.
- **MCP central** revalida o contrato e encaminha o alerta. Ele não determina
  saúde, não recalcula severidade e não interpreta o significado operacional do
  finding.
- **Notification** revalida o alerta, aplica a política de canal à severidade
  já recebida, escolhe destinatários somente pela configuração local e realiza
  no máximo uma tentativa SMTP.
- **DBA** recebe evidência técnica em uma inbox privada. O recebimento não abre
  investigação automaticamente e não autoriza nenhuma mudança no banco.

O registro no DBA é pré-condição para Notification. Falha, desabilitação ou
erro de configuração no DBA impede a tentativa de e-mail. Uma falha posterior
do Notification não desfaz o registro já persistido.

## Escopo

O MCP implementa somente:

1. descoberta das tres tools publicadas;
2. validação de `health_check_alert.v1` e `audit_security_alert.v1`;
3. rejeição de payload inválido ou aparentemente sensível antes do fan-out;
4. persistência obrigatória na inbox correta do DBA;
5. encaminhamento opcional para Notification após a persistência;
6. resposta estruturada e sanitizada para o produtor.

Ficam fora do escopo:

- consulta ao MySQL ou HeatWave;
- execução de SQL, workload ou ação corretiva;
- criação de regra, finding, categoria ou severidade;
- alteração de Audit, usuários, privilégios ou infraestrutura;
- tickets, tarefas, filas, retries, SQLite, banco ou Redis;
- WhatsApp, Slack, Teams, SMS ou chamada telefônica;
- comunicação direta do MCP com operadores.

No fluxo `refactor_result_raise`, o MCP valida o resultado, registra a copia do
DBA e somente entao pode pedir ao Notification um aviso de conclusao. O aviso
nao inclui SQL literal, nao autoriza producao e uma entrega `duplicate` nao e
reenviada.

## Tool `incident_raise`

Nome conceitual: `incident.raise`  
Nome técnico MCP: `incident_raise`

A entrada possui exatamente um argumento:

```json
{
  "alert": {
    "contract_version": "health_check_alert.v1",
    "alert_id": "UUID",
    "audit_id": "UUID",
    "detected_at": "ISO 8601 UTC",
    "environment": "identificador-logico",
    "source": "health-check",
    "severity": "warning",
    "category": "query_latency",
    "title": "titulo curto",
    "summary": "resumo",
    "findings": [],
    "dedupe_key": "chave-estavel",
    "report": {
      "json": {},
      "html": "<!doctype html>...</html>",
      "report_generated_at": "ISO 8601 UTC",
      "report_format_version": "versao"
    },
    "metadata": {}
  }
}
```

O exemplo é apenas estrutural. O payload completo deve obedecer ao JSON Schema
do contrato selecionado. Consulte também
[`docs/tool-catalog.md`](docs/tool-catalog.md).

### Contratos suportados

| Contrato | Dono canônico | Origem obrigatória | Categorias atuais |
|---|---|---|---|
| `health_check_alert.v1` | Health Check | `health-check` | `deadlock`, `lock_wait`, `query_latency`, `connections`, `innodb`, `replication`, `database_error` |
| `audit_security_alert.v1` | Audit Security | `audit-security` | `destructive_ddl`, `schema_change` |

Os contratos canônicos permanecem nos produtores:

```text
agents/health-check/contracts/health_check_alert.v1.schema.json
agents/audit/contracts/audit_security_alert.v1.schema.json
```

O pacote MCP contém cópias byte a byte em
`src/mysqlconf_mcp/contracts/`. `provenance.json` registra os SHA-256 e os testes
falham quando uma cópia diverge da fonte canônica. O MCP não é dono e não
modifica esses contratos.

## Validação

Toda validação ocorre antes de qualquer chamada ao Notification ou ao DBA. O
MCP verifica, entre outros pontos:

- JSON Schema e campos fechados do contrato;
- `contract_version`, `source`, severidade e categoria;
- UUIDs e timestamps com fuso;
- presença do JSON e do HTML completos da coleta;
- igualdade do `audit_id` entre alerta, JSON e HTML;
- igualdade entre `report_generated_at` e `collected_at`;
- igualdade entre `report_format_version` e `schema_version`;
- `detected_at` não anterior à coleta;
- `dedupe_key` sem timestamp, `alert_id` ou `audit_id`;
- ausência de campos de segredo, connection strings, endpoints e chaves;
- ausência aparente de segredo ou endereço IP privado em valores textuais.

Payload rejeitado não atravessa nenhuma rota. Os erros retornados contêm apenas
campo, código estável e mensagem segura.

## Ordem obrigatória de roteamento

Depois da validação contratual, `incident_raise` registra primeiro a evidência
na inbox do DBA. O Notification somente é chamado quando o DBA retorna
`recorded` ou `duplicate`. Se a persistência estiver desabilitada, mal
configurada ou falhar, o e-mail não é tentado, `accepted` permanece `false` e
o produtor pode tentar novamente sem marcar o alerta como entregue.

Uma falha posterior do Notification não remove nem invalida o registro já
persistido no DBA.

## Fan-out para Notification

Com `MCP_NOTIFICATION_ENABLED=true`, o MCP chama a API Python pública
`NotificationDispatcher.dispatch()` e entrega o mesmo alerta validado.
Notification então:

- revalida o contrato;
- ignora qualquer destinatário existente no payload;
- não recalcula a severidade recebida;
- suprime `info` com `suppressed_by_policy`;
- usa somente `NOTIFICATION_EMAIL_RECIPIENTS_WARNING` para `warning`;
- usa somente `NOTIFICATION_EMAIL_RECIPIENTS_CRITICAL` para `critical`;
- retorna `dry_run` quando a entrega externa está desabilitada;
- realiza no máximo uma tentativa SMTP quando habilitado;
- mantém anexos somente em memória e não persiste o alerta.

O MCP não implementa SMTP, template, destinatários nem retry. Essa etapa sempre
ocorre depois da confirmação de persistência no DBA.

## Fan-out para o DBA

Com `MCP_DBA_ENABLED=true`, o adapter escolhe a inbox pelo
`contract_version`.

### Alertas do Health Check

Destino padrão:

```text
agents/dba/health-check-alerts/runtime/inbox/
```

Cada `alert_id` cria atomicamente:

```text
<detected-at>__<categoria>__<alert-id>/
├── alert.json
├── latest.json
└── latest.html
```

O `alert.json` guarda a referência estruturada, enquanto `latest.json` e
`latest.html` preservam a evidência completa da mesma coleta. Reentregar o
mesmo `alert_id` retorna `duplicate`.

### Alertas do Audit Security

Destino padrão:

```text
agents/dba/audit-security-alerts/runtime/inbox/
```

Cada evento cria atomicamente somente:

```text
<detected-at>__<categoria>__<alert-id>/
├── alert-summary.json
└── audit-event-summary.json
```

A inbox Audit usa allowlist e não copia o relatório completo. Ela não persiste
SQL literal, identidades, credenciais, host/IP ou configuração de comunicação.
A idempotência usa o `event_key` SHA-256; o mesmo evento com outro `alert_id`
retorna `duplicate`.

## Semântica da resposta

Exemplo resumido:

```json
{
  "accepted": true,
  "status": "validated",
  "alert_id": "UUID",
  "audit_id": "UUID",
  "delivery": {
    "target": "notification",
    "status": "dry_run",
    "delivered": false,
    "channel": "email"
  },
  "dba": {
    "target": "dba",
    "status": "recorded",
    "recorded": true
  }
}
```

Interpretação correta:

- `accepted=true`: o contrato foi validado e a persistência no DBA foi confirmada;
- `status=validated`: a validação terminou, não significa envio de e-mail;
- `delivery.status=dry_run`: Notification processou o alerta sem chamar SMTP;
- `delivery.status=sent` e `delivered=true`: a tentativa foi aceita pelo SMTP;
- `delivery.status=suppressed_by_policy`: a política local não enviou o alerta;
- `delivery.status=failed`: a rota Notification falhou de modo controlado;
- `dba.status=recorded`: a inbox criou o registro atômico;
- `dba.status=duplicate`: a evidência já existia;
- `dba.status=failed`: a rota DBA falhou de modo controlado e o e-mail não foi tentado;
- `delivery.status=not_attempted`: o DBA não confirmou a persistência obrigatória;
- `not_configured`: a rota correspondente não estava habilitada.

Aceitação pelo SMTP não comprova leitura humana da mensagem. Da mesma forma,
registro na inbox não autoriza investigação ou correção automática.

## Configuração

Todas as rotas começam desabilitadas.

| Variável | Padrão | Efeito |
|---|---:|---|
| `MCP_NOTIFICATION_ENABLED` | `false` | Autoriza a chamada ao Notification |
| `MCP_DBA_ENABLED` | `false` | Autoriza o registro na inbox do DBA |
| `MCP_DBA_ALERTS_DIR` | inbox padrão do Health Check | Sobrescreve apenas a inbox Health Check |
| `MCP_DBA_AUDIT_ALERTS_DIR` | inbox padrão do Audit | Sobrescreve apenas a inbox Audit Security |
| `NOTIFICATION_DELIVERY_ENABLED` | `false` | Mantém dry-run ou autoriza a tentativa SMTP |
| `NOTIFICATION_EMAIL_FROM` | vazio | Remetente configurado pelo operador |
| `NOTIFICATION_EMAIL_RECIPIENTS_WARNING` | vazio | Destinatários de warning |
| `NOTIFICATION_EMAIL_RECIPIENTS_CRITICAL` | vazio | Destinatários de critical |
| `NOTIFICATION_EMAIL_RECIPIENTS_REFACTOR` | vazio | Destinatários do aviso de Refactor; quando vazio, usa a lista de warning |
| `SMTP_HOST` | vazio | Host SMTP usado pelo Notification |
| `SMTP_PORT` | `587` | Porta SMTP |
| `SMTP_USERNAME` | vazio | Usuário SMTP |
| `SMTP_USE_STARTTLS` | `true` | Habilita STARTTLS |

Para `warning` e `critical`, a lista correspondente de destinatários deve
existir mesmo em `dry_run`; caso contrário, Notification retorna
`recipients_not_configured`.

### Credencial SMTP no ambiente local

O MCP não consulta o Chaves do macOS e seu código não lê `SMTP_PASSWORD`. No
fluxo local vigente do Audit, somente o runtime do Notification resolve a senha
imediatamente antes da entrega, usando o serviço fixo
`mysqlconf-notification-smtp` e a conta definida em `SMTP_USERNAME`.

Não configure destinatários ou senha dentro do alerta. Não versione senha,
`.env` real ou conteúdo do Chaves.

> **Compatibilidade conhecida:** o cliente MCP do Health Check ainda inclui
> `SMTP_PASSWORD` em sua allowlist de ambiente do subprocesso. O MCP não usa a
> variável, mas essa passagem deve ser removida para que o Health Check tenha a
> mesma fronteira estrita já aplicada pelo Audit. Até essa correção, não injete
> `SMTP_PASSWORD` no processo do Health Check; prefira a resolução pelo runtime
> do Notification.

## Instalação e execução

Pré-requisitos: Python 3.11 ou superior e `uv`.

```sh
cd "${AGENT_MONITORING_ROOT}/mcp"
uv sync --extra dev
```

Para iniciar manualmente o servidor stdio:

```sh
uv run mysqlconf-mcp
```

Esse comando aguarda um cliente MCP em stdin/stdout; não é um shell interativo.
No fluxo normal, Health Check ou Audit inicia o servidor automaticamente ao
publicar um alerta.

### Teste seguro com MCP Inspector

Na raiz do repositório:

```sh
MCP_NOTIFICATION_ENABLED=true \
MCP_DBA_ENABLED=true \
NOTIFICATION_DELIVERY_ENABLED=false \
NOTIFICATION_EMAIL_RECIPIENTS_WARNING=warning-operator@example.invalid \
NOTIFICATION_EMAIL_RECIPIENTS_CRITICAL=critical-operator@example.invalid \
npx -y @modelcontextprotocol/inspector \
  uv run --directory mcp mysqlconf-mcp
```

Use somente fixtures fictícias e diretórios temporários quando não quiser
gravar nas inboxes padrão. Com `NOTIFICATION_DELIVERY_ENABLED=false`, nenhuma
conexão SMTP é aberta.

## Logs e dados sensíveis

Logs técnicos vão para `stderr` e podem conter apenas:

- `alert_id` e `audit_id`;
- severidade e categoria validadas;
- status sanitizado das rotas;
- código estável de erro.

JSON, HTML, SQL, destinatários, configurações, segredos e exceções brutas não
devem aparecer nos logs. Valores inválidos são substituídos por `<invalid>`.

## Desenvolvimento e testes

```sh
cd "${AGENT_MONITORING_ROOT}/mcp"
uv run python -m unittest discover -s tests -v
uv run ruff check .
uv run ruff format --check .
```

A suíte cobre:

- descoberta das tres tools;
- sincronismo e proveniência dos dois contratos;
- validação anterior ao fan-out;
- rejeição de dados sensíveis;
- persistência obrigatória no DBA antes da chamada ao Notification;
- Notification real em `dry_run`, protegido contra chamada SMTP;
- inbox Health Check completa e idempotente;
- inbox Audit sanitizada e idempotente por `event_key`;
- categorias `destructive_ddl` e `schema_change`;
- transporte MCP real sobre stdio.
- resultado de Refactor registrado no DBA e aviso deduplicado em dry-run.

Os testes não consultam MySQL e não enviam e-mail real.

## Estrutura do módulo

```text
mcp/
├── docs/tool-catalog.md
├── src/mysqlconf_mcp/
│   ├── contracts/            # cópias verificadas dos contratos
│   ├── delivery/             # adapters Notification e DBA
│   ├── tools/incidents.py    # implementação de incident_raise
│   ├── validation/           # schema, integridade e dados sensíveis
│   ├── config.py             # flags MCP
│   └── server.py             # FastMCP sobre stdio
└── tests/
```

## Diagnóstico rápido

| Sintoma | Interpretação |
|---|---|
| `accepted=false`, `status=rejected` | Contrato rejeitado antes do roteamento |
| `accepted=false`, `status=persistence_required` | O DBA não confirmou `recorded` ou `duplicate`; Notification não foi chamado |
| `delivery.status=not_configured` | `MCP_NOTIFICATION_ENABLED` está desabilitado |
| `delivery.status=not_attempted` | A persistência obrigatória no DBA não foi confirmada |
| `delivery.error_code=recipients_not_configured` | Lista da severidade recebida está vazia |
| `delivery.status=dry_run` | Fluxo validado sem SMTP |
| `dba.status=not_configured` | `MCP_DBA_ENABLED` está desabilitado; o incidente não foi aceito |
| `dba.status=duplicate` | Alerta ou evento já estava registrado |
| `*_enabled_invalid` | Flag booleana contém valor inválido |

Para detalhes do contrato de cada produtor, consulte:

- `../agents/health-check/alerts/alert-contract.md`;
- `../agents/audit/docs/audit-security-runbook.md`;
- `../agents/notification/README.md`;
- `../agents/dba/health-check-alerts/README.md`;
- `../agents/dba/audit-security-alerts/README.md`.
