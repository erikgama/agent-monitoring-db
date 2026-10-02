# Notification: instrucoes operacionais

## Contexto permitido

- Leia este `AGENTS.md`, `../../AGENTS.md`, `../../MEMORY.md` e
  `../../docs/handoffs.md` antes de atuar.
- Trabalhe somente nesta pasta. As unicas fontes externas que podem ser lidas
  para validar compatibilidade contratual sao
  `../health-check/contracts/health_check_alert.v1.schema.json` e
  `../audit/contracts/audit_security_alert.v1.schema.json` e
  `../refactor/contracts/query_refactor_result.v1.schema.json`.
- Nao leia credenciais, perfis de conexao, chaves privadas, caches, logs ou
  relatorios de outros agentes.

## Missao

Receber do MCP central um alerta ou resultado ja validado nos contratos
`health_check_alert.v1`, `audit_security_alert.v1` ou
`query_refactor_result.v1`, revalidar o payload e usar o Advisor para rotear a
severidade recebida ao canal permitido pela politica local.

Fluxo obrigatorio:

```text
Health Check   -> MCP central -> notification -> e-mail
Audit Security -> MCP central -> notification -> e-mail
Refactor -> MCP central -> DBA + notification -> e-mail de conclusao
```

## Limites absolutos

- Nao consultar MySQL ou HeatWave, nem acessar credenciais do banco.
- Nao decidir se existe problema de saude e nao recalcular severidade.
- Nao executar SQL, alterar banco ou infraestrutura, nem propor correcoes.
- Nao se comunicar diretamente com Health Check ou DBA.
- Nao se comunicar diretamente com Refactor; o aviso parte somente do MCP
  depois que a entrega ao DBA foi registrada.
- Nao criar ticket, tarefa, fila ou persistencia de alertas/anexos.
- Nao implementar MCP server ou MCP client.
- Nao implementar WhatsApp, Slack, Microsoft Teams ou SMS nesta fase.
- Nao aceitar destinatarios fornecidos pelo alerta; usar somente configuracao
  local controlada pelo operador.
- Nao repetir tentativas de entrega. Cada chamada realiza no maximo uma
  transacao SMTP.

## Contrato e seguranca

- O Health Check e dono de `health_check_alert.v1`; o Audit Security e dono de
  `audit_security_alert.v1`; o Refactor e dono de
  `query_refactor_result.v1`.
- O e-mail de conclusao do Refactor nao pode conter SQL original, SQL proposta,
  hashes de resultado ou anexos. Deve informar apenas identificador da query,
  status, resumo da mudanca, tempos, percentual, equivalencia e referencia.
- As copias empacotadas devem permanecer byte a byte identicas as fontes canonicas. O
  teste de sincronismo e o SHA-256 da proveniencia devem falhar diante de
  divergencia.
- `NOTIFICATION_DELIVERY_ENABLED` inicia desabilitado; nesse modo nao pode
  existir chamada SMTP.
- O teste manual so pode enviar quando `--send` e
  `NOTIFICATION_DELIVERY_ENABLED=true` estiverem presentes ao mesmo tempo. Ele
  nao aceita senha ou destinatarios por argumento.
- Para `smtp.gmail.com`, exigir porta 587, STARTTLS, credenciais completas e
  remetente identico ao usuario autenticado.
- Quando o MCP habilita uma entrega real e `SMTP_PASSWORD` nao foi injetada,
  somente o runtime do Notification pode resolver a senha no Chaves do macOS,
  pelo servico fixo `mysqlconf-notification-smtp` e pela conta de
  `SMTP_USERNAME`. Audit e MCP nunca leem nem recebem essa senha.
- Toda conexao SMTP deve possuir timeout explicito. STARTTLS deve validar cadeia
  de certificados e hostname com as autoridades confiaveis do sistema.
- Nunca registrar senha, token, destinatarios, JSON/HTML integral ou excecao
  bruta de transporte.
- Logs podem conter somente `alert_id`, `audit_id`, severidade, categoria,
  canal, status e codigo de falha.

## Estrutura

- `notification/advisor/agent.py`: roteamento deterministico da severidade ja
  validada, sem reclassificacao.
- `notification/advisor/rules.md`: role completa em portugues e tabela de
  canais permitidos.
- `notification/dispatcher.py`: revalidacao, decisao do Advisor e entrega.
- `notification/refactor_dispatcher.py`: aviso de conclusao do Refactor.
- `notification/domain.py`: resultados e decisoes tipadas.
- `notification/validation/`: revalidacao contratual e de integridade.
- `notification/channels/`: interface de canal e implementacao de e-mail.
- `notification/templates/`: corpos HTML proprios e seguros.
- `contracts/`: copias locais verificadas dos tres contratos canonicos.
- `tests/`: testes isolados com SMTP falso.
- `fixtures/`: alertas exclusivamente ficticios para teste e demonstracao.
- `docs/`: operacao detalhada e limites do canal.
- `reports/`: evidencias historicas sanitizadas de validacao.

A interface oficial em runtime e `notification.build_dispatcher()`, chamada
somente pelo MCP central. O comando manual oficial e:

```sh
uv run mysql-notification-email-test fixtures/connection-warning.json
```

O entrypoint anterior `notification-email-test` permanece compativel. O agente
nao possui daemon proprio, `advisor/results/`, cache latest-only, fila, logs
persistidos ou implementacao paralela. Essa ausencia e intencional: Notification
nao persiste payloads nem decisoes de entrega.

## Validacao

```sh
uv sync --extra dev
uv run python -m unittest discover -s tests -v
uv run ruff check .
uv run ruff format --check .
```
