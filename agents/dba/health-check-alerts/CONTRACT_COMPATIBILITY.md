# Compatibilidade com `health_check_alert.v1`

Este documento descreve apenas como o agente DBA pretende consumir e validar o
contrato `health_check_alert.v1`. Ele **não é a fonte oficial do contrato**. A
definição normativa pertence ao domínio Health Check ou a um futuro pacote de
contratos compartilhado e versionado.

O validador local deve ser revisado quando a definição oficial existir. Uma
divergência deve resultar em rejeição controlada, nunca em inferência silenciosa
ou ação no banco.

## Campos esperados

- `contract_version`
- `alert_id`
- `audit_id`
- `detected_at`
- `environment`
- `source`
- `severity`
- `category`
- `title`
- `summary`
- `findings`
- `dedupe_key`
- `report.json`
- `report.html`
- `metadata`

Para os testes locais desta fase, `report.json` e `report.html` são objetos
incorporados com `audit_id`, `media_type` e `content`. Essa forma existe apenas
para exercitar compatibilidade e consistência sem arquivos externos, rede ou
armazenamento compartilhado.

## Validações do consumidor DBA

- `contract_version` deve ser exatamente `health_check_alert.v1`.
- `severity` deve pertencer ao conjunto atualmente reconhecido: `info`,
  `warning` ou `critical`.
- `category` deve pertencer ao conjunto inicial reconhecido: `deadlock`,
  `latency` ou `connections`.
- `audit_id` deve ser não vazio e igual nos dois relatórios incorporados.
- `report.json` e `report.html` devem estar presentes e ter o tipo de conteúdo
  correspondente.
- o payload inteiro deve estar livre de campos ou padrões aparentes de segredo.
- `dedupe_key` deve estar presente e não vazio.
- identificadores, texto, data com fuso, ambiente, origem, achados e metadados
  devem ter a forma mínima esperada pelo consumidor.

Aceitar um payload significa somente que ele é compatível com este perfil
local. Não comprova autenticidade, não substitui validação do MCP central e não
autoriza análise, recomendação ou ação automática.
