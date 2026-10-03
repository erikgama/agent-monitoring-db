# agent-monitoring-db

Monitoramento MySQL com cinco agentes, contratos versionados, MCP central e
console Web. O DBA coordena; Health Check e Audit coletam evidências somente
leitura; Refactor valida queries conhecidas em laboratório; Notification entrega
alertas já validados.

O código, as regras, os testes e os scripts do laboratório estão neste
repositório. As referências de banco e a escolha do LLM ficam em um único
arquivo por máquina: **`config/agent-monitoring.toml`**. As credenciais MySQL
ficam no perfil local do cliente, fora do Git. Coletores, advisors, DBA,
Refactor e os executores do console usam o mesmo resolvedor. O login do LLM,
os launchers MCP e a configuração opcional de e-mail também são locais;
[`docs/SETUP.md`](docs/SETUP.md) mostra onde configurar cada um.

Instalação e testes executados em Oracle Linux 9, a partir de um clone novo:
[`registro da validação`](docs/VALIDATION_VM.md). Os testes sem banco usam
fixtures; login LLM, conexão MySQL e entrega SMTP têm verificações próprias.

## 1. Pré-requisitos

Linux ou macOS (Windows via WSL2), Git, Python para iniciar o bootstrap, `uv`,
Node.js 22 LTS com npm e cliente **Oracle MySQL** com `mysql_config_editor`.
O bootstrap instala Python 3.11 com `uv` e ambientes isolados para cada módulo.

No Oracle Linux 9, o instalador documentado está em
[`docs/SETUP.md`](docs/SETUP.md). Instale e autentique pelo menos um cliente LLM:
Codex, Claude Code ou Kimi **Code**. A conta e os modelos precisam estar
disponíveis para o próprio usuário que clonou o projeto.

## 2. Clonar e instalar

```sh
git clone https://github.com/erikgama/agent-monitoring-db.git
cd agent-monitoring-db
python3 scripts/bootstrap.py
```

**Verificação:** o comando termina com `Instalação concluída` e cria
`config/agent-monitoring.toml`. Pode ser repetido; usa os lockfiles e preserva a
configuração local existente. Um repositório privado exige acesso no GitHub.

## 3. Ver o console sem banco

```sh
python3 apps/lab-console/scripts/dev.py
```

Abra **http://localhost:3000**. Este é o modo demo: dados fictícios, sem MySQL,
LLM ou SMTP. `Ctrl-C` encerra os processos. Para uma VM, faça um túnel SSH para
as portas 3000 e 8000 conforme [`docs/SETUP.md`](docs/SETUP.md).

## 4. Configurar o acesso ao MySQL uma vez

Edite as referências em `config/agent-monitoring.toml`. Para cadastrar o perfil:

```sh
uv run --locked agent-monitoring configure-db \
  --host SEU_HOST_MYSQL --port 3306 --user SEU_USUARIO_MONITORAMENTO
```

Digite a senha **diretamente no prompt do MySQL**. Ela nunca é argumento do
comando, conteúdo do TOML ou variável `MYSQL_PWD`. Este perfil atende Health
Check, Audit e as leituras do DBA. Refactor usa um perfil próprio para
`sakila_dev`. Workloads e demonstrações Audit usam perfis explicitamente
separados. Veja permissões, TLS e as limitações de schema em
[`docs/CONNECTION.md`](docs/CONNECTION.md).

**Verificação:** `uv run --locked agent-monitoring doctor` mostra os
pré-requisitos e a existência do perfil, sem ler credenciais nem consultar o
banco. `uv run --locked agent-monitoring db-check` verifica login, schema,
porta e TLS numa transação somente leitura; deve devolver `status=ok`.

## 5. Escolher o LLM e conectar o MCP

Em `config/agent-monitoring.toml`, escolha `llm.provider`: `codex`, `claude` ou
`kimi`. Configure os modelos nas respectivas tabelas; um nome vazio usa o modelo
padrão do cliente autenticado. Os advisors e o chat DBA usam esse mesmo adapter.

```sh
uv run --locked agent-monitoring configure-clients
uv run --locked agent-monitoring llm-check
```

**Verificação:** `llm-check` faz uma pequena chamada real e deve devolver
`"status": "ok"`. Ele verifica o login, o modelo e a resposta estruturada;
consome uma chamada do provedor e não acessa o banco. Para trocar apenas durante
um teste: `AGENT_MONITORING_LLM_PROVIDER=claude uv run --locked agent-monitoring llm-check`.

O gerador cria launchers locais com o caminho **deste clone** para Codex, Claude
Code e Kimi. Cada máquina gera seus próprios launchers. Inicie seu cliente na
raiz do clone e aprove a confiança do projeto/MCP quando o cliente solicitar.
[`docs/LLM_CLIENTS.md`](docs/LLM_CLIENTS.md) explica as instruções por papel e as
diferenças entre os clientes.

## 6. Executar todas as verificações sem banco

```sh
python3 scripts/check.py
```

**Verificação:** todas as suítes Python, lint, tipos e build Web devem passar.
Os testes usam bancos/SMTP simulados. CI executa o mesmo caminho em Linux.

## 7. Ativar o modo integrado

Com os perfis, TLS, schemas e LLM disponíveis, siga a validação incremental em
[`docs/SETUP.md`](docs/SETUP.md). Primeiro faça uma coleta somente leitura de
Health Check e Audit; depois inicie o console integrado. Demonstrações que
executam carga, DML ou DDL exigem tarefa e autorização específicas, conforme
`AGENTS.md`; não pertencem ao bootstrap nem aos testes de instalação.

## 8. Configurar e testar e-mail

SMTP é opcional e local a cada clone. Execute `python3 scripts/smtp_setup.py init`,
preencha host, porta, remetente e destinatários no arquivo criado em
`agents/notification/.notification.local.env`, e configure a referência ao
gerenciador de segredos no Linux ou o Chaves no macOS. A senha não entra no
arquivo nem no Git. Depois execute `python3 scripts/smtp_setup.py check` e
`python3 scripts/smtp_setup.py send-test --send`; o segundo comando envia um
e-mail real identificado como teste para validar o recebimento. O
[passo a passo de SMTP](docs/SETUP.md) inclui o formato do helper e a ativação
no console integrado.

## Estrutura

| Caminho | Função |
| --- | --- |
| `agent_monitoring/` | Configuração central, CLI e adapters LLM |
| `config/agent-monitoring.example.toml` | Modelo sem segredos |
| `agents/dba/` | Coordenação, inboxes, playbooks e cargas explícitas |
| `agents/health-check/` | Coletas, latência SELECT e triagem de queries lentas |
| `agents/audit/` | Coleta e interpretação do Enterprise Audit |
| `agents/refactor/` | Propostas SQL e equivalência em `sakila_dev` |
| `agents/notification/` | Entrega de contratos validados |
| `mcp/` | Validação, deduplicação e roteamento central |
| `apps/lab-console/` | API, Web e runner local |

Os nomes internos `mysqlconf_mcp` e o comando legado `mysqlconf-mcp` são
mantidos como interfaces compatíveis. O repositório é `agent-monitoring-db`;
a CLI de configuração e o pacote central usam `agent-monitoring`.
Resultados reais, credenciais, caches, sessões LLM,
configurações locais e materiais pessoais permanecem locais e ignorados pelo
Git. Histórico de implementação: [`HISTORICO.md`](HISTORICO.md).
