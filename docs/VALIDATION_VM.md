# Validação de instalação e portabilidade

Executada em **2 de outubro de 2026**, a partir de um clone do GitHub.
Código validado: [`11ac976`](https://github.com/erikgama/agent-monitoring-db/commit/11ac976).
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
| Navegador | `python3 scripts/e2e.py` | **7 cenários Chromium passaram** com runtime e build isolados |
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

## GitHub Actions

O mesmo bootstrap, `check.py` e os sete cenários de navegador passaram no CI:
[execução validada](https://github.com/erikgama/agent-monitoring-db/actions/runs/36962682102).
O workflow é `.github/workflows/ci.yml` e roda em cada push/PR, sem banco,
SMTP ou autenticação LLM reais.

## Banco real: situação verificada

Na máquina de origem, usando apenas a referência do perfil já configurado:

| Verificação | Resultado |
| --- | --- |
| `agent-monitoring db-check` | Login, `sakila`, porta e TLS confirmados em transação somente leitura |
| Health Check `collect` | Coleta executada; o relatório observou estado `critical` |
| Audit `collect` | Coleta executada com `collection_status=complete` |

Sucesso de instalação/coleta não significa que o banco esteja saudável.
Os resultados operacionais reais permanecem nos `results/` de seus agentes,
ignorados pelo Git.

**Na VM, as coletas reais MySQL permanecem pendentes.** O perfil não foi
encontrado nos caminhos padrão `~/.mylogin.cnf` e
`~/.config/agent-monitoring/mylogin.cnf`. É necessário cadastrar o perfil na
própria VM ou indicar seu caminho e nome no TOML central. Depois, executar
`doctor`, `db-check` e as duas coletas da etapa 8 de `SETUP.md`.

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
