# MySQL Security Monitor

Este projeto coleta um retrato de segurança do MySQL Enterprise, converte os dados em JSON estruturado e gera uma visão HTML para pessoas. O objetivo é fornecer evidências para um DBA e para um agente de IA detectarem riscos de postura, atividades destrutivas, falhas de autenticação e comportamento suspeito.

O monitor é de detecção: as consultas do coletor são somente leitura. As únicas rotinas que alteram o banco são o provisionamento opcional de contas demo e o simulador explicitamente protegido por `--environment staging --execute`.

## Visão geral

```text
MySQL Enterprise Audit + metadados do MySQL
                 │
                 ▼
  collect_security_snapshot.py
      ├─ queries/*.sql              postura, contas, privilégios e sessões
      ├─ leitura paginada do Audit  conexões, DDL, erros e Sakila
      └─ policy/security-rules.json regras determinísticas
                 │
                 ├─ latest.json    evidência detalhada para IA/integrações
                 ├─ summary.json   triagem rápida
                 └─ latest.html    leitura humana

run_controlled_security_exercise.py ──► eventos reais de demonstração ──► coletor
```

## Componentes

| Componente | O que faz | Altera o MySQL? |
|---|---|---|
| `collect_security_snapshot.py` | Orquestra a coleta, avalia a política e publica JSON/HTML. | Não |
| `queries/*.sql` | Consultas separadas por domínio de segurança. | Não |
| `policy/security-rules.json` | Regras declarativas e orientação para a IA. | Não |
| `provision_demo_accounts.py` | Cria contas persistentes, com privilégio mínimo, para demonstração. | Sim, somente com confirmação explícita |
| `run_controlled_security_exercise.py` | Gera eventos reais e controlados para validar o monitor. | Sim, somente em staging e com confirmação explícita |
| `demo-accounts.local.json` | Credenciais aleatórias das contas demo. Arquivo local com permissão `0600`. | Não enviar à IA nem versionar |
| `requirements.txt` | Dependência Python: `mysql-connector-python`. | Não |

## Consultas por domínio

Cada arquivo em `queries/` representa um tema independente. O coletor continua quando um tema falha; nesse caso, o domínio aparece como `unavailable` no resultado.

| Arquivo | Evidência coletada |
|---|---|
| `00_instance_security.sql` | Versão, exigência de TLS, validade padrão de senha, `LOCAL INFILE` e estado do Enterprise Audit. |
| `10_active_connections.sql` | Conexões existentes no instante da coleta, transporte e origem pseudonimizada. |
| `20_accounts.sql` | Contas, hosts amplos, anonimato e postura de autenticação. |
| `30_global_privileges.sql` | Privilégios globais elevados e permissões delegáveis. |
| `40_roles.sql` | Papéis e relações entre contas e roles. |
| `50_audit_preflight.sql` | Visibilidade e configuração necessária para ler o Enterprise Audit. |
| `60_audit_connections.sql` | Contrato/consulta de eventos de conexão do Audit. |
| `70_audit_ddl.sql` | Contrato/consulta de eventos DDL, como `CREATE`, `ALTER`, `DROP` e `TRUNCATE`. |
| `80_audit_errors.sql` | Contrato/consulta de erros de conexão e comandos. |
| `90_audit_sakila_data_access.sql` | Contrato/consulta de acessos a tabelas do schema Sakila. |

Os quatro últimos domínios são materializados pelo leitor paginado no Python, e os SQLs permanecem na pasta como documentação e referência do formato Audit.

## Artefatos produzidos

O destino é definido por `--output-dir`. Em uma execução normal, o coletor escreve os três arquivos abaixo de forma atômica.

### `latest.json` — fonte de evidência

É o documento principal para um agente de IA. Contém todos os domínios, linhas coletadas, achados, a política avaliada e qualidade da coleta.

```json
{
  "audit_id": "identificador-único-do-snapshot",
  "collected_at": "data-hora-UTC",
  "collector": {"audit_window_minutes": 1, "audit_max_events": 5000},
  "data_quality": {"unavailable_domains": [], "truncated_domains": []},
  "policy_evaluation": {"status": "healthy|attention|critical", "violations": []},
  "domains": {
    "audit_ddl": {"status": "critical", "findings": [], "rows": []}
  }
}
```

Campos importantes em um evento DDL:

