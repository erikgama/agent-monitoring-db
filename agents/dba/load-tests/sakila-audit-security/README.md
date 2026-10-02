# Demonstração controlada do Audit Security

Este cenário produz eventos seguros e previsíveis para validar o fluxo:

```text
workload DBA
  -> MySQL Enterprise Audit
  -> Audit Security monitor
  -> regra determinística
  -> MCP
  -> notification + inbox do DBA
```

O executor nunca usa `sakila-admin`. Ele aceita somente o login-path
`sakila-audit-demo` e confirma no preflight que a conta efetiva é
`sakila_audit_demo`, sem role ativa e sem privilégios `DROP`, `ALTER`,
`ALL PRIVILEGES` ou `GRANT OPTION`.

## Credencial e identidade da demonstração

O provisionamento validado em 2026-09-16 usa:

- conta MySQL: `sakila_audit_demo`;
- login-path: `sakila-audit-demo`;
- arquivo local de login-path:
  `~/.config/agent-monitoring/mylogin.cnf`;
- schema: `sakila`;
- mesma instância MySQL HeatWave observada pelo Audit;
- TLS obrigatório;
- máximo de duas conexões simultâneas para essa conta.

O arquivo local possui permissão `600`. A senha foi gerada aleatoriamente em
memória, informada diretamente ao MySQL e ao `mysql_config_editor` e removida
da memória após o provisionamento. A senha em texto puro não existe no código,
README, relatórios, argumentos de execução ou saída do terminal.

O workload usa apenas a referência ao perfil. Para validar manualmente a
conexão sem informar senha:

```sh
MYSQL_TEST_LOGIN_FILE=~/.config/agent-monitoring/mylogin.cnf \
mysql --login-path=sakila-audit-demo \
  --ssl-mode=REQUIRED \
  --database=sakila
```

Não abra, copie ou versione o conteúdo do arquivo de login-path. Se a senha
precisar ser conhecida por outro cliente, não tente recuperá-la: faça uma
rotação controlada com o DBA e atualize o perfil `sakila-audit-demo` usando o
prompt interativo do `mysql_config_editor`.

## Preparação única pelo DBA

A conta e a tabela descartável foram provisionadas e validadas em 2026-09-16.
Se precisarem ser reconstruídas em outro ambiente, o provisionamento deverá ser
uma mudança separada e explicitamente aprovada. O executor não cria usuários,
não concede privilégios e não armazena senha.

Estrutura necessária:

```sql
CREATE TABLE sakila.audit_security_demo_events (
  run_id CHAR(36) NOT NULL PRIMARY KEY,
  event_type VARCHAR(32) NOT NULL,
  payload VARCHAR(255) NOT NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
) ENGINE=InnoDB;
```

Privilégios mínimos da conta `sakila_audit_demo`:

```sql
GRANT SELECT, INSERT, UPDATE, DELETE
ON sakila.audit_security_demo_events
TO 'sakila_audit_demo'@'<host-controlado>';

GRANT CREATE TEMPORARY TABLES
ON sakila.*
TO 'sakila_audit_demo'@'<host-controlado>';
```

Não conceda `DROP`, `ALTER`, `CREATE`, `ALL PRIVILEGES` ou `GRANT OPTION`. A
senha deve ser criada e guardada fora do repositório. Configure a mesma conta
no login-path local `sakila-audit-demo`; o comando interativo de configuração
deve ser executado pelo operador e não pelo executor.

## Operações de uma rodada

Cada rodada usa um UUID novo e executa, nesta ordem:

1. `INSERT` na tabela descartável;
2. `UPDATE` somente da linha da rodada;
3. `SELECT` da mesma linha;
4. criação, uso e remoção de uma tabela temporária;
5. `DELETE` da linha da rodada;
6. tentativa de `ALTER TABLE` para remover uma coluna propositalmente
   inexistente da fixture;
7. tentativa de `DROP TABLE sakila.audit_security_demo_events`.

As duas últimas operações devem falhar com erro de permissão. A tentativa de
`ALTER TABLE` alimenta a regra `audit_security.sakila.blocked_alter_table`; a
tentativa de `DROP` alimenta `audit_security.sakila.blocked_destructive_ddl`.
Ambas são críticas e independentes. A coluna do `ALTER` não existe, portanto,
mesmo diante de um erro de configuração que conceda `ALTER`, a operação falha
sem modificar o schema. Se uma operação protegida for aceita, o executor registra
`unsafe_unexpected_success`, encerra a rodada e retorna erro.

