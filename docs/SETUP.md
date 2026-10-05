# Instalação e verificação, etapa por etapa

Execute na raiz do clone. Cada etapa indica o resultado esperado. O caminho
testado em Linux é Oracle Linux 9; macOS usa os mesmos comandos após instalar os
pré-requisitos. Windows deve executar este fluxo dentro do WSL2.
Para ver ou publicar somente a demo fictícia, siga as etapas 1–4; o cliente
MySQL, o login LLM e o SMTP são necessários apenas para os fluxos reais
descritos depois.

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
dependências Python serão instaladas pelo `uv`. Ao abrir outra sessão SSH,
confirme que `uv` continua no `PATH`; se necessário, configure o diretório
`$HOME/.local/bin` no perfil do seu shell.

Se o módulo `nodejs:22` não estiver disponível nos repositórios aprovados da
VM, outra opção verificada em Oracle Linux 9 é extrair o arquivo oficial do
Node.js 22.19.0 para o diretório do usuário. Escolha o arquivo adequado à
arquitetura da máquina, confira seu SHA-256 com o `SHASUMS256.txt` publicado
em [nodejs.org](https://nodejs.org/dist/v22.19.0/) e inclua o diretório `bin`
no `PATH` de cada sessão ou serviço. Essa instalação não substitui o Node do
sistema. A versão 22.19.0 é a que foi testada, não uma exigência de fixá-la.

Para as etapas de banco real, instale também **somente o cliente Oracle MySQL**
aprovado pela organização, com `mysql` e `mysql_config_editor`. Para Oracle
Linux 9, a [documentação oficial dos pacotes RPM](https://dev.mysql.com/doc/refman/8.4/en/linux-installation-rpm.html)
descreve os pacotes de cliente e a [documentação do repositório MySQL
Yum](https://dev.mysql.com/doc/refman/8.4/en/linux-installation-yum-repo.html)
explica como habilitar a fonte de pacotes. Não é preciso instalar um servidor
MySQL nesta máquina. Confirme antes de cadastrar perfis:

```sh
mysql --version
command -v mysql_config_editor
mysql --help | grep -- '--ssl-mode'
```

MariaDB CLI não é substituto automático para essas opções.
Na VM Oracle Linux 9 de validação, `sudo dnf install -y mysql` a partir do
repositório aprovado `ol9_appstream` forneceu ambos os executáveis e
`--ssl-mode`. É uma alternativa ao repositório MySQL Yum; confirme a origem e
a versão permitidas pela sua organização antes de instalar.

No macOS, instale Git, `uv` e Node.js 22 pelo gerenciador de pacotes utilizado
pela sua organização; acrescente o cliente MySQL para as etapas de banco real.
No Linux de outras distribuições, use o gerenciador do sistema para os mesmos
pré-requisitos.

## 2. Clonar e instalar módulos

O repositório público pode ser clonado por HTTPS sem login no GitHub. Se usar
um fork privado, conceda acesso de leitura à identidade desta máquina e
configure a autenticação Git conforme o método aprovado pela organização.
Não coloque tokens na URL nem copie chaves ou sessões de login de outra máquina.

```sh
git clone https://github.com/erikgama/agent-monitoring-db.git
cd agent-monitoring-db
python3 scripts/bootstrap.py
```

Esperado: nove ambientes Python, dependências Web instaladas com `npm ci` e
configuração central criada. Os lockfiles ficam versionados. Para um servidor
sem Web, use `--skip-web`. O comando pode ser repetido sem sobrescrever o TOML.
Se um fork privado retornar `Repository not found` ou erro de autenticação,
confirme o acesso de leitura e a autenticação HTTPS antes de prosseguir.

## 3. Verificar instalação sem dependências externas

```sh
python3 scripts/check.py
```

Esperado: testes Python de todos os papéis, MCP e API passam; lint, tipos e
build Web terminam com sucesso. Nenhum banco, SMTP ou LLM real é chamado. Esse
resultado confirma o clone e as dependências, não o acesso à sua instância.

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

Depois dos testes, execute `git status --short`: em um clone sem alterações
próprias, a saída deve ficar vazia. O teste de navegador usa configuração
TypeScript e build temporários; arquivos gerados pelo Next.js são ignorados.

## 4. Confirmar o console demo

```sh
python3 apps/lab-console/scripts/dev.py
```

Esperado: Web em `127.0.0.1:3000` e API em `127.0.0.1:8000`. Dados e eventos
fictícios, sem efeitos externos. `Ctrl-C` encerra os processos.

Para visualizar **somente a demo** de uma VM pelo computador do operador:

```sh
ssh -L 3000:127.0.0.1:3000 USUARIO@SUA_VM
```

Abra `http://localhost:3000`. Se sua organização usa uma chave SSH, forneça
seu caminho com `-i`; ela permanece fora do repositório. Esse túnel é para a
demo. O integrado atual, que concede papel DBA sem autenticar a pessoa, só
deve ser usado no navegador da própria máquina confiável; não o exponha nem
o disponibilize por túnel ou proxy.

### Publicar somente a demo fictícia

Use esta receita apenas para a fábrica `public_demo`. Ela bloqueia a navegação
pelos arquivos do clone e não oferece MySQL, runner, MCP ou SMTP reais.
Qualquer visitante da URL publicada pode iniciar as simulações fictícias, sem
login. Não use esse endereço para dados ou ações operacionais.
Conclua o bootstrap antes. Nos exemplos abaixo, substitua `HOST_PUBLICO` pelo
nome ou IP de entrada aprovado; configure a origem com o mesmo esquema, host e
porta que o navegador usará. Em um ambiente corporativo, use a política de
ingresso aprovada e prefira HTTPS em um proxy que publique apenas a Web.

No primeiro terminal, inicie a API isolada em loopback:

```sh
cd /caminho/agent-monitoring-db/apps/lab-console/api
LAB_MODE=demo LAB_ORIGIN=http://HOST_PUBLICO:3000 \
  LAB_RUNTIME="$PWD/../runtime/public-demo" \
  MCP_NOTIFICATION_ENABLED=false MCP_DBA_ENABLED=false \
  NOTIFICATION_DELIVERY_ENABLED=false \
  .venv/bin/uvicorn labconsole.public_demo:create_public_demo \
  --factory --host 127.0.0.1 --port 8000
```

No segundo terminal, faça o build com o destino da API já definido e publique
a Web na porta 3000. `LAB_API_URL` é incorporado ao build; se mudar o destino,
refaça o build.

```sh
cd /caminho/agent-monitoring-db/apps/lab-console/web
LAB_API_URL=http://127.0.0.1:8000 npm run build
LAB_BIND_HOST=0.0.0.0 PORT=3000 LAB_API_URL=http://127.0.0.1:8000 npm start
```

Libere apenas `3000/tcp` no firewall local e na regra de entrada da nuvem,
conforme a política da organização. Mantenha `8000/tcp` fechado para a rede.
De outro computador, verifique que
`http://HOST_PUBLICO:3000/api/health` responde `"mode":"demo"` e que
`/api/resources/` responde 404. Se houver proxy HTTPS, use sua URL externa
em `LAB_ORIGIN` e nas verificações. Uma resposta HTTP 200 da Web sozinha não
prova qual modo da API está ativo.

Esses comandos ocupam os terminais e param quando a sessão termina. Para
operação contínua, instale API e Web em serviços supervisionados pela equipe
da VM, com diretório de trabalho, `PATH`, ambiente e reinício definidos;
teste o início e a parada dos dois serviços e repita o check de `mode=demo`
após reiniciar a VM. Uma tentativa de `systemd-run` executando diretamente
binários sob `/home/opc` falhou com `203/EXEC` nessa VM: valide um caminho
executável permitido pelo serviço, sem presumir que o terminal interativo e o
gerenciador de serviços tenham as mesmas permissões. Consulte também o
[guia do Lab Console](../apps/lab-console/README.md).

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

Esta etapa usa o caminho simples de instalação com `mysql_config_editor`.
Para operação corporativa contínua, leia a [nota sobre cofre e limites do
arquivo de login](CONNECTION.md); o projeto ainda não integra um cofre para
credenciais MySQL.

O banco deve estar preparado pelo DBA e alcançável da máquina. Em
`config/agent-monitoring.toml`, ajuste a seção `[database]` antes de cadastrar
as contas. Este arquivo contém apenas referências, nunca senhas:

| Campo em `[database]` | O que preencher |
| --- | --- |
| `login_file` | Caminho local, fora do Git, para os perfis criados por `mysql_config_editor`; pode usar `~` |
| `monitoring_login_path` | Nome do perfil de Health Check, Audit e leituras do DBA |
| `refactor_login_path` | Nome do perfil separado de Refactor para `sakila_dev` |
| `expected_port` | Porta real do MySQL; use o mesmo número em `configure-db --port` |
| `ssl_mode` e `ssl_ca` | TLS obrigatório; configure a CA ao usar `VERIFY_CA` ou `VERIFY_IDENTITY` |
| `target_label` | Rótulo de exibição do ambiente; o host de conexão é cadastrado no perfil MySQL |

Os nomes dos perfis podem permanecer como no exemplo. Se os mudar no TOML,
`configure-db` usa os novos nomes. Cadastre primeiro a conta de monitoramento:

```sh
uv run --locked agent-monitoring configure-db \
  --host SEU_HOST_MYSQL --port 3306 --user SEU_USUARIO_MONITORAMENTO
```

Esperado: o cliente pede a senha interativamente e grava o perfil local. O
agente não lê sua senha. Para Refactor, use outra conta, restrita a `sakila_dev`:

```sh
uv run --locked agent-monitoring configure-db --role refactor \
  --host SEU_HOST_MYSQL --port 3306 --user SEU_USUARIO_REFACTOR
```

Não configure os perfis `audit-lab` ou `workload` com a conta de monitoramento.
Eles são para tarefas de laboratório com autorização específica.

```sh
uv run --locked agent-monitoring doctor
uv run --locked agent-monitoring db-check
uv run --locked agent-monitoring db-check --role refactor
```

`doctor` verifica referências e binários, sem conectar ao banco. Requisitos de
privilégios e de serviço estão em [`CONNECTION.md`](CONNECTION.md).
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

## 9. Configurar e testar o SMTP opcional

O SMTP é configurado uma vez por clone, em um arquivo local ignorado pelo Git.
Ele contém host, porta, conta e destinatários; a senha fica no Chaves do macOS
ou no gerenciador de segredos da instalação Linux. As coletas funcionam sem
SMTP. Prepare o e-mail antes do console integrado se quiser receber alertas.

```sh
python3 scripts/smtp_setup.py init
```

Edite `agents/notification/.notification.local.env` e preencha:

| Chave | Valor |
| --- | --- |
| `NOTIFICATION_EMAIL_FROM` | Remetente, igual a `SMTP_USERNAME` |
| `NOTIFICATION_EMAIL_RECIPIENTS_WARNING` | Destinatários de avisos e do teste, separados por vírgula |
| `NOTIFICATION_EMAIL_RECIPIENTS_CRITICAL` | Destinatários de alertas críticos, separados por vírgula |
| `NOTIFICATION_EMAIL_RECIPIENTS_REFACTOR` | Destinatários da conclusão do Refactor; se vazio, usa WARNING |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USE_STARTTLS` | Servidor, porta e STARTTLS conforme o serviço SMTP; Gmail usa 587 e `true` |
| `SMTP_USERNAME` | Conta de autenticação SMTP; obrigatória neste fluxo integrado |
| `NOTIFICATION_SMTP_CREDENTIAL_HELPER` | No Linux, caminho absoluto do executável que consulta o segredo; no macOS pode ficar ausente |

Troque todos os endereços `.invalid` usados por endereços reais. Mantenha
`NOTIFICATION_DELIVERY_ENABLED=false` no arquivo e **não adicione
`SMTP_PASSWORD`**. `init` cria o arquivo com permissão `0600` e preserva uma
configuração existente. O formato é uma atribuição `CHAVE=VALOR` por linha,
sem usar `source` no shell.

No **macOS**, use o mesmo usuário do sistema que executará o projeto. Sem
helper, Notification procura a senha no Chaves com serviço fixo
`mysqlconf-notification-smtp` e conta igual a `SMTP_USERNAME`. Esse nome do
serviço é um identificador legado preservado para compatibilidade com os
segredos locais existentes; o projeto, o repositório e o comando MCP usam
`agent-monitoring`. Cadastre a senha com um prompt interativo; substitua apenas
o endereço no comando:

```sh
security add-generic-password -U -a "SEU_EMAIL_SMTP" -s mysqlconf-notification-smtp -w
```

No **Linux**, configure `NOTIFICATION_SMTP_CREDENTIAL_HELPER` com o caminho
absoluto de um executável do gerenciador de segredos da empresa. O programa
recebe `--account USUARIO_SMTP`, devolve uma única linha com a senha em stdout
e sai com código zero. Provisione o segredo no cofre e conceda acesso à mesma
identidade do sistema que executará o console. Não escreva a senha no script,
no arquivo local ou na linha de comando. `check` valida o caminho e a permissão
de execução; não comprova acesso ao cofre. O contrato completo está no
[guia Notification](../agents/notification/docs/operations.md).
Uma chave temporária da sessão do terminal serve para teste, mas não sustenta
um serviço após reinício. Para entrega contínua, valide a consulta ao cofre
sob a identidade do serviço e após reiniciar a VM.

```sh
python3 scripts/smtp_setup.py check
python3 scripts/smtp_setup.py send-test --send
```

`check` deve devolver `status=ready_for_send_test`. Ele valida campos,
endereços, permissão do arquivo e referência do helper sem consultar o segredo
nem abrir conexão SMTP. `send-test --send` consulta a senha somente em runtime
e envia **um e-mail real** identificado como teste aos destinatários WARNING.
Espere `status=sent` e `delivered=true` e confirme a chegada na caixa de
entrada; o programa não confirma o recebimento final. A saída não expõe senha
ou endereços. Envie apenas a destinatários autorizados.

Se não quiser SMTP, pule esta etapa e inicie o console sem
`AGENT_MONITORING_NOTIFY=true`.

## 10. Rodar o console integrado

Se for executar a simulação de SELECTs, cadastre também uma conta separada
de somente leitura para a carga. A SELECT conhecida e deliberadamente lenta
usa esse perfil, enquanto o coletor do Slow Query Log continua usando o
perfil de monitoramento:

```sh
uv run --locked agent-monitoring configure-db --role workload \
  --host SEU_HOST_MYSQL --port 3306 --user SEU_USUARIO_CARGA
uv run --locked agent-monitoring db-check --role workload
```

Esperado: `status=ok`, `database=sakila` e `read_only`, `tls` e
`port_matches` iguais a `true`.

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

Se a etapa 9 passou e esta sessão deve entregar e-mail real, inicie com:

```sh
AGENT_MONITORING_NOTIFY=true python3 apps/lab-console/scripts/dev.py --integrated --allow-execute
```

O arquivo local continua com `NOTIFICATION_DELIVERY_ENABLED=false`; o launcher
habilita Notification em memória apenas quando a sessão usa
`AGENT_MONITORING_NOTIFY=true`. Se mudar servidor, conta ou destinatários,
edite o mesmo arquivo e repita `check` e `send-test --send`.

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
| `smtp_config_permissions_insecure` | Aplique `chmod 600 agents/notification/.notification.local.env` no clone |
| `smtp_addresses_invalid_or_placeholder` ou `smtp_sender_mismatch` | Substitua endereços `.invalid` e iguale remetente a `SMTP_USERNAME` |
| `smtp_credential_helper_required` | No Linux, configure o caminho absoluto do helper corporativo |
| `smtp_credential_helper_unavailable` ou `smtp_credential_lookup_failed` | Confira se o helper é executável e se o usuário do processo acessa o cofre |
| `smtp_keychain_lookup_failed` | No macOS, cadastre o serviço e a conta SMTP no Chaves do usuário que executa o projeto |
| `smtp_send_test_failed` ou outra falha SMTP | Verifique host, porta, STARTTLS, política do provedor e acesso de rede; repita `send-test --send` |
| Lockfile desatualizado | Use um checkout íntegro; alterações de dependências exigem `uv lock` pelo mantenedor |