| Campo | Significado |
|---|---|
| `sql_command` | Tipo normalizado do comando, por exemplo `drop_table`. |
| `outcome` e `status_code` | Resultado e código MySQL (`0` = sucesso; `1142` = privilégio insuficiente). |
| `security_assessment` | Severidade determinada pelo coletor para o evento. |
| `assessment_reason` | Motivo normalizado, como `blocked_destructive_ddl_possible_sabotage`. |
| `llm_interpretation` | Orientação de interpretação que a IA deve respeitar. |
| `actor_id` e `source_id` | Hashes estáveis que permitem correlacionar ator e origem sem expor identidade. |

### `summary.json` — triagem

É a entrada curta para decidir se vale enviar o JSON detalhado a uma IA. Tem estado geral, quantidade de achados, estado de cada domínio, estado da política e limitações de qualidade.

Use-o para alertas e roteamento. Se `overall_status` for `critical`, ou se algum domínio relevante estiver `attention`/`critical`, encaminhe também `latest.json`.

### `latest.html` — painel humano

Mostra o mesmo snapshot em cartões: estado do domínio, achados e amostra das evidências. Ele não é a fonte de verdade para automação; a integração deve sempre consumir os JSONs.

## Status e severidade

| Valor | Significado operacional |
|---|---|
| `healthy` | Nenhuma regra local encontrou problema. Não prova ausência de incidente. |
| `attention` | Requer validação; pode ser configuração inadequada ou mudança autorizada. |
| `critical` | Exige investigação prioritária. Não é prova conclusiva de invasão. |
| `unavailable` | O monitor não conseguiu coletar o domínio. Nunca interpretar como normalidade. |

Regra especial de DDL:

- `DROP` bem-sucedido é `attention`: pode corresponder a mudança aprovada, mas requer validação de autorização, escopo e impacto.
- `DROP` bloqueado é `critical`: representa tentativa de ação destrutiva sem privilégio e pode indicar sabotagem, abuso de privilégio ou conta comprometida.
- Alterações de identidade (`CREATE USER`, `ALTER USER`, `DROP USER`) são críticas até serem vinculadas a uma alteração aprovada.

## Qualidade da evidência: regra obrigatória para IA

Antes de analisar qualquer risco, leia `data_quality`.

- `unavailable_domains`: o domínio não foi consultado. Não conclua que não houve eventos nele.
- `truncated_domains`: havia mais linhas que o limite de exportação. Os eventos presentes são válidos, mas sua ausência não é conclusiva.
- O leitor percorre páginas de `audit_log_read()` até `audit_max_events` (padrão 5.000). Se o limite for atingido, a limitação é registrada.

Uma IA deve declarar essas limitações na resposta, em vez de afirmar “não houve atividade suspeita”.

## Política declarativa

`policy/security-rules.json` é a referência de postura esperada. Ele permite definir, sem alterar o banco:

- TLS obrigatório e `LOCAL INFILE` proibido;
- validade máxima de senha;
- limite de contas com host amplo;
- limite de privilégios elevados delegáveis;
- IPs permitidos para conexões;
- IPs e janelas UTC permitidos por usuário;
- orientação explícita para a IA sobre DDL, sabotagem e dados truncados.

Preencha `network.allowed_source_ips` e `account_access` com os valores reais antes de usar a política em produção. Enquanto a lista global de IPs estiver vazia, a regra de origem é marcada como `not_evaluated`, não como violação.

## Como conectar a um agente de IA

Envie, nesta ordem:

1. `policy/security-rules.json` — expectativa e regras de interpretação.
2. `summary.json` — triagem do snapshot.
3. `latest.json` — evidência, somente se a triagem exigir investigação ou para análise completa.

Nunca envie `demo-accounts.local.json`, variáveis de ambiente, certificados ou senhas.

Instrução sugerida para o agente:

```text
Analise a política, o resumo e o snapshot do MySQL. Primeiro, declare os domínios
indisponíveis ou truncados. Em seguida, liste evidências por severidade e correlacione
actor_id, source_id, timestamps e sql_fingerprint. Trate `llm_interpretation` como regra
de análise. Não afirme invasão como fato: diferencie evidência, hipótese e ação recomendada.
Para um DROP bloqueado, considere possível sabotagem e recomende validação imediata de
origem, conta, falhas de login e mudanças aprovadas. Não proponha alterações automáticas
no MySQL; indique ações para validação de um DBA.
```

## Coleta manual por um agente de IA

Um agente pode disparar o coletor manualmente quando precisar de evidência atualizada, por exemplo após identificar um alerta crítico, antes de uma análise sob demanda ou para validar a configuração do Audit. Essa operação é somente leitura no MySQL.

O ambiente de execução deve injetar `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_DATABASE` e, quando aplicável, `MYSQL_SSL_CA`. O agente não deve solicitar, registrar ou colocar a senha no comando; ela deve vir de um secret manager, variável protegida ou perfil de execução já configurado.

