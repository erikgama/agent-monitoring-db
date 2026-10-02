# Testes de carga pesada no Sakila

> Guia atual de instalação: README.md da raiz e docs/SETUP.md.
> Nos exemplos abaixo, defina `AGENT_MONITORING_ROOT="$(pwd)"` na raiz do clone.

Esta pasta contém os dois geradores de escrita e leitura mais agressivos do
agente DBA. Ambos são limitados ao schema `sakila`, usam o perfil MySQL local do
projeto e exigem confirmações explícitas antes de acessar o banco.

> Estes testes podem pressionar conexões, CPU, I/O, locks e filas do serviço.
> Execute somente em uma janela autorizada e acompanhe o relatório final.

## Arquivos

| Arquivo | Modelo | Quando usar |
| --- | --- | --- |
| `sakila_heavy_load.py` | Carga controlada com workers e limite agregado | Para aumentar gradualmente a carga e medir o throughput que o banco consegue concluir. |
| `sakila_open_loop_1000tps.py` | Gerador open-loop de taxa oferecida | Para oferecer uma taxa fixa, mesmo quando o banco está respondendo mais devagar e formando backlog. |
| `reports/` | Evidências locais em JSON e Markdown | Para conferir parâmetros, contagens, latências, erros e limpeza de cada execução. |

Os scripts reutilizam as operações seguras definidas em
`../../scripts/sakila_realistic_workload.py`. Nenhuma credencial é armazenada
nesta pasta.

## Operações executadas

O perfil padrão `insert-read-heavy` trabalha em grupos de dez transações
lógicas:

- quatro `INSERT` de aluguel e pagamento sintéticos;
- quatro `READ` do fluxo recém-criado;
- um `UPDATE` em lote sobre registros sintéticos controlados pelo teste;
- um `DELETE` em lote para devolver/remover os mesmos registros.

Isso produz uma distribuição lógica de 40% INSERT, 40% READ, 10% UPDATE e 10%
DELETE. O modo opcional `balanced-cycle` executa INSERT, READ, UPDATE e DELETE
em proporção de 25% para cada tipo.

Somente dados sintéticos identificados pelo próprio executor podem ser
alterados ou removidos. O cleanup tenta eliminar qualquer pendência no final.
O efeito persistente esperado é apenas o avanço dos `AUTO_INCREMENT` de
`rental` e `payment`.

## Diferença entre os dois modelos

### Carga controlada

`sakila_heavy_load.py` mantém um conjunto de conexões persistentes e regula o
ritmo agregado. O `--target-tps` é uma meta/teto, não uma garantia: se o banco
não tiver capacidade, o throughput concluído ficará abaixo dela.

Valores padrão:

- 64 workers/conexões;
- ramp-up de 60 segundos;
- 600 segundos de carga depois do ramp-up;
- meta de 1.000 transações lógicas por segundo;
- limite absoluto de 1.000.000 de transações;
- modo `insert-read-heavy`.

### Open-loop

`sakila_open_loop_1000tps.py` separa produção e execução. O produtor oferece
trabalho na taxa configurada, enquanto os workers consomem a fila na velocidade
que o banco suporta. Por isso, `offered_tps` pode permanecer em 1.000 mesmo que
`completed_tps` seja menor.

As métricas não são equivalentes:

- `offered`: transações lógicas programadas pelo produtor;
- `enqueued`: transações aceitas na fila local;
- `completed`: transações que chegaram ao fim no banco;
- `failed_batches`: lotes que falharam mesmo depois dos retries configurados;
- `retry_attempts`: novas tentativas realizadas;
- `rejected`: trabalho que não entrou na fila ou foi descartado ao encerrar;
- `backlog`: diferença ainda pendente entre o que foi oferecido e concluído.

Oferecer 600.000 transações não significa que 600.000 foram concluídas. Essa
confirmação deve vir das métricas finais do relatório.

## Pré-requisitos e proteções

- Execute a partir da raiz `${AGENT_MONITORING_ROOT}`.
- O alvo é validado como o schema `sakila` no MySQL Cloud/HeatWave configurado
  no perfil local do projeto.
- O preflight rejeita schema errado, servidor read-only, ambiente incompatível
  e ausência das tabelas obrigatórias.
- O número de workers é comparado com `max_connections`, preservando uma margem
  de cinco conexões.
- `sakila.payment.payment_id` deve ser `INT UNSIGNED AUTO_INCREMENT`. O tipo
  original `SMALLINT UNSIGNED` alcança o limite 65.535 rapidamente.
- Um timeout recicla somente a conexão do worker afetado. Leases com resultado
  incerto são recuperadas por identificadores controlados pelo teste.
- O dry-run não abre conexão e pode ser usado para conferir a configuração.

## 1. Carga pesada controlada

Dry-run:

```sh
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH=agents/dba/load-tests/sakila-heavy:agents/dba/scripts \
python3 agents/dba/load-tests/sakila-heavy/sakila_heavy_load.py
```

Execução com os padrões pesados:

