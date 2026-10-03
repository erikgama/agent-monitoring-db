# Agent Monitoring Notification

Entrega segura por e-mail de alertas e conclusoes que o MCP central já recebeu
e validou nos contratos do Health Check, Audit Security ou Refactor. O
Notification revalida o payload, preserva a severidade recebida, usa seu
Advisor para escolher o canal permitido e realiza no máximo uma tentativa.

```text
Health Check   -> MCP central -> Notification -> e-mail
Audit Security -> MCP central -> Notification -> e-mail
Refactor -> MCP central -> DBA + Notification -> e-mail de conclusao
```

## Requisitos

- Python 3.11 ou superior;
- `uv`;
- configuração não secreta fornecida pelo ambiente do operador.

## Instalação

No diretório `agents/notification`:

```sh
uv sync --extra dev
```

## Interface oficial

O MCP constrói o dispatcher pela API pública:

```python
from notification import build_dispatcher

dispatcher = build_dispatcher()
result = dispatcher.dispatch(alert)
```

O Notification não executa um daemon ou servidor MCP próprio.

No fluxo Refactor, o MCP primeiro registra `query_refactor_result.v1` na inbox
do DBA. Somente um resultado novo solicita o aviso; uma entrega duplicada não
gera outro e-mail. O aviso usa `NOTIFICATION_EMAIL_RECIPIENTS_REFACTOR` quando
configurado ou, como fallback, os destinatarios de warning. Nenhuma SQL literal
ou anexo faz parte da mensagem.

## Entrega real e recorrência

O Notification não agenda alertas, não mantém cooldown e não decide quando
repetir um e-mail. A recorrência pertence ao agente que originou o alerta:

- o Health Check publica imediatamente uma nova condição de latência crítica e,
  se ela continuar ativa, permite nova publicação após 120 segundos;
- o Audit publica uma vez por `event_key`; outro DROP ou ALTER bloqueado cria um
  evento diferente e pode ser entregue imediatamente.

Para cada chamada recebida do MCP, o Notification revalida o contrato, escolhe
o canal e os destinatários locais pela severidade recebida e realiza no máximo
uma transação SMTP, sem retry. Destinatários presentes no payload são ignorados.
O Advisor nunca recalcula a severidade definida pelo agente produtor.

Uma entrega integrada real exige `AGENT_MONITORING_NOTIFY=true` ao iniciar o
console, configuração não secreta válida em `.notification.local.env` e a senha
SMTP disponível no Chaves do macOS ou no cofre corporativo via helper Linux.
O launcher define `NOTIFICATION_DELIVERY_ENABLED=true` apenas no processo.
Para Gmail, a política exige porta 587,
STARTTLS, validação de certificado e hostname e remetente igual ao usuário
autenticado. Somente o runtime do Notification resolve a senha pelo serviço
`mysqlconf-notification-smtp` no macOS; Health Check, Audit e MCP não recebem o
segredo. O [guia de instalação](../../docs/SETUP.md) ensina a configurar e
testar cada opção.

## Laboratórios integrados

Os orquestradores na raiz do repositório exercitam a integração real sem
duplicar implementação no Notification:

```sh
python3 apps/lab-console/scripts/run-health-check-lab.py --execute
python3 apps/lab-console/scripts/run-audit-lab.py --execute
python3 apps/lab-console/scripts/run-audit-drop-lab.py --execute
python3 apps/lab-console/scripts/run-audit-alter-lab.py --execute
```

O Health Check executa sua própria carga read-only. No Audit, mantenha
`run-audit-lab.py` ativo antes de disparar os scripts de DROP e ALTER. Esses
comandos habilitam entrega real e, portanto, devem ser usados somente com
autorização explícita do operador. Sem `--execute`, todos mostram ou exercitam
somente o caminho seguro de conferência.

Em 2026-09-17, Health Check e Audit foram executados simultaneamente. O MCP
validou três alertas — `query_latency`, `destructive_ddl` e `schema_change` — e
o Notification retornou `delivery_status=sent` para os três. A inbox do DBA
também confirmou os três registros. Nenhum endereço ou segredo foi persistido
nos relatórios ou no console.

## Teste manual seguro

O teste permanece em `dry_run` por padrão e nunca chama SMTP sem as duas
confirmações exigidas pela política:

```sh
uv run mysql-notification-email-test fixtures/connection-warning.json
```

O entrypoint anterior `notification-email-test` continua compatível.

## Testes

```sh
uv run python -m unittest discover -s tests -v
uv run ruff check .
uv run ruff format --check .
```

A suíte usa SMTP falso e não abre rede nem envia e-mail real.

## Segurança

- não consulta MySQL ou credenciais do banco;
- não decide severidade nem cria alertas;
- ignora destinatários presentes no payload;
- não persiste alertas, anexos, filas ou relatórios recebidos;
- não registra senha, destinatários, payload integral ou erro bruto;
- entrega real permanece desabilitada por padrão.

## Estrutura

- `notification/advisor/`: agente de roteamento e role em português;
- `notification/dispatcher.py`: fluxo principal de validação e entrega;
- `notification/refactor_dispatcher.py`: aviso de conclusão do Refactor;
- `notification/domain.py`: modelos tipados;
- `notification/validation/`: revalidação contratual e de integridade;
- `notification/channels/`: implementação dos canais, hoje somente e-mail;
- `notification/templates/`: templates HTML seguros;
- `contracts/`: cópias locais verificadas dos contratos canônicos;
- `fixtures/`: alertas fictícios;
- `tests/`: testes isolados;
- `docs/`: documentação operacional;
- `reports/`: evidências operacionais locais, ignoradas pelo Git.

Não existe `advisor/results/`: por segurança, este agente não persiste alertas,
payloads, anexos nem decisões de roteamento.

## Documentação avançada

- [Guia operacional completo](docs/operations.md)
- [Configuração de banco, LLM e SMTP](../../docs/SETUP.md)
- [Validação de clone e envio SMTP em VM](../../docs/VALIDATION_VM.md)

Toda configuração detalhada, gates de envio e procedimento do Chaves do macOS
estão no guia operacional. Nenhum segredo deve ser colocado no repositório.
