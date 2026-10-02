# Instalação e verificação, etapa por etapa

Execute na raiz do clone. Cada etapa indica o resultado esperado. O caminho
testado em Linux é Oracle Linux 9; macOS usa os mesmos comandos após instalar os
pré-requisitos. Windows deve executar este fluxo dentro do WSL2.

## 1. Preparar o sistema

No Oracle Linux 9:

```sh
sudo dnf install -y git curl tar gzip xz
sudo dnf module enable -y nodejs:22
sudo dnf install -y nodejs npm
curl --proto '=https' --tlsv1.2 -LsSf https://astral.sh/uv/install.sh -o /tmp/uv-install.sh
sh /tmp/uv-install.sh
export PATH="$HOME/.local/bin:$PATH"
git --version
node --version
npm --version
uv --version
```

Esperado: todos os comandos respondem; Node deve ser 22.19 ou superior. As
dependências Python serão instaladas pelo `uv`. O cliente Oracle MySQL deve
oferecer `mysql --version`, `mysql_config_editor` e `--ssl-mode`. MariaDB CLI
não é substituto automático para essas opções.

No macOS, instale Git, `uv`, Node.js 22 e o cliente MySQL pelo gerenciador de
pacotes utilizado pela sua organização. No Linux de outras distribuições,
instale os mesmos pré-requisitos com o gerenciador do sistema.

## 2. Clonar e instalar módulos

```sh
git clone https://github.com/erikgama/agent-monitoring-db.git
cd agent-monitoring-db
python3 scripts/bootstrap.py
```

Esperado: nove ambientes Python, dependências Web instaladas com `npm ci` e
configuração central criada. Os lockfiles ficam versionados. Para um servidor
sem Web, use `--skip-web`. O comando pode ser repetido sem sobrescrever o TOML.

## 3. Verificar instalação sem dependências externas

```sh
python3 scripts/check.py
```

Esperado: testes Python de todos os papéis, MCP e API passam; lint, tipos e
build Web terminam com sucesso. Nenhum banco, SMTP ou LLM real é chamado.

Os testes de navegador também usam demo e um runtime temporário vazio:

```sh
cd apps/lab-console/web
npx playwright install chromium
cd ../../..
python3 scripts/e2e.py
```

No Linux, o navegador pode precisar das bibliotecas do sistema indicadas pelo
Playwright. O instalador do navegador não cadastra conexão MySQL.

No Oracle Linux 9, as bibliotecas usadas na validação Chromium foram:

```sh
sudo dnf install -y atk at-spi2-atk alsa-lib cups-libs libXcomposite \
  libXdamage libXrandr libxkbcommon mesa-libgbm pango cairo
```

Execute `check.py` e `e2e.py` sequencialmente, como acima; a compilação Web
simultânea pode disputar CPU com os prazos da suíte de integração.

## 4. Confirmar o console demo

```sh
python3 apps/lab-console/scripts/dev.py
```

Esperado: Web em `127.0.0.1:3000` e API em `127.0.0.1:8000`. Dados e eventos
fictícios, sem efeitos externos. `Ctrl-C` encerra os processos.

Para visualizar uma VM pelo computador do operador:

```sh
ssh -L 3000:127.0.0.1:3000 -L 8000:127.0.0.1:8000 USUARIO@SUA_VM
```

Abra `http://localhost:3000`. Se sua organização usa uma chave SSH, forneça
seu caminho com `-i`; ela permanece fora do repositório. Não exponha as portas
do console no firewall: o acesso integrado é restrito a loopback.

## 5. Instalar e autenticar o LLM escolhido

Com Node.js preparado, uma instalação npm por usuário:

```sh
npm install --global --prefix "$HOME/.local" @openai/codex
export PATH="$HOME/.local/bin:$PATH"
codex --version
codex login
```

Para Claude Code ou Kimi Code, instale o pacote do cliente escolhido:

```sh
npm install --global --prefix "$HOME/.local" @anthropic-ai/claude-code
claude --version
claude auth login
```

```sh
npm install --global --prefix "$HOME/.local" @moonshot-ai/kimi-code
kimi --version
kimi login
```