```bash
cd /opt/mysql-security-monitor
.venv/bin/python collect_security_snapshot.py \
  --output-dir /var/lib/mysql-security-monitor/manual/2026-09-15T130000Z \
  --audit-window-minutes 5 \
  --audit-max-events 5000 \
  --max-rows 500
```

| Parâmetro | Uso recomendado |
|---|---|
| `--output-dir` | Use sempre um diretório novo e identificável para não sobrescrever a evidência do cron. |
| `--audit-window-minutes` | Janela de eventos entre 1 e 1.440 minutos. Use 5 para investigação recente; aumente apenas se o volume permitir. |
| `--audit-max-events` | Teto de eventos Audit paginados, de 1 a 50.000. Aumente somente se `truncated_domains` indicar necessidade. |
| `--max-rows` | Máximo de linhas exportadas por domínio. Aumentar amplia o JSON enviado à IA. |
| `--history-dir` | Opcional; grava cópia imutável detalhada do snapshot para investigação posterior. |
| `--policy-file` | Opcional; use outra política somente se ela tiver sido aprovada para aquele ambiente. |

Após executar, o agente deve ler primeiro `summary.json`, depois `latest.json`, e registrar no relatório o caminho da coleta manual, `audit_id`, `collected_at` e qualquer valor em `data_quality`.

O agente pode chamar `run_controlled_security_exercise.py` somente com autorização explícita para staging. Ele não deve chamar o provisionador nem o simulador para investigar produção.

## Contas e simulador de demonstração

`provision_demo_accounts.py` cria as contas abaixo no ambiente de teste. Todas usam privilégio mínimo em Sakila; nenhuma recebe privilégio administrativo real.

| Conta | Finalidade |
|---|---|
| `security_demo_reader` | Leitura em Sakila; usada para tentativa de `DROP` que deve falhar. |
| `security_demo_operator` | Leitura e tabelas temporárias; usada para DDL sem objeto persistente. |
| `security_demo_admin` | Conta de conexão demo; usada para falhas de senha controladas. |
| `security_demo_locked` | Conta bloqueada; usada para evento de login negado. |

O simulador produz: leitura permitida, `DROP` bloqueado, criação/remoção de tabela temporária, três falhas de senha e login em conta bloqueada. Ele não remove tabelas persistentes, não testa brute force contra contas reais, não eleva privilégios e não gera carga deliberada.

```bash
MYSQL_HOST=... MYSQL_USER=... MYSQL_PASSWORD=... \
python3 run_controlled_security_exercise.py --environment staging --execute --collect
```

Para testar “mesmo usuário vindo de IPs distintos”, rode o simulador em duas máquinas de staging com origens de rede diferentes. Um único runner não deve tentar falsificar o IP auditado.

## Instalação e coleta agendada

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

export MYSQL_HOST=db.example.internal MYSQL_PORT=3306
export MYSQL_USER=security_collector
export MYSQL_PASSWORD='fornecido-por-secret-manager'
export MYSQL_DATABASE=mysql
export MYSQL_SSL_CA=/caminho/ca.pem

.venv/bin/python collect_security_snapshot.py \
  --output-dir /var/lib/mysql-security-monitor/current \
  --history-dir /var/lib/mysql-security-monitor/history \
  --audit-window-minutes 1
```

O usuário coletor precisa de acesso de leitura a `mysql`, `information_schema` e `performance_schema`. `PROCESS` aumenta a visibilidade de sessões. Para eventos Audit, precisa de `AUDIT_ADMIN`, Audit ativo e formato JSON.

Exemplo de cron a cada minuto:

```cron
* * * * * /opt/mysql-security-monitor/.venv/bin/python /opt/mysql-security-monitor/collect_security_snapshot.py --output-dir /var/lib/mysql-security-monitor/current --history-dir /var/lib/mysql-security-monitor/history >> /var/log/mysql-security-monitor.log 2>&1
```

Use uma pequena sobreposição entre a janela Audit e o intervalo de execução. Ao manter histórico, deduplique eventos por `server_uuid`, `timestamp` e `event_id` antes de calcular tendências.

## Segurança operacional

- Não inclua senhas em argumentos de linha de comando nem nos JSONs enviados à IA.
- Armazene `MYSQL_PASSWORD` em secret manager ou ambiente protegido.
- Proteja diretórios de saída, especialmente snapshots com hashes correlacionáveis.
- Revise periodicamente a política, os privilégios do coletor e o filtro do Enterprise Audit.
- O monitor recomenda investigação; ele não bloqueia usuários nem muda configurações automaticamente.
