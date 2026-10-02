# Contrato: evento de auditoria normalizado

Use este contrato para transferir um evento sanitizado do fluxo de Auditoria
para análise de risco. Eventos brutos e segredos não pertencem a este arquivo.

## Campos obrigatórios

| Campo | Tipo | Descrição |
|---|---|---|
| `schema_version` | string | Versão deste contrato |
| `event_id` | string | Identificador estável e não secreto |
| `occurred_at` | string | Timestamp ISO 8601 com fuso |
| `observed_at` | string | Timestamp ISO 8601 da normalização |
| `source` | string | Origem lógica do evento |
| `category` | string | Categoria normalizada |
| `action` | string | Ação observada |
| `outcome` | string | `success`, `failure` ou `unknown` |
| `actor` | object | Identidade sanitizada necessária à análise |
| `object` | object | Objeto afetado, sem dados sensíveis |
| `evidence_ref` | string | Caminho ou ID da evidência autorizada |
| `normalization_notes` | array | Lacunas e transformações aplicadas |

## Regras

- Não incluir senha, token, chave, conteúdo de perfil de login ou SQL com dados
  sensíveis.
- Preservar o evento bruto apenas no repositório autorizado e ignorado pelo
  controle de versão.
- Não inferir sucesso, ator ou objeto quando a origem não os demonstrar; usar
  `unknown` e registrar a lacuna.