```sh
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH=agents/dba/load-tests/sakila-heavy:agents/dba/scripts \
python3 agents/dba/load-tests/sakila-heavy/sakila_heavy_load.py \
  --execute \
  --confirm-target sakila \
  --confirm-heavy-load HEAVY_LOAD_SAKILA
```

Exemplo limitado a 100 TPS e 16 workers:

```sh
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH=agents/dba/load-tests/sakila-heavy:agents/dba/scripts \
python3 agents/dba/load-tests/sakila-heavy/sakila_heavy_load.py \
  --execute \
  --confirm-target sakila \
  --confirm-heavy-load HEAVY_LOAD_SAKILA \
  --duration-seconds 600 \
  --workers 16 \
  --ramp-up-seconds 60 \
  --target-tps 100 \
  --max-transactions 100000
```

Parâmetros principais:

| Parâmetro | Função |
| --- | --- |
| `--workers` | Quantidade de workers/conexões persistentes, entre 1 e 1.000. |
| `--ramp-up-seconds` | Tempo para ativar os workers gradualmente. |
| `--duration-seconds` | Duração da janela sustentada após o ramp-up. |
| `--target-tps` | Meta/teto agregado; zero usa a capacidade disponível. |
| `--max-transactions` | Trava absoluta de volume. |
| `--transaction-timeout-seconds` | Timeout de cada chamada ao banco. |
| `--mode` | `insert-read-heavy` ou `balanced-cycle`. |
| `--insert-burst` / `--reads-per-insert` | Ajustam o peso de INSERT e READ. |
| `--updates-per-batch` / `--deletes-per-batch` | Ajustam UPDATE e DELETE por lote. |
| `--maintenance-batch-size` | Linhas processadas por UPDATE/DELETE de manutenção. |
| `--progress-interval-seconds` | Intervalo dos indicadores exibidos no terminal. |

Uma execução controlada saudável deve terminar sem transações falhas e sem
falhas de cleanup. Compare `throughput`, latência média, p95 e máxima com o
objetivo da rodada; atingir exatamente `target_tps` depende da capacidade do
banco.

## 2. Carga open-loop de 1.000 TPS

Dry-run:

```sh
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH=agents/dba/load-tests/sakila-heavy:agents/dba/scripts \
python3 agents/dba/load-tests/sakila-heavy/sakila_open_loop_1000tps.py
```

Comando de referência para oferecer 1.000 transações lógicas por segundo por
dez minutos:

```sh
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH=agents/dba/load-tests/sakila-heavy:agents/dba/scripts \
python3 agents/dba/load-tests/sakila-heavy/sakila_open_loop_1000tps.py \
  --execute \
  --confirm-target sakila \
  --confirm-open-loop OPEN_LOOP_SAKILA \
  --target-tps 1000 \
  --duration-seconds 600 \
  --workers 512 \
  --queue-size 100000 \
  --transaction-timeout-seconds 120 \
  --max-retries 2 \
  --progress-interval-seconds 30 \
  --drain-queue
```

Durante os 600 segundos, o produtor tenta oferecer 600.000 transações. Com
`--drain-queue`, a produção para aos dez minutos, mas o processo continua até
consumir a fila e concluir retries. Portanto, o tempo total pode ser muito
maior que dez minutos.

Sem `--drain-queue`, os jobs restantes são encerrados e contabilizados como
`rejected`. Isso reduz o tempo depois da janela, mas significa que parte do
trabalho oferecido não executou.

Parâmetros principais:

| Parâmetro | Função |
| --- | --- |
| `--target-tps` | Taxa lógica que o produtor tenta oferecer. |
| `--duration-seconds` | Janela de produção. |
| `--total-transactions` | Limite opcional de transações oferecidas; deve ser múltiplo de dez. |
| `--workers` | Consumidores simultâneos da fila e suas conexões. |
| `--queue-size` | Capacidade máxima do backlog local. |
| `--transaction-timeout-seconds` | Timeout de cada lote executado. |
| `--max-retries` | Novas tentativas permitidas para um lote falho. |
| `--progress-interval-seconds` | Intervalo do progresso no terminal. |
| `--drain-queue` | Aguarda o backlog terminar depois da janela de produção. |

Para considerar a meta completa, valide no relatório final:

- `offered=600000` e `completed=600000`;
- `failed_batches=0`;
- `rejected=0`;
- backlog final igual a zero;
- `cleanup_failures` vazio.

Retries bem-sucedidos evitam perda de trabalho, mas continuam indicando pressão
ou instabilidade e ficam contabilizados em `retry_attempts`. Se o requisito for
zero retry, esse campo também precisa terminar em zero.

## Relatórios e interpretação

Cada execução real grava um par `.json` e `.md` em `reports/`. O JSON é a fonte
estruturada; o Markdown é o resumo para leitura. Confira sempre:

- data, alvo, parâmetros e confirmações da execução;
- volume oferecido, concluído, falho e rejeitado;
- TPS oferecido e efetivamente concluído;
- latência média, p95 e máxima;
- distribuição entre INSERT, READ, UPDATE e DELETE;
- retries, mensagens de erro e falhas de cleanup.

Consulte também [`reports/README.md`](reports/README.md) para os nomes e a forma
de interpretar os artefatos.