Não copie autenticação de outra pessoa ou máquina. Use o login do próprio
cliente na VM. Instalação do binário não confirma login, plano ou modelo.
Guias oficiais: [Codex CLI](https://developers.openai.com/codex/cli/),
[Claude Code](https://code.claude.com/docs/en/setup),
[Kimi Code](https://moonshotai.github.io/kimi-code/en/guides/getting-started.html).

## 6. Selecionar provedor, modelos e MCP

Edite `config/agent-monitoring.toml`. Em `[llm]`, escolha `codex`, `claude` ou
`kimi`. Em `[llm.<provedor>]`, configure `analysis_model` e `refactor_model`.
Os modelos Codex do exemplo preservam a configuração do laboratório original;
troque por modelos disponíveis na sua conta ou deixe `""` para o padrão do
cliente. A autenticação continua no armazenamento do cliente, fora do TOML.

```sh
uv run --locked agent-monitoring configure-clients
uv run --locked agent-monitoring llm-check
```

Esperado: launchers locais gerados e JSON com `"status": "ok"`. Uma nova
localização do clone exige gerar launchers nessa localização. Consulte
[`LLM_CLIENTS.md`](LLM_CLIENTS.md) para conferir a descoberta do MCP no cliente.

Para verificar os quatro advisors, o resumo e o chat do DBA:

```sh
python3 scripts/check-llm.py --execute
```

Esperado: seis resultados `status=ok`. Consome seis chamadas do provedor
escolhido, com fixtures sintéticas; não consulta MySQL, publica contratos ou
envia e-mail. A proposta Refactor é validada como proposta; equivalência e
desempenho do SQL dependem da validação específica em `sakila_dev`.

## 7. Cadastrar os perfis do banco

O banco deve estar preparado pelo DBA e alcançável da máquina. Em
`config/agent-monitoring.toml`, configure `login_file`, perfis, TLS,
`ssl_ca` quando aplicável e `expected_port`. Cadastre primeiro monitoramento:

```sh
uv run --locked agent-monitoring configure-db \
  --host SEU_HOST_MYSQL --port 3306 --user SEU_USUARIO_MONITORAMENTO
```

Esperado: o cliente pede a senha interativamente e grava o perfil local. O
agente não lê sua senha. Para Refactor, repita com `--role refactor` e a conta
restrita a `sakila_dev`. Não configure o perfil `audit-lab` ou `workload` com a
conta de monitoramento. Esses perfis são para tarefas de laboratório com
autorização específica.

```sh
uv run --locked agent-monitoring doctor
uv run --locked agent-monitoring db-check
uv run --locked agent-monitoring db-check --role refactor
```

Esperado: todos os itens necessários são `true`. Doctor verifica referências e
binários; as coletas seguintes comprovam o acesso. Requisitos de privilégios e
de serviço estão em [`CONNECTION.md`](CONNECTION.md).
`db-check` verifica login, schema, porta e TLS numa transação somente leitura.
O segundo comando exige o perfil Refactor e `sakila_dev`; pode ser executado
depois que esse perfil estiver cadastrado. Saída esperada: `status=ok` e
`read_only`, `tls` e `port_matches` iguais a `true`.

## 8. Verificar duas coletas reais somente leitura

```sh
uv run --locked --directory agents/health-check mysql-health-check collect
uv run --locked --directory agents/audit mysql-audit-security collect
```

Esperado: JSON/HTML recentes nos respectivos `results/`, sem credenciais,
identidades ou SQL de sessões persistidos. Verifique timestamp, capacidades,
erros de permissão e domínios inconclusivos antes de considerar a cobertura
completa. Audit requer MySQL Enterprise Audit configurado; bootstrap não
habilita plugins, filtros ou usuários.

## 9. Rodar o console integrado

Primeiro verifique o runner sem autorizar execução:

```sh
python3 apps/lab-console/scripts/dev.py --integrated
```

Esperado: runner conecta e mostra disponibilidade dos recursos. Para habilitar
as ações permitidas pelo console em uma sessão operacional explicitamente
autorizada:

```sh
python3 apps/lab-console/scripts/dev.py --integrated --allow-execute
```

Os botões de carga e DDL mantêm as confirmações e precondições do projeto.
Não os use como teste de instalação. Health Check e Audit não executam
mudanças. Refactor recebe somente o fluxo versionado de query lenta via MCP.

## 10. Configurar notificação opcional

Copie `agents/notification/.env.example` para
`agents/notification/.notification.local.env` e configure somente os campos
não secretos e destinatários, mantendo `NOTIFICATION_DELIVERY_ENABLED=false`.
As rotinas de monitoramento funcionam sem essa configuração.

Nos scripts de laboratório, entrega real exige a opção explícita de ambiente
`AGENT_MONITORING_NOTIFY=true`. No macOS, Notification pode resolver sua própria
senha no Keychain pelo serviço legado documentado. Em Linux integrado, configure
`NOTIFICATION_SMTP_CREDENTIAL_HELPER` com o caminho absoluto de um executável
do seu gerenciador de segredos; somente Notification o chama. Para execução
isolada, `SMTP_PASSWORD` pode ser injetado só no processo Notification.
Consulte `agents/notification/docs/operations.md`. E-mail real deve
ser validado numa tarefa explicitamente autorizada para os destinatários.

## Problemas comuns

| Sintoma | Verificação |
| --- | --- |
| `central_config_not_found` | Caminho de `AGENT_MONITORING_CONFIG` deve existir |
| `approved_login_file_not_found` | Cadastre o perfil indicado pelo TOML |
| `llm_cli_not_found` | Cliente instalado e diretório de binários no PATH |
| `llm_execution_failed` | Login, modelo e acesso à API do provedor |
| Falha TLS | Cliente Oracle MySQL, certificado/hostname e CA corretos |
| Coleta inconclusiva | Recursos e privilégios exigidos pelo domínio |
| Web sem API | Túnel de ambas as portas e API em loopback |
| Lockfile desatualizado | Use um checkout íntegro; alterações de dependências exigem `uv lock` pelo mantenedor |
