# Guia operacional do agente Notification

O agente `notification` entrega por e-mail alertas e conclusoes que o MCP
central ja recebeu e validou. Ele revalida `health_check_alert.v1`,
`audit_security_alert.v1` ou `query_refactor_result.v1`, aplica a politica
local de destinatarios, monta uma mensagem segura e retorna um resultado
estruturado.

```text
Health Check   -> MCP central -> notification -> e-mail
Audit Security -> MCP central -> notification -> e-mail
Refactor -> MCP central -> DBA + notification -> e-mail de conclusao
```

Este agente nao implementa servidor nem cliente MCP.
`NotificationDispatcher.dispatch()` e o ponto de entrada Python usado pelo MCP
central depois que `incident_raise` ou `refactor_result_raise` valida o payload.
O Advisor em `notification/advisor/agent.py` preserva a severidade recebida e
aplica a tabela em `notification/advisor/rules.md`.

## Limites

O agente nao acessa MySQL, HeatWave ou credenciais de banco, nao executa SQL,
nao determina saude, nao recalcula severidade e nao se comunica diretamente
com Health Check, Audit, Refactor ou DBA. Ele tambem nao cria tickets, tarefas,
filas ou persistencia. WhatsApp, Slack, Microsoft Teams e SMS sao extensoes
futuras e nao possuem implementacao nesta fase.

O e-mail nao possui anexos. O corpo e gerado pelo template local; `report.json`
e `report.html` continuam sendo revalidados como parte do contrato, mas nao sao
incluidos na mensagem.

## Contrato

As fontes canonicas pertencem aos agentes produtores:

```text
agents/health-check/contracts/health_check_alert.v1.schema.json
agents/audit/contracts/audit_security_alert.v1.schema.json
agents/refactor/contracts/query_refactor_result.v1.schema.json
```

O agente contem copias byte a byte dos tres schemas em `contracts/`.
`provenance.json` registra dono, caminhos e SHA-256 de cada
contrato. `tests/test_contract_sync.py` falha se qualquer copia divergir da
fonte canonica ou da proveniencia.

Antes de politica ou SMTP, o dispatcher revalida schema, formatos, integridade
entre alerta e relatorios e presenca aparente de dados sensiveis. Destinatarios
presentes no payload nunca sao consultados; somente a configuracao local do
operador e usada.

## Configuracao de e-mail

Para preparar um clone, use `python3 scripts/smtp_setup.py init`, configure
`agents/notification/.notification.local.env`, execute
`python3 scripts/smtp_setup.py check` e, com destinatarios autorizados,
`python3 scripts/smtp_setup.py send-test --send`. O ultimo comando envia um
e-mail real. O [passo a passo](../../../docs/SETUP.md) detalha cada etapa.

### Linux e gerenciador de segredos corporativo

No fluxo integrado Linux, configure a referencia nao secreta
`NOTIFICATION_SMTP_CREDENTIAL_HELPER=/caminho/absoluto/do/helper` no arquivo
local de Notification. O helper e provisionado pelo operador e deve aceitar
`--account USUARIO_SMTP`, consultar seu gerenciador de segredos e devolver
uma unica linha com a senha em stdout, com codigo de saida zero. Nao coloque
a senha no helper ou no arquivo de configuracao. O runtime Notification chama
o executavel sem shell, com timeout de cinco segundos, captura a resposta
somente em memoria e nunca publica stdout/stderr. Falhas retornam apenas
`smtp_credential_lookup_failed`. Sem entrega habilitada, o helper nao e chamado.

Esta referencia permite usar o gerenciador corporativo de cada instalacao.
O projeto nao instala nem presume um cofre especifico. A injecao direta de
`SMTP_PASSWORD` continua disponivel para o processo isolado de Notification.

Toda configuracao vem do ambiente do processo. Nenhum valor real ou sensivel e
versionado. O arquivo `.env.example` contem somente placeholders ficticios e
serve como referencia; o agente nao cria nem carrega um `.env` real.

| Variavel | Padrao | Uso |
|---|---:|---|
| `NOTIFICATION_DELIVERY_ENABLED` | `false` | Habilita a unica tentativa SMTP |
| `NOTIFICATION_EMAIL_FROM` | vazio | Remetente controlado pelo operador |
| `NOTIFICATION_EMAIL_RECIPIENTS_WARNING` | vazio | Lista separada por virgulas |
| `NOTIFICATION_EMAIL_RECIPIENTS_CRITICAL` | vazio | Lista separada por virgulas |
| `NOTIFICATION_EMAIL_RECIPIENTS_REFACTOR` | vazio | Lista do aviso de conclusao; quando vazia usa WARNING |
| `SMTP_HOST` | vazio | Servidor SMTP |
| `SMTP_PORT` | `587` | Porta SMTP |
| `SMTP_USERNAME` | vazio | Usuario SMTP opcional |
| `SMTP_PASSWORD` | vazio | Senha SMTP opcional; nunca registrada. Se ausente no fluxo MCP local, o proprio Notification consulta o Chaves |
| `NOTIFICATION_SMTP_KEYCHAIN_SERVICE` | `mysqlconf-notification-smtp` | Referencia fixa do Chaves; outro valor e rejeitado |
| `SMTP_USE_STARTTLS` | `true` | Solicita STARTTLS antes de autenticar |

