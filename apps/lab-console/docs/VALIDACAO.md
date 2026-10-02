# Evidência de validação · 24/09/2026

## Resultado

A revisão final validou separadamente código, contratos, interface e operações
reais permitidas. O Lab Console foi testado em build de produção; os cenários
visuais usaram um runtime demo descartável, enquanto as coletas MySQL foram
executadas pelos entrypoints oficiais em modo somente leitura.

| Componente | Resultado |
| --- | --- |
| Health Check | 69 testes aprovados, 2 integrações stdio aprovadas em execução separada |
| Audit | 23 testes aprovados |
| Refactor | 6 testes aprovados |
| Notification | 43 testes aprovados |
| MCP central | 45 testes aprovados |
| DBA | 26 testes gerais e 7 testes da inbox Audit aprovados |
| API | 54 aprovados, 1 skip explícito e 2 avisos de dependências |
| Web | ESLint, Prettier, TypeScript e build Next.js aprovados |
| E2E Chrome | 7 cenários aprovados em 30,6 segundos |

Ruff e sua verificação de formatação passaram em Health Check, Audit,
Refactor, Notification, MCP, DBA e API. O mypy da API não encontrou erros.

## Validação operacional real

- `mysql-health-latency collect` criou um novo `latest.json` e `latest.html`
  com status `available`; não houve SELECT concluída na janela observada.
- `mysql-health-check collect` concluiu e atualizou o relatório geral. O
  estado técnico observado foi `critical` por evidências atuais dos domínios
  `errors` e `workload`; isso é resultado da coleta, não falha do programa.
- `mysql-audit-security collect` concluiu com `collection_status=complete`.
- `mysql-refactor-advisor run-once` respondeu `waiting`, sem solicitação
  pendente; nenhum SQL precisou ser reexecutado.
- A integração Health Check -> MCP -> Notification em dry-run atravessou o
  transporte stdio real em dois testes.
- O teste SMTP controlado entregou uma mensagem real pelo canal configurado:
  `status=sent`, `delivered=true` e dois destinatários. Nenhum endereço ou
  segredo foi impresso.
- A simulação SELECT completou o aquecimento de 60 segundos a 1 TPS, com 60
  consultas concluídas, zero falhas e zero rejeições. Em seguida iniciou a
  carga de latência e a query versionada do Slow Query Log em paralelo; o
  cancelamento encerrou os dois processos corretamente.

DROP e ALTER reais não foram executados nesta revisão. Os wrappers foram
validados por dry-run, testes de segurança e testes E2E em modo demo. A regra
global exige autorização específica e uma tarefa separada para qualquer DDL,
mesmo quando se espera que o MySQL negue a tentativa.

## Cobertura E2E

Os sete cenários no Chrome verificaram:

- ativação e parada independentes de Health Check e Audit;
- bloqueio das simulações até o respectivo observador estar ativo;
- fluxo fictício de três alertas e isolamento dos artefatos;
- menu e central do DBA;
- navegação por teclado, reduced motion e axe WCAG A/AA/2.1 AA;
- sandbox do HTML, incluindo bloqueio de script e recurso remoto;
- estado explícito quando um artefato não está disponível.

A suíte deve ser executada somente em modo demo e com runtime vazio. Ela cria
estado encadeado: o primeiro cenário gera três artefatos fictícios usados pelos
testes seguintes. Nunca aponte `LAB_E2E_URL` para o modo integrado.

## Observações

Os dois avisos da API vêm de deprecações em `Starlette TestClient/httpx` e no
alias `anyio.abc.BlockingPortal`; não foram ocultados. O skip da API permanece
explícito e não pertence aos fluxos dos agentes.

As tentativas iniciais do Playwright sem navegador próprio falharam antes dos
testes. A execução válida usou o Chrome instalado, fora do sandbox, contra o
runtime demo isolado. Traces, relatório HTML e screenshots gerados durante a
validação são temporários e não fazem parte do código-fonte.

## Regressão de concorrência · 25/09/2026

Após uma execução integrada interromper simultaneamente Health Check, carga
SELECT, Refactor e Audit, o histórico do controle mostrou que todos os jobs
foram marcados `runner_disconnected` exatamente após a soma do timeout do
WebSocket com a tolerância de reconexão. A causa no runner era a enumeração
síncrona e frequente das árvores de processos no mesmo event loop responsável
pelo heartbeat.

O rastreamento passou a executar fora do event loop, serializado e com intervalo
de um segundo. A regressão automatizada inicia simultaneamente `health.lab`,
`health.load`, `refactor.lab`, `audit.lab`, `audit.drop` e `audit.alter`, força
uma enumeração lenta e confirma que o heartbeat continua responsivo, DROP e
ALTER terminam e os quatro jobs duradouros permanecem ativos.

Os wrappers protegidos de DROP e ALTER também deixaram de usar o limite interno
de 20 segundos, insuficiente durante a SELECT lenta. A conexão permanece
limitada a 15 segundos, a tentativa conectada tem limite de 300 segundos e não
há repetição automática de DDL. A suíte da API concluiu com 63 testes aprovados,
1 skip explícito e os mesmos 2 avisos de dependências; Ruff e mypy passaram nos
arquivos atingidos. Esta regressão não executou MySQL, MCP ou SMTP reais.

## Validação integrada real de concorrência · 25/09/2026

Com autorização explícita do operador, a correção foi exercitada contra o fluxo
integrado real na ordem solicitada:

1. Health Check foi ativado e chegou a `ready`; `select_latency` e
   `refactor_collector` publicaram coletas novas.
2. A carga SELECT permaneceu ativa por quatro minutos completos, com Health
   `ready`, carga `running`, relatórios avançando e runner online.
3. Refactor foi ativado; após 20 segundos, Audit foi ativado e chegou a `ready`.
4. ALTER e DROP foram disparados em paralelo, com 75 ms entre as solicitações.
   Ambos terminaram em cerca de dois segundos como negações esperadas, sem
   interromper os quatro jobs duradouros.

O Audit publicou um alerta `schema_change` e um `destructive_ddl`; ambos foram
validados pelo MCP, entregues pelo Notification e registrados no DBA. Nenhuma
alteração foi aplicada no banco. O Health observou P99 de `3.019952 s`, decidiu
`alert` e suprimiu a nova publicação por cooldown ativo, como previsto.

O Refactor concluiu a solicitação `correlated_running_total` em `sakila_dev`
com equivalência confirmada para 16.044 linhas. A execução original levou
`239.223510 s`, a proposta `4.662108 s`, com melhoria medida de `98.051%` e
speedup de `51.312x`. O resultado foi registrado no DBA e o aviso de conclusão
foi enviado pelo Notification. Nenhuma mudança foi aplicada em `sakila`.

A execução revelou um defeito apenas visual: a timeline inferia as duas
categorias Audit como `destructive_ddl`, embora os contratos e a inbox do DBA
estivessem corretos. O parser passou a reconhecer `schema_change` e
`destructive_ddl` na chave de deduplicação canônica e a preservar o audit ID.
A regressão específica e a suíte completa passaram. Os jobs foram cancelados
de forma limpa; depois do restart final, o app ficou integrado, com runner
online e sem jobs ativos.
