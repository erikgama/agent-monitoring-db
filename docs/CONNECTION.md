# Acesso central ao banco

A única fonte de configuração do banco e do LLM é
`config/agent-monitoring.toml`, criado por `python3 scripts/bootstrap.py` ou
`uv run --locked agent-monitoring init`. Para usar outro local, defina
`AGENT_MONITORING_CONFIG=/caminho/agent-monitoring.toml`. Caminhos relativos no
TOML são resolvidos a partir da pasta desse arquivo, independentemente do
diretório atual do agente.

Na seção `[database]`, `login_file` aponta para o arquivo local de perfis;
`monitoring_login_path`, `refactor_login_path`, `workload_login_path` e
`audit_lab_login_path` são **nomes de perfis dentro dele**, não nomes de
usuários MySQL. `expected_port` deve coincidir com a porta cadastrada em cada
perfil e `ssl_mode`/`ssl_ca` definem o TLS. `target_label` é só um rótulo para
relatórios; o host e o usuário de conexão são cadastrados interativamente com
`configure-db`. Veja os comandos completos em [SETUP.md](SETUP.md).

O exemplo usa `~/.config/agent-monitoring/mylogin.cnf` e nomes de perfil fixos.
Se o mesmo usuário do sistema mantiver clones para bancos diferentes, escolha
um `login_file` ou nomes de perfil distintos em cada TOML antes de cadastrar
as contas, para não substituir a conexão de outro ambiente.

O resolvedor em `agent_monitoring/config.py` é instalado como dependência de
todos os módulos. Ele lê somente o TOML não secreto e nunca abre o perfil MySQL.
O cliente Oracle MySQL recebe a referência em `MYSQL_TEST_LOGIN_FILE` e o nome
em `--login-path`. Host, usuário e senha permanecem dentro do arquivo gerenciado
por `mysql_config_editor`. O arquivo é ofuscado pelo cliente; proteja seu acesso
no sistema operacional e nunca o versione.

## Método simples e evolução para um cofre

O `mysql_config_editor` foi escolhido para que cada pessoa consiga cadastrar
seus perfis com um prompt, sem colocar a senha no comando ou no Git. É uma
opção prática para instalar e testar o projeto. A ofuscação do arquivo de login
evita exposição acidental, mas não substitui um cofre de segredos; mantenha o
arquivo acessível somente ao usuário do processo. O próprio
[manual do MySQL](https://dev.mysql.com/doc/refman/8.4/en/mysql-config-editor.html)
explica esse limite.

Para operação corporativa contínua, recomendamos integrar um gerenciador de
segredos da organização, com acesso restrito e rotação. O código atual **não
busca credenciais MySQL em um cofre**: essa integração exige implementação e
validação próprias. O TOML pode continuar como ponto central de referências.
Veja as [orientações da OWASP para gestão de segredos](https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html).

| Papel | Chave do TOML | Schema permitido |
| --- | --- | --- |
| Health Check, Audit, DBA | `monitoring_login_path` | `sakila` e metadados necessários |
| Refactor | `refactor_login_path` | validação read-only em `sakila_dev` |
| Cargas explicitamente autorizadas | `workload_login_path` | `sakila` |
| Demonstração Audit explicitamente autorizada | `audit_lab_login_path` | objetos próprios em `sakila` |

`Notification` não acessa o banco. O MCP valida e roteia; não abre conexões.
As antigas variáveis de conexão específicas por agente não substituem o TOML.
`MYSQL_PWD` é removida dos processos MySQL. Para cadastrar o perfil Refactor:

```sh
uv run --locked agent-monitoring configure-db --role refactor \
  --host SEU_HOST_MYSQL --port 3306 --user SEU_USUARIO_REFACTOR
```

TLS é obrigatório: `REQUIRED`, `VERIFY_CA` ou `VERIFY_IDENTITY`. Prefira
`VERIFY_IDENTITY` com `ssl_ca` apontando para o certificado da CA do servidor.
O resolvedor rejeita modos sem TLS. Nunca copie chave privada para o projeto.

Os schemas `sakila` e `sakila_dev` continuam fixados nas regras e contratos do
laboratório. Este projeto não provisiona esses schemas nem cria usuários ou
privilégios durante setup. MySQL/HeatWave deve estar alcançável por rede e
possuir os recursos consultados, incluindo Performance Schema e, para Audit,
MySQL Enterprise Audit já configurado. Audit depende da função de leitura de
eventos e da política de retenção existentes.

O DBA responsável deve preparar contas de menor privilégio para as consultas
versionadas em `agents/health-check/*/sql/` e
`agents/audit/audit_security/sql/security_snapshot/`. Metadados globais,
`mysql.slow_log` e `audit_log_read()` podem exigir permissões específicas à
versão e ao serviço. Uma conta com somente `SELECT ON sakila.*` não confirma
cobertura completa dessas coletas. O Refactor precisa ler o catálogo de
`sakila_dev` e executar SELECTs de validação. Bootstrap não altera o banco.

Primeiro confirme o perfil com `doctor`; depois rode as duas coletas do guia
de setup. Uma coleta parcial deve ser interpretada com suas capacidades e
limitações, não como cobertura completa da instância.