Não há retry. O executor tenta remover sua linha sintética no encerramento e
nunca persiste SQL literal, senha, endpoint ou conteúdo do login-path nos
relatórios.

## Dry-run

A partir da raiz do repositório:

```sh
PYTHONDONTWRITEBYTECODE=1 \
python3 agents/dba/load-tests/sakila-audit-security/sakila_audit_security_demo.py
```

O dry-run não abre conexão com o banco.

## Execução autorizada

Depois de iniciar o monitor e o agente Luna do Audit, execute uma rodada:

```sh
PYTHONDONTWRITEBYTECODE=1 \
python3 agents/dba/load-tests/sakila-audit-security/sakila_audit_security_demo.py \
  --execute \
  --confirm-target sakila \
  --confirm-user sakila_audit_demo \
  --confirm-demo AUDIT_SECURITY_SAKILA \
  --rounds 1
```

`--rounds` aceita de 1 a 10, mas cada rodada gera duas novas tentativas
bloqueadas e, consequentemente, duas chaves criptográficas de evento elegíveis
para alerta. Para a primeira demonstração, use somente uma rodada.

Para executar os dois cenários separadamente, acrescente `--scenario drop` ou
`--scenario alter`. Nesses modos, o executor realiza somente a tentativa
protegida escolhida, sem executar o ciclo auxiliar de DML da demonstração
completa.

## Atalhos do laboratório integrado

A partir da raiz do repositório, o procedimento recomendado é:

```sh
# terminal 1: coleta inicial, monitor, MCP e advisor
python3 apps/lab-console/scripts/run-audit-lab.py --execute

# terminal 2: uma tentativa protegida por cenário
python3 apps/lab-console/scripts/run-audit-drop-lab.py --execute
python3 apps/lab-console/scripts/run-audit-alter-lab.py --execute
```

Os três scripts são dry-run por padrão. Os wrappers de cenário fornecem ao
executor as confirmações fixas de `sakila`, `sakila_audit_demo` e
`AUDIT_SECURITY_SAKILA`, sempre com uma rodada. Eles não contornam o preflight
de segurança e não usam a conta administrativa.

DROP e ALTER podem ser iniciados em paralelo depois que o monitor declarar que
está pronto. O monitor deve detectar dois eventos independentes, publicar
`destructive_ddl` e `schema_change` pelo MCP e marcar repetições dos mesmos
eventos como `duplicate`. Encerre o primeiro terminal com `Ctrl-C`.

Os resultados ficam em `reports/`, em JSON e Markdown. Esses arquivos mostram
somente nomes lógicos das operações, resultados, códigos MySQL e durações.

## Reversão

A reversão é deliberadamente separada do workload e exige aprovação do DBA.
Primeiro, remova o login-path local:

```sh
MYSQL_TEST_LOGIN_FILE=~/.config/agent-monitoring/mylogin.cnf \
mysql_config_editor remove --login-path=sakila-audit-demo
```

Depois, em sessão administrativa autorizada, remova a conta usando o mesmo
host controlado escolhido no provisionamento e elimine a fixture:

```sql
DROP USER IF EXISTS 'sakila_audit_demo'@'<host-controlado>';
DROP TABLE IF EXISTS sakila.audit_security_demo_events;
```

O executor nunca realiza essa reversão automaticamente.

## Resultado validado

Em 2026-09-16, o fluxo completo foi validado na instância configurada:

- preflight da conta e dos privilégios aprovado;
- operações controladas de DML aprovadas;
- ciclo de tabela temporária aprovado;
- tentativa permanente de `DROP TABLE` negada com erro MySQL 1142;
- alerta crítico aceito pelo MCP;
- notification confirmado em `dry_run`;
- alerta e relatórios registrados na inbox do DBA;
- Luna `low` classificou a análise como `consistent`;
- fixture finalizada com zero linhas;
- nenhum e-mail real enviado.

O relatório de implementação, evidências e limitações está em
`agents/dba/reports/2026-09-16-audit-security-demo-provisioning.md`.

Em 2026-09-17, os dois cenários separados foram validados novamente em produção:

- DROP e ALTER foram negados com erro MySQL 1142;
- `destructive_ddl` e `schema_change` foram aceitos pelo MCP;
- os dois e-mails retornaram `delivery_status=sent`;
- os dois alertas foram registrados na inbox do DBA;
- ciclos posteriores reconheceram os mesmos eventos como duplicados;
- monitor, advisor e workloads foram encerrados sem processos residuais.