`SMTP_USERNAME` e `SMTP_PASSWORD` devem ser configurados juntos quando o
servidor exigir autenticacao. Quando existe usuario autenticado,
`NOTIFICATION_EMAIL_FROM` deve ser exatamente a mesma conta, sem alias ou nome
de exibicao. Com entrega habilitada, remetente e host sao obrigatorios. Valores
booleanos aceitos: `true/false`, `1/0`, `yes/no` e `on/off`.

### Gmail SMTP

Para Gmail ou Google Workspace nesta fase, a configuracao aceita e:

```text
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USE_STARTTLS=true
SMTP_USERNAME=<endereco Gmail completo>
NOTIFICATION_EMAIL_FROM=<o mesmo endereco Gmail completo>
SMTP_PASSWORD=<senha de app injetada pelo ambiente seguro do operador>
```

Referencia oficial: [Enviar e-mail de um dispositivo ou
app](https://support.google.com/a/answer/176600).

O validador rejeita Gmail com outra porta, STARTTLS desabilitado, credenciais
incompletas ou remetente diferente do usuario autenticado. A conexao possui
timeout explicito de 10 segundos. STARTTLS usa o armazenamento de autoridades
certificadoras do sistema, validacao da cadeia e verificacao do hostname por
meio de `ssl.create_default_context()`.

A senha de app deve ser provisionada no ambiente do processo por mecanismo
seguro do operador. Nao a coloque no comando, `.env.example`, README, arquivos
de shell versionados ou historico do terminal.

Uma instalacao local pode manter os valores nao secretos em
`.notification.local.env`, que e ignorado pelo Git e lido diretamente pelos
launchers Python integrados. O formato e estrito, com uma atribuicao
`CHAVE=VALOR` por linha. O arquivo deve manter
`NOTIFICATION_DELIVERY_ENABLED=false`; a senha SMTP nao deve ser colocada nele.

### Autenticacao local atual (macOS)

Em 2026-09-15, os testes reais locais usam uma senha de app do Gmail armazenada
como senha generica no Chaves do macOS. A referencia possui:

- servico: `mysqlconf-notification-smtp`;
- conta: o mesmo endereco definido em `SMTP_USERNAME`;
- segredo: armazenado somente no Chaves e nunca documentado ou versionado.

O fluxo MCP local de autenticacao e:

1. O launcher Python carrega `.notification.local.env`, que contem apenas host,
   porta, usuario, remetente, destinatarios e STARTTLS.
2. Audit envia ao MCP somente o alerta validado, sem credenciais.
3. O MCP chama a fabrica publica do Notification, sem ler nem receber a senha.
4. Somente o runtime do Notification executa o utilitario `security` do macOS
   e recupera o segredo pelo servico fixo `mysqlconf-notification-smtp` e pela
   conta definida em `SMTP_USERNAME`. O segredo permanece somente em memoria,
   nao e impresso nem salvo em arquivo.
5. `EmailChannel` abre `smtp.gmail.com:587` com timeout de 10 segundos, chama
   STARTTLS com validacao de cadeia e hostname e entao autentica via SMTP.
6. O remetente deve ser exatamente igual ao usuario autenticado.

Portanto, no fluxo de producao local, a fronteira e estrita:

```text
Audit -> MCP incident_raise -> Notification -> Chaves do macOS -> SMTP
```

Nem Audit nem MCP executam `security`, recebem `SMTP_PASSWORD` ou conhecem o
segredo. A referencia do servico nao pode ser redirecionada por configuracao.

O comando manual isolado continua podendo receber a configuracao somente pelo
ambiente efemero do processo. Para o fluxo integrado, prefira os launchers
Python, que carregam o arquivo local sem executar shell:

```sh
python3 apps/lab-console/scripts/run-health-check-lab.py --execute
python3 apps/lab-console/scripts/run-audit-lab.py --execute
```

O envio real continua exigindo simultaneamente
`NOTIFICATION_DELIVERY_ENABLED=true` e `--send`. Sem ambos, o resultado e
`dry_run` e SMTP nao e chamado. O Chaves pode solicitar autorizacao interativa
ao usuario do macOS. Esse mecanismo e exclusivo do ambiente local atual; uma
implantacao de servico deve usar o gerenciador de segredos corporativo e
injecao de segredo em runtime.

O registro operacional detalhado esta em
[`2026-09-15-smtp-authentication.md`](../reports/2026-09-15-smtp-authentication.md).

## Politica inicial

- `info`: nao envia e retorna `suppressed_by_policy`.
- `warning`: usa apenas `NOTIFICATION_EMAIL_RECIPIENTS_WARNING`.
- `critical`: usa apenas `NOTIFICATION_EMAIL_RECIPIENTS_CRITICAL` e adiciona
  os cabecalhos de prioridade alta suportados por e-mail.

Se a lista correspondente estiver vazia, o resultado e
`recipients_not_configured` e SMTP nao e chamado. Nao ha repeticao automatica:
cada `dispatch()` faz no maximo uma tentativa.

## Demo do alerta de latencia

A fixture segura `fixtures/query-latency-critical.json` representa o problema
atual da query `actor_popularity`: P99 estimado de `3.162278 s` diante do limite
de `2.0 s`, na janela completa de 120 segundos. Todos os IDs, horarios e dados
da fixture sao ficticios.

O e-mail produzido para esse alerta mostra de forma legivel:

- query e schema monitorados;
- P99 observado e limite configurado;
- janela, execucoes, amostras e cobertura;
- digest e SELECT normalizado;
- `alert_id` e `audit_id` para rastreabilidade;
- nenhum anexo no e-mail.

O template continua generico: novas regras podem fornecer outras evidencias no
finding sem exigir um template exclusivo por query. Valores dinamicos sao
escapados e a mensagem nao inventa diagnostico nem acao corretiva.

## Alerta do Audit Security

A fixture ficticia `fixtures/audit-destructive-ddl-critical.json` representa
uma tentativa de `drop_table` em `sakila` negada pelo MySQL com codigo 1142.
O template dedicado `audit_security_email.html` mostra somente regra, comando
normalizado, resultado, codigo MySQL, schema, evidencia de escopo, IDs e chave
do evento. Campos de identidade pseudonimizados que existam no finding nao sao
exibidos no corpo. SQL literal, credenciais, host/IP, login-path e destinatarios
do payload nunca entram na mensagem.

O Notification nao anexa o relatorio recebido do Audit. O corpo do e-mail usa
somente a allowlist sanitizada do proprio envelope. Notification nao persiste
o alerta nem chama o DBA; o fan-out e responsabilidade do MCP.

O segundo contexto suportado e `schema_change`: uma tentativa de
`ALTER TABLE` em `sakila` que o MySQL negou por permissao. Ele usa a metrica
`blocked_schema_change_attempt`, exibe o comando normalizado `alter_table` e
mantem a mesma politica de seguranca e corpo sanitizado. A classificacao e
recebida pronta do Audit; Notification nao decide nem recalcula a regra.

## Teste manual seguro

A entrega inicia desabilitada. O comando manual aceita somente o caminho de uma
fixture `warning` ou `critical` e a flag opcional `--send`; nao existe argumento
de senha nem de destinatario. Remetente, destinatarios e SMTP sempre vem das
variaveis locais controladas pelo operador.

Sem `--send`, o comando forca dry-run mesmo que a variavel de entrega esteja
habilitada e possui uma guarda que impede qualquer chamada SMTP:

```sh
cd agents/notification
uv run mysql-notification-email-test fixtures/connection-warning.json
```

Saida esperada:

```json
{"alert_id":"cccccccc-cccc-4ccc-8ccc-cccccccccccc","channel":"email","delivered":false,"recipient_count":1,"status":"dry_run"}
```

Para validar especificamente o e-mail da demo de P99, ainda sem SMTP:

```sh
cd agents/notification
uv run mysql-notification-email-test fixtures/query-latency-critical.json
```

O primeiro envio real exige as duas confirmacoes simultaneas:

1. `NOTIFICATION_DELIVERY_ENABLED=true` ja provisionado no ambiente.
2. `--send` informado manualmente no comando.

Com todas as variaveis, inclusive o segredo, previamente injetadas pelo
mecanismo seguro do operador, o comando exato e:

```sh
cd agents/notification
uv run mysql-notification-email-test fixtures/connection-warning.json --send
```

Sem qualquer uma das duas confirmacoes nao existe tentativa SMTP. O comando
faz no maximo uma tentativa e nunca aceita senha ou destinatarios pela linha de
comando.

## Conteudo e resultados

O assunto segue o formato
`[SEVERITY][MySQL][category] ambiente - titulo`. O corpo HTML escapa todo
conteudo dinamico e inclui severidade, ambiente, categoria, titulo, resumo,
horario, `alert_id`, `audit_id`, findings e o contexto estruturado de cada
finding. O template usa uma faixa visual por severidade, detalhes alinhados,
categoria legivel, horario local de Sao Paulo junto do valor ISO original,
destaque para valor observado e limite e rodape que declara que a confirmacao
de recebimento ainda nao e rastreada. A mensagem nao inclui anexos. JSON e HTML
permanecem no contrato para validacao e rastreabilidade do MCP/DBA, mas nao sao
copiados para o e-mail.

Sucesso retorna `delivered`, `alert_id`, `channel`, `status` e
`recipient_count`. Falhas retornam somente um `error_code` estavel; valores de
configuracao, destinatarios, excecoes brutas e relatorios nao aparecem no
resultado ou nos logs.

## Desenvolvimento e testes

Requer Python 3.11 ou superior e `uv`:

```sh
cd agents/notification
uv sync --extra dev
uv run python -m unittest discover -s tests -v
uv run ruff check .
uv run ruff format --check .
```

A suite usa SMTP inteiramente falso. Nenhum teste abre rede ou envia e-mail
real.
