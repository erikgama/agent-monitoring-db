# Validação de instalação e portabilidade

Validação inicial executada em **2 de outubro de 2026**, a partir de um clone do GitHub.
Código dessa primeira rodada: [`610be18`](https://github.com/erikgama/agent-monitoring-db/commit/610be18).
O repositório foi publicado como privado; quem clonar precisa de acesso ao GitHub.

## Revisão de publicação em 3 de outubro de 2026

O clone da VM foi atualizado por fast-forward até
[`151f1d6`](https://github.com/erikgama/agent-monitoring-db/commit/151f1d6),
sem alterações locais. `scripts/check.py` passou na VM, incluindo testes Python,
Ruff, tipos e build Web. `doctor` confirmou as dependências; `db-check` para
Health Check e Refactor confirmou TLS, porta e os schemas `sakila` e
`sakila_dev` em transações somente leitura. `llm-check` fez uma chamada real
bem-sucedida com Codex. O [CI desse commit](https://github.com/erikgama/agent-monitoring-db/actions/runs/37152130546)
também passou, incluindo navegador em demo isolada.

Nesta revisão não houve novo envio SMTP. O `smtp_setup.py check` retornou
`smtp_credential_helper_required` na VM: o helper temporário da validação
anterior foi removido, e o envio contínuo no Linux depende da integração com o
cofre corporativo descrita em [`SETUP.md`](SETUP.md). Claude Code e Kimi Code
continuam sem autenticação e inferência reais confirmadas nessa VM.

## VM: Oracle Linux 9

| Etapa | Comando ou evidência | Resultado |
| --- | --- | --- |
| Clone | `git clone` com deploy key de leitura | Commit do GitHub instalado numa pasta nova |
| Bootstrap | `python3 scripts/bootstrap.py` | Nove ambientes Python e Web instalados dos lockfiles |
| Configuração | `agent-monitoring init` | TOML local criado; exemplos não contêm segredos |
| Testes Python | `python3 scripts/check.py` | **316 testes passaram**, sem skips na VM |
| Estilo e tipos | Mesmo comando | Ruff, formatação Python, mypy e TypeScript passaram |
| Web | Mesmo comando | Lint e build Next.js de produção passaram |
| Navegador | `python3 scripts/e2e.py` | **7 cenários Chromium passaram** com runtime e build isolados; Git permaneceu limpo |
| Dependências Web | `npm audit` | Nenhuma vulnerabilidade reportada após atualizar Next.js para 16.3.6 |
| Codex | `codex login status` e `agent-monitoring llm-check` | Login ChatGPT e chamada estruturada reais confirmados |
| Advisors | `scripts/check-llm.py --execute` | Health Check, Audit, Slow Query e proposta Refactor passaram |
| DBA | Mesmo script, checks `dba-summary` e `dba-chat` | Resumo e chat passaram com o adapter real |
| MCP | Testes stdio Health Check → MCP → DBA + Notification | Contratos aceitos, inbox temporário gravado e entrega `dry_run` |
| Codex + MCP | Registro pelo CLI e descoberta pelo próprio Codex | Os três tools `mcp__agent_monitoring__*` ficaram disponíveis |

Os seis checks LLM usam **fixtures sintéticas**, clientes/modelos reais e
validação local da resposta. Não consultam MySQL nem publicam propostas.
A proposta Refactor não constitui comprovação de equivalência ou desempenho SQL.

Os cenários de navegador verificam ciclo de vida dos agentes, cancelamento,
três alertas simulados, catálogo de evidências, separação dos relatórios,
layout em tablet, acessibilidade e bloqueio de scripts/recursos externos por
sandbox e CSP. Chromium em Oracle Linux usa o build de compatibilidade do
Playwright; as bibliotecas necessárias estão em `SETUP.md`.

A suíte reduz o tempo simulado do modo demo e permite dez segundos por
asserção assíncrona, considerando a atualização alternativa da tela a cada
três segundos. Cada cenário confirma modo demo e encerra jobs simulados que
possam ter sobrado do cenário anterior. O ritmo do console normal não mudou.

## GitHub Actions

O mesmo bootstrap, `check.py` e os sete cenários de navegador passaram no CI:
[execução validada](https://github.com/erikgama/agent-monitoring-db/actions/runs/37007295426).
O workflow é `.github/workflows/ci.yml` e roda em cada push/PR, sem banco,
SMTP ou autenticação LLM reais.

## Banco real: situação verificada

Na máquina de origem, usando apenas a referência do perfil já configurado:

| Verificação | Resultado |
| --- | --- |
| `agent-monitoring db-check` | Login, `sakila`, porta e TLS confirmados em transação somente leitura |
| `agent-monitoring db-check --role refactor` | Perfil Refactor, `sakila_dev`, porta e TLS confirmados em transação somente leitura |
| Health Check `collect` | Coleta executada; o relatório observou estado `critical` |
| Audit `collect` | Coleta executada com `collection_status=complete` |

Sucesso de instalação/coleta não significa que o banco esteja saudável.
Os resultados operacionais reais permanecem nos `results/` de seus agentes,
ignorados pelo Git.

**A validação MySQL real na VM foi concluída em 2 de outubro de 2026.** Os
perfis de monitoramento e Refactor foram cadastrados pelo prompt do cliente
MySQL, fora do clone. O arquivo de login local possui permissões `0600`;
o TOML contém somente sua referência. Host, usuário e senha não foram
incluídos neste relatório ou no Git.

| Verificação na VM | Resultado |
| --- | --- |
| `agent-monitoring doctor` | Todas as referências e dependências necessárias disponíveis |
| `agent-monitoring db-check` | Login, `sakila`, porta e TLS confirmados; transação somente leitura |
| `agent-monitoring db-check --role refactor` | Login, `sakila_dev`, porta e TLS confirmados; transação somente leitura |
| Health Check `collect` | Relatório real coletado às `12:17:03.074Z`; estado `critical` no domínio de erros |
| Audit `collect` | Coleta real às `12:17:03.379Z`, `complete`; todos os dez domínios disponíveis |
| `mysql-health-latency collect` | Fontes disponíveis; nenhuma execução do digest monitorado na janela observada |
| `mysql-health-refactor-collector` | Coleta `healthy`, sem queries candidatas retornadas |
| Advisors Health Check e Audit | Codex interpretou os HTMLs reais e devolveu `no_alert`; schema validado, sem publicação |
| DBA `actor_count` e `film_count` | As duas consultas SELECT oficiais passaram usando o perfil central |
| API integrada e runner | Conexão real confirmada em runtime temporário; execução desabilitada e nenhum job iniciado |
| Integridade do clone | Git limpo após configurar perfis, coletar e testar |

O achado Health Check `errors.accumulated` usa contadores desde o último
reset/restart; não demonstra taxa atual de erros. O `no_alert` do advisor
Health Check se refere à regra de latência do digest monitorado, não ao estado
geral do banco. Sem candidatas no Slow Query Log, esta etapa não comprova
equivalência ou desempenho de uma refatoração real.

Os JSON/HTML e registros dos dois checks LLM reais permanecem nos `results/`
dos próprios agentes, ignorados pelo Git. As contagens DBA foram verificadas
sem publicar os valores retornados. Para repetir, siga as etapas 7 e 8 de
`SETUP.md` com seus próprios perfis locais.

O bootstrap não provisiona schemas, contas ou privilégios. DDL, DML, cargas
deliberadas e e-mail real não foram executados na validação inicial. O teste
operacional autorizado posteriormente está descrito abaixo.

## Teste integrado pelo navegador: SELECTs, Refactor e e-mail

Executado em **2 de outubro de 2026**, após autorização explícita do operador
para ativar Health Check, Audit e Refactor e iniciar a simulação de SELECTs.
A Web e a API integradas ficaram em loopback na VM, acessadas pelo navegador
por SSH. O endereço público continua servindo o modo demonstração.

1. O navegador confirmou **INTEGRADO** e **Runner conectado**. Health Check,
   Audit e Refactor foram ativados, cada um com a confirmação `sakila`.
2. `Iniciar consultas` executou o aquecimento de um minuto e os dois componentes
   oficiais: carga de latência e query versionada para o Slow Query Log.
3. Codex decidiu o alerta P99; o MCP validou o contrato, registrou a evidência
   no DBA e Notification enviou e-mail real. O recebimento na caixa Oracle
   configurada foi confirmado às **10:24:21**, horário de São Paulo.
4. A query conhecida `correlated_running_total` registrou **424 segundos** e
   originou uma única solicitação deduplicada para o Refactor. O worker ativo
   recebeu o pedido pelo MCP e usou o provedor configurado.
5. No laboratório `sakila_dev`, original e proposta retornaram **16.044 linhas**
   com equivalência confirmada. Tempos da execução serial: **91,375041 s** e
   **0,164595 s**. O resultado `approved_lab` foi registrado no DBA pelo MCP;
   o e-mail de conclusão foi recebido na caixa Oracle às **10:27:35**.
6. A interface confirmou o cancelamento dos SELECTs. Uma consulta read-only
   posterior confirmou **zero consultas ativas em `sakila`**. Os monitores e o
   worker foram encerrados após o teste.

O ganho medido é restrito à execução controlada em `sakila_dev`; não demonstra
ganho em produção. Audit foi ativado e coletou evidências, mas este teste não
executou DROP/ALTER nem gerou alertas Audit artificiais. A carga foi cancelada
após comprovar o fluxo e não constitui benchmark final de TPS.

SMTP aceitou os envios para os dois destinatários já configurados; o recebimento
foi verificado diretamente na caixa Oracle conectada. Também passou uma entrega
manual com fixture fictícia. A senha foi resolvida exclusivamente pelo runtime
Notification no gerenciador de segredos existente, através de um helper
temporário e transporte SSH; nenhum segredo foi gravado no clone. Para operação
Linux independente deste computador, configure um helper permanente do seu
gerenciador de segredos conforme a etapa 9 de `SETUP.md`.

Correções encontradas nesta execução:

- O runner preserva a opção explícita `AGENT_MONITORING_NOTIFY` para os
  executores oficiais, mantendo senhas SMTP e chave do runner fora dos filhos.
- Health Check, Audit e Refactor repassam somente a referência não secreta de
  `NOTIFICATION_SMTP_CREDENTIAL_HELPER` ao MCP.
- Health Check publica os resultados detalhados do roteamento para que a
  timeline mostre alerta, MCP, e-mail, registro DBA e cooldown.
- A biblioteca inclui `result.json` dentro dos jobs Refactor, com IDs reais de
  resultado/solicitação, timestamp de conclusão e retenção histórica. O JSON
  preserva os controles de hash, caminhos, symlinks e filtragem de segredos.

Relatórios operacionais, SQLs do Refactor, inboxes, capturas de tela e a
configuração SMTP local permanecem ignorados pelo Git. O passo a passo de
instalação continua em `SETUP.md`; SMTP e workloads reais exigem autorização
operacional separada da instalação.

## Claude Code e Kimi Code

Os binários e launchers MCP foram instalados/gerados na VM. Os adapters dos
três provedores têm testes de parsing, schema e erros sanitizados.
**Somente Codex teve autenticação e inferência reais confirmadas nesta VM.**
Para outro provedor, autentique sua própria conta, selecione-o no TOML e rode
`llm-check` e `scripts/check-llm.py --execute`. Guia: `LLM_CLIENTS.md`.

## Conteúdo publicado

Código, regras, contratos, templates, documentação, testes e lockfiles.
Configuração local, perfis MySQL, chaves, autenticação LLM, logs e evidências
operacionais foram excluídos. Gitleaks não encontrou segredos nos arquivos
preparados para publicação.

Para repetir toda a instalação e comparar resultados, siga
[`SETUP.md`](SETUP.md), etapa por etapa. Cada etapa descreve a saída esperada.

## Revisão dos arquivos publicados e do painel público

Uma revisão posterior percorreu todo o histórico Git com Gitleaks, sem detectar
segredos. Nenhum perfil MySQL, configuração local, resultado operacional ou
arquivo `DATABASE_ACCESS.md` aparece no histórico publicado. Os endereços de
e-mail versionados usam apenas domínios de exemplo; os endereços IP fora de
loopback aparecem somente em fixtures de teste privadas/reservadas.

Foi encontrada uma rota do modo demo que ainda listava entregas reais do
Refactor, incluindo SQL de laboratório, a partir dos resultados locais da VM.
O painel público foi interrompido durante a correção. O modo demo agora devolve
lista vazia para essa rota, e a fábrica pública bloqueia toda a navegação pelos
arquivos do clone. Pela porta Web pública, foram confirmados `mode=demo`, zero
entregas reais e resposta 404 para os recursos de código. A API permanece
somente em loopback. Não havia logs de acesso disponíveis para determinar se a
rota antiga foi consultada por terceiros.

Na VM, os dois arquivos locais de configuração foram restringidos a `0600` e os
diretórios de resultados operacionais e seus arquivos a `0700`/`0600`. O serviço
integrado de teste permanece encerrado; o serviço público demo está ativo.

## Revalidação do clone em 3 de outubro de 2026

Na VM Oracle Linux, `scripts/check.py` passou com **319 testes Python**, Ruff,
mypy, lint, tipos e build Web. Em seguida, `scripts/e2e.py` passou nos **7
cenários Chromium**. `agent-monitoring llm-check` e as **6 chamadas reais** de
`scripts/check-llm.py --execute` passaram com Codex e entradas sintéticas.
Essas chamadas não acessam o banco nem publicam alertas.

Os perfis `health-check`, `refactor` e `workload` passaram em `db-check` com
TLS, schema e porta corretos. Health Check coletou um relatório real às
`14:35:26.497Z`, com nove domínios disponíveis e estado geral `critical`;
Audit coletou às `14:35:26.823Z`, com os dez domínios disponíveis e coleta
`complete`. O estado `critical` contém contadores acumulados e não comprova
um incidente recente sem comparação entre snapshots.

A revisão do fluxo de instalação identificou que a SELECT deliberadamente
lenta da simulação usava `monitoring_login_path`. O executor foi corrigido para
`workload_login_path`, como as demais cargas de leitura, e `SETUP.md` passou a
mostrar o cadastro e o `db-check` desse perfil. Um teste isolado confirmou que
referência, ambiente e comando MySQL usam o perfil de carga. O fluxo integrado
com SELECTs, Refactor e e-mail havia passado antes dessa correção; depois dela,
o teste isolado e as verificações sem banco cobrem a alteração de perfil.

A entrega SMTP manual foi aceita na VM na validação anterior. Para e-mail
automático contínuo no Linux, ainda é necessário provisionar o helper do
gerenciador de segredos corporativo descrito na etapa 9 de `SETUP.md`.
Claude Code e Kimi Code continuam sem login e inferência reais nesta VM; os
adapters foram verificados com respostas simuladas.

## Entrega real de e-mail confirmada em 3 de outubro de 2026

Após uma revisão que havia executado apenas `dry_run`, foi feito um envio SMTP
real a partir da VM, com senha recuperada do Chaves do macOS e injetada somente
na memória do processo de teste. O comando devolveu `sent` para dois
destinatários. A mensagem apareceu como não lida na Inbox Oracle às
`15:23:22Z`.

Em seguida, três contratos fictícios foram enviados pelo MCP central em
transporte stdio, com inboxes DBA temporárias e um helper SMTP temporário sem
segredo em arquivo. Para Health Check, Audit e conclusão do Refactor, o MCP
devolveu `accepted=true`, o DBA registrou o evento e Notification devolveu
`sent` e `delivered=true`. As três mensagens apareceram como não lidas na
Inbox Oracle às `15:27:40Z`, `15:27:45Z` e `15:27:42Z`, respectivamente.
Nenhum DDL ou workload real foi executado nessa prova; os eventos eram
fixtures e o helper temporário foi removido ao final.

Essa prova confirma o roteamento MCP, SMTP e recebimento na caixa testada.
Para operação automática contínua após o teste, a VM ainda precisa de um
helper persistente ligado ao gerenciador de segredos corporativo e da opção
`AGENT_MONITORING_NOTIFY=true` no processo integrado.

## Setup SMTP para clones e nova execução integrada em 3 de outubro

O commit [`bfaf13f`](https://github.com/erikgama/agent-monitoring-db/commit/bfaf13f)
adicionou `scripts/smtp_setup.py` com `init`, `check` e `send-test --send`, e
documentou o uso em `README.md`, `SETUP.md` e no guia Notification. O arquivo
local preserva permissão `0600`; o script não armazena nem imprime a senha.
`check` não consulta o segredo nem abre SMTP. `send-test --send` usa o próprio
runtime Notification e requer envio explícito.

Depois dessa prova, o assunto e o corpo de `send-test --send` foram marcados
como **TESTE SMTP — sem incidente real**. O contrato sintético continuou válido,
e um novo envio real chegou à caixa Oracle às **16:25:25Z** com essa marcação.

Na máquina macOS, `check` identificou o Chaves e `send-test --send` devolveu
`sent`, `delivered=true` e dois destinatários. A mensagem chegou à caixa
Oracle às **15:59:51Z**. Na VM Linux, após `git pull --ff-only`, `check`
identificou o helper temporário e o mesmo comando devolveu `sent`,
`delivered=true` para dois destinatários; a mensagem chegou às **16:09:22Z**.
O helper apenas mediou o segredo em memória durante a sessão de teste. Cada
instalação Linux ainda precisa ligar o helper ao seu gerenciador corporativo
para entrega permanente.

Pelo navegador da VM, o console mostrou **INTEGRADO** e **Runner conectado**.
Health Check, Audit e Refactor foram ativados com a confirmação `sakila`;
`Iniciar consultas` foi acionado pelo botão da interface, sem `dry_run`.
Depois do aquecimento de um minuto, a carga de SELECTs e a query versionada
alimentaram as coletas reais. Durante a execução, a interface mostrou **cinco
entregas SMTP confirmadas**, nenhum erro de entrega e nove evidências no DBA.
Cinco alertas de latência chegaram à caixa Oracle entre **16:07:32Z** e
**16:16:20Z**. Audit permaneceu em observação; não houve DROP ou ALTER.

O coletor identificou `correlated_running_total` no Slow Query Log com
**389 segundos**, acima do limiar estrito de 80 segundos. A triagem devolveu
`no_candidate` porque o estado operacional da própria VM já registrava o envio
da mesma impressão digital em **00:23:25Z**. A deduplicação impediu um novo
pedido e, por consequência, não houve uma nova execução ou novo e-mail do
Refactor nesta rodada. A validação anterior do fluxo completo Refactor está
descrita na seção de 2 de outubro; os três contratos fictícios enviados via
MCP nesta data foram testados separadamente da carga real.

`Cancelar consultas` encerrou a simulação pela interface. Os três agentes
foram parados e o painel ficou com **0/6 componentes ativos**. Uma SELECT de
verificação em `performance_schema.threads`, excluindo a própria conexão,
retornou **zero sessões ativas em `sakila`**. A sessão integrada foi encerrada;
as portas 3000 e 8000 deixaram de escutar, a referência ao helper temporário
foi removida, o arquivo SMTP local manteve `0600` e o clone da VM permaneceu
limpo em `bfaf13f`. O CI desse commit passou em
[GitHub Actions](https://github.com/erikgama/agent-monitoring-db/actions/runs/37135653253).
