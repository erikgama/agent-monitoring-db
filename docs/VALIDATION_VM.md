# Validação de instalação e portabilidade

Executada em **2 de outubro de 2026**, a partir de um clone do GitHub.
Código validado: [`610be18`](https://github.com/erikgama/agent-monitoring-db/commit/610be18).
O repositório foi publicado como privado; quem clonar precisa de acesso ao GitHub.

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
deliberadas e e-mail real não foram executados; os testes correspondentes usam
fixtures e entrega simulada, respeitando `AGENTS.md`.

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
