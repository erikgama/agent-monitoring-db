# Catalogo de tools

## `incident_raise`

Nome conceitual: `incident.raise`  
Nome tecnico MCP: `incident_raise`

### Proposito

Receber um alerta completo do Health Check ou Audit Security, validar o contrato
`health_check_alert.v1` ou `audit_security_alert.v1`, registrar primeiro o
alerta na inbox técnica do DBA e, somente após `recorded` ou `duplicate`,
encaminhar o mesmo objeto ao `NotificationDispatcher` quando
`MCP_NOTIFICATION_ENABLED=true`. Uma falha de persistência impede a tentativa
de e-mail. Os resultados das duas etapas são devolvidos de forma sanitizada. O nome técnico usa sublinhado
para máxima compatibilidade entre clientes MCP; não existe rota REST
`incident.raise`.

### Quando usar

Use somente depois que a coleta read-only produzir os relatórios JSON e HTML
completos, e o advisor com o LLM configurado decidir pelo alerta conforme as
regras versionadas do agente produtor.

### Quando não usar

Não use para consultar o Health Check, executar SQL, corrigir o banco, escolher
destinatários, configurar ou executar SMTP diretamente, falar com o DBA,
iniciar investigação autônoma, criar tarefas ou executar mudanças. WhatsApp,
Slack, Teams e SMS não são suportados.

### Schema de entrada

```json
{
  "alert": {
    "contract_version": "health_check_alert.v1",
    "alert_id": "UUID",
    "audit_id": "UUID",
    "detected_at": "ISO 8601 UTC terminado em Z",
    "environment": "identificador-logico",
    "source": "health-check",
    "severity": "info | warning | critical",
    "category": "deadlock | lock_wait | query_latency | connections | innodb | replication | database_error",
    "title": "texto",
    "summary": "texto",
    "findings": [
      {
        "check_id": "texto",
        "metric": "texto",
        "observed_value": "valor JSON",
        "evidence": {}
      }
    ],
    "dedupe_key": "chave-estavel",
    "report": {
      "json": {},
      "html": "<!doctype html>...documento completo...</html>",
      "report_generated_at": "ISO 8601 UTC terminado em Z",
      "report_format_version": "versao do relatorio"
    },
    "metadata": {}
  }
}
```

O schema normativo detalhado pertence ao Health Check. A cópia empacotada em
`src/mysqlconf_mcp/contracts/health_check_alert.v1.schema.json` é verificada
contra `agents/health-check/contracts/health_check_alert.v1.schema.json` pela suíte de testes.
O contrato normativo de segurança pertence ao Audit em
`agents/audit/contracts/audit_security_alert.v1.schema.json`.

### Schema de resposta

Persistência obrigatória desabilitada ou indisponível:

```json
{
  "accepted": false,
  "status": "persistence_required",
  "alert_id": "UUID",
  "audit_id": "UUID",
  "delivery": {
    "target": "notification",
    "status": "not_attempted",
    "delivered": false,
    "error_code": "dba_persistence_required"
  },
  "dba": {
    "target": "dba",
    "status": "not_configured"
  }
}
```

Dry-run ou envio concluído:

```json
{
  "accepted": true,
  "status": "validated",
  "alert_id": "UUID",
  "audit_id": "UUID",
  "delivery": {
    "target": "notification",
    "channel": "email",
    "status": "dry_run | sent | suppressed_by_policy",
    "delivered": false
  },
  "dba": {
    "target": "dba",
    "status": "recorded | duplicate",
    "recorded": true,
    "record_id": "identificador-seguro",
    "directory": "data-hora__categoria__alert-id",
    "alert_file": "data-hora__categoria__alert-id/alert.json",
    "report_json_file": "data-hora__categoria__alert-id/latest.json",
    "report_html_file": "data-hora__categoria__alert-id/latest.html"
  }
}
```

Em `sent`, `delivered` será `true`. O status `suppressed_by_policy` é uma
decisão do agente `notification`, não do MCP.

`dba.status=recorded` confirma a criação atômica da pasta com a referência em
`alert.json` e as evidências em `latest.json` e `latest.html`. `duplicate`
informa que o mesmo `alert_id` já
estava registrado. Falhas da rota DBA usam `dba.status=failed` e um
`error_code` sanitizado. Nessa situação, Notification não é chamado e o
produtor não deve registrar a publicação como concluída.

Falha de entrega após validação:

```json
{
  "accepted": true,
  "status": "validated",
  "alert_id": "UUID",
  "audit_id": "UUID",
  "delivery": {
    "target": "notification",
    "channel": "email",
    "status": "failed",
    "delivered": false,
    "error_code": "safe_error_code"
  }
}
```

## `refactor_request_raise`

### Proposito

Receber `query_refactor_request.v1` do Health Check, revalidar o contrato e
registrar uma unica solicitacao deduplicada na inbox do Refactor. A tool nao
executa a query, nao chama o modelo e nao aplica alteracoes no banco.

### Deduplicacao e seguranca

- aceita somente solicitacoes com origem `health-check` e schema `sakila`; o
  catalogo conhecido e versionado e validado antes da chamada pelo Health Check;
- valida fingerprint, hash do SQL original e chave de deduplicacao;
- deduplica a solicitacao pelo fingerprint da query;
- preserva literalmente o SQL original para o Refactor;
- nao encaminha a solicitacao ao Notification ou ao DBA.

### Resposta resumida

```json
{
  "accepted": true,
  "status": "validated",
  "request_id": "UUID",
  "refactor": {
    "target": "refactor",
    "status": "recorded",
    "recorded": true
  }
}
```

## `refactor_result_raise`

### Proposito

Receber `query_refactor_result.v1`, revalidar schema, hashes, equivalencia e
ganho declarado, registrar o resultado na inbox do DBA e solicitar ao
Notification um aviso curto de conclusao quando `MCP_NOTIFICATION_ENABLED=true`.

O aviso informa somente identificador da query, status, resumo, tempos,
percentual, equivalencia e referencia. SQL original, SQL proposta, hashes e
anexos nao atravessam para o corpo do e-mail.

### Deduplicacao e seguranca

- o registro do DBA e deduplicado por `query_id` e `request_id`;
- somente um registro novo pode solicitar o aviso;
- uma segunda chamada retorna `notification.status=duplicate` sem SMTP;
- o Notification revalida sua copia byte a byte do contrato;
- o resultado `approved_lab` nao significa aprovacao para producao;
- nenhuma tool executa a SQL proposta ou aplica mudanca.

### Resposta resumida

```json
{
  "accepted": true,
  "status": "validated",
  "result_id": "UUID",
  "dba": {
    "target": "dba",
    "status": "recorded",
    "recorded": true
  },
  "notification": {
    "target": "notification",
    "channel": "email",
    "status": "dry_run | sent | failed | not_configured | duplicate",
    "delivered": false
  }
}
```

Rejeição contratual anterior ao encaminhamento:

```json
{
  "accepted": false,
  "status": "rejected",
  "errors": [
    {
      "field": "alert.report.json.audit_id",
      "code": "audit_id_mismatch",
      "message": "O audit_id do relatório JSON não corresponde ao audit_id do alerta."
    }
  ]
}
```
