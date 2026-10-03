# Contrato de alerta do Health Check v1

Identificador normativo: `health_check_alert.v1`.

Este contrato define somente o payload que o Health Check entrega ao MCP
central. O cliente stdio está separado em `src/alerting/`; o contrato não
define transporte, servidor MCP, fila,
persistência, destinatários, canais de comunicação, integração com o DBA ou
ações corretivas.

O contrato é compartilhado com MCP e Notification, por isso o schema permanece
versionado e compatível com os consumidores. No estado atual, este produtor
emite somente `query_latency`, a partir da decisão do provedor LLM configurado documentada
em `../advisor/rules.md`, com severidade `critical`.

## Escopo

O alerta descreve uma condição identificada pelo provedor LLM configurado no relatório HTML do MySQL HeatWave
PaaS. O escopo inclui InnoDB e, quando disponível, replicação. Exclui
infraestrutura, host, disco, rede, segurança, slow query log, migração de
engine e o cluster analítico/secondary engine/RAPID.

O schema normativo está em
[`../contracts/health_check_alert.v1.schema.json`](../contracts/health_check_alert.v1.schema.json). A
validação adicional de integridade está em `src/alert_contract.py`.

## Envelope

| Campo | Obrigatório | Regra |
|---|---:|---|
| `contract_version` | sim | Valor fixo `health_check_alert.v1`. |
| `alert_id` | sim | UUID único para a ocorrência do alerta. |
| `audit_id` | sim | UUID da coleta que originou o alerta. |
| `detected_at` | sim | ISO 8601 em UTC, com sufixo `Z`. |
| `environment` | sim | Identificador lógico seguro, sem endpoint ou credencial. |
| `source` | sim | Valor fixo `health-check`. |
| `severity` | sim | `info`, `warning` ou `critical`. |
| `category` | sim | Uma das categorias v1 listadas abaixo. |
| `title` | sim | Título curto e objetivo. |
| `summary` | sim | Explicação humana curta. |
| `findings` | sim | Lista não vazia de evidências que justificam o alerta. |
| `dedupe_key` | sim | Chave estável da condição, sem timestamp ou IDs de ocorrência. |
| `report` | sim | JSON e HTML completos da mesma coleta. |
| `metadata` | não | Objeto extensível, sem dados sensíveis. |

O schema v1 conserva categorias adicionais por compatibilidade do contrato
compartilhado. Elas não são emitidas pelo Health Check atual.

Cada item de `findings` requer `check_id`, `metric`, `observed_value` e
`evidence`. Pode incluir `threshold`, `unit` e `affected_objects` quando forem
aplicáveis. O threshold deve vir de uma regra configurada; ausência de regra
numérica não autoriza inventar um valor.

## Deduplicação

Composição recomendada:

```text
environment|category|affected_object|check_id
```

Normalizar os componentes de modo determinístico. Não incluir `alert_id`,
`audit_id`, `detected_at`, data, hora ou valor observado. Assim, novas
ocorrências da mesma condição preservam a mesma chave, enquanto o MCP central
pode aplicar sua própria janela de supressão.

## Relatório anexado

`report.json` contém o objeto completo retornado pelo coletor que originou a
decisão e é a fonte estruturada de evidências. `report.html` contém o documento
HTML completo gerado a partir daquele mesmo objeto. Coletores anexáveis mantêm o
envelope comum de identidade, tempo, alvo, escopo, status, capacidades,
domínios, findings e retenção definido no schema; seus campos específicos
continuam presentes no mesmo objeto.

Campos auxiliares:

- `report_generated_at`: deve ser igual a `report.json.collected_at`;
- `report_format_version`: deve ser igual a `report.json.schema_version`.

O projeto já publica JSON e HTML a partir do mesmo objeto em
`replace_latest_snapshot()`, exigindo que o HTML contenha `audit_id` e `collected_at`.
O contrato reutiliza essas garantias e acrescenta validação cruzada no envelope.

## Regras de integridade

1. `audit_id` do alerta deve ser igual a `report.json.audit_id` e aparecer em
   `report.html`.
2. `report.json.collected_at` deve aparecer em `report.html`.
3. `detected_at` deve ser posterior ou igual a `report.json.collected_at`.
4. `report_generated_at` deve ser igual a `report.json.collected_at`.
5. `report_format_version` deve ser igual a `report.json.schema_version`.
6. `contract_version`, `severity`, `category` e `source` devem respeitar os
   valores fechados do schema.
7. O payload não pode conter senha, token, chave, string de conexão, endpoint,
   host privado sensível ou outro segredo.
8. O HTML não pode ser originado de uma coleta diferente do JSON.
9. O contrato não inclui destinatário, canal de comunicação ou ação corretiva.
10. O MCP central pode validar e encaminhar o envelope usando somente este
    contrato, sem conhecer os módulos internos do coletor.

## Fluxo de uso

O provedor LLM configurado lê o HTML de latência e decide conforme `advisor/rules.md`. Apenas
uma decisão `alert` gera o envelope. O agente executa a coleta geral read-only,
anexa seu JSON e HTML, valida o contrato localmente e chama `incident_raise` no
MCP central. O MCP revalida o contrato e controla o encaminhamento. O contrato
não autoriza comunicação direta nem ação no banco.
