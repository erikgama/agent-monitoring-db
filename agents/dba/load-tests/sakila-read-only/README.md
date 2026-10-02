# Workloads de leitura Sakila

Dois executores independentes e exclusivamente de leitura. O workload misto em
`../sakila-heavy/` permanece inalterado. Os dois perfis executam **exatamente a
mesma query** `actor_popularity`; somente taxa e concorrência mudam, permitindo
comparar a latência sob baixa e alta pressão.

Todos os SELECTs são executados no schema `sakila`, dentro de
`START TRANSACTION READ ONLY`. Os scripts fazem preflight de identidade e
capacidade, exigem confirmação explícita e gravam relatórios próprios em
`reports/`. Não existe INSERT, UPDATE, DELETE, DDL ou ação corretiva.

## Arquivos desta pasta

| Arquivo | Responsabilidade |
|---|---|
| `sakila_read_steady.py` | Carga 1 fixa: referência de leitura a 5 TPS |
| `sakila_read_heavy.py` | Carga 2 fixa: mesma query com TPS e concorrência maiores |
| `sakila_read_demo_35.py` | executa o fluxo completo e calibra a Carga 2 |
| `sakila_read_common.py` | query canônica, transação read-only, workers e relatórios |
| `compare_read_reports.py` | compara dois JSONs e valida faixa, amostra e integridade |
| `reports/` | evidências Markdown e JSON de cada etapa |

Os três primeiros são executáveis. Os dois arquivos `common` e `compare` são
infraestrutura compartilhada local da demo.

## Execução recomendada: demo adaptativa de 35%

O orquestrador executa warm-up, Carga 1, calibrações curtas, Carga 2 oficial e
validação. Ele ajusta somente o TPS oferecido; não injeta atraso artificial e
interrompe se qualquer etapa tiver falha, rejeição ou retry.

```sh
python3 agents/dba/load-tests/sakila-read-only/sakila_read_demo_35.py \
  --execute \
  --confirm-target sakila \
  --confirm-demo LATENCY_35_SAKILA \
  --warmup-queries 10
```

Por padrão, a aprovação exige média entre 35% e 50%, pelo menos 300 conclusões
em cada carga oficial e 100% de conclusão limpa. As tentativas intermediárias
ficam registradas, sem substituir ou apagar resultados reprovados.

### Como o controlador adaptativo funciona

1. Executa exatamente 10 consultas de warm-up, excluídas da comparação.
2. Executa a Carga 1 por 60 segundos a 5 TPS.
3. Calcula o piso de latência como `média da Carga 1 x 1,35`.
4. Executa prévias curtas da Carga 2 e ajusta somente o TPS oferecido.
5. Executa janelas oficiais de 60 segundos, com até seis tentativas.
6. Quando há um ponto abaixo e outro acima da faixa, interpola o próximo TPS.
7. Encerra na primeira janela com aumento médio entre 35% e 50% e 100% de
   conclusão limpa.

O controlador não adiciona `SLEEP`, atraso no cliente ou alteração na query.
A latência registrada é a duração real da mesma consulta no MySQL. Uma rodada
pode terminar reprovada se nenhuma das seis janelas oficiais entrar na faixa.

### Parâmetros do orquestrador

| Parâmetro | Padrão | Uso |
|---|---:|---|
| `--warmup-queries` | 10 | quantidade exata de consultas de aquecimento, excluídas da comparação |
| `--measurement-seconds` | 60 | duração de cada carga oficial |
| `--calibration-seconds` | 20 | duração de cada prévia |
| `--baseline-tps` | 5,0 | taxa da Carga 1 |
| `--initial-loaded-tps` | 5,65 | primeiro candidato da Carga 2 |
| `--minimum-loaded-tps` | 5,3 | piso do controlador |
| `--maximum-loaded-tps` | 6,2 | teto do controlador |
| `--target-increase-percent` | 35 | referência usada nos ajustes abaixo do piso |
| `--minimum-accepted-increase-percent` | 35 | menor aumento médio aceito |
| `--maximum-accepted-increase-percent` | 50 | maior aumento médio aceito |
| `--calibration-attempts` | 4 | máximo de prévias curtas |
| `--official-attempts` | 6 | máximo de janelas oficiais da Carga 2 |
| `--minimum-completed` | 300 | amostra mínima em cada carga oficial |

Altere os limites somente quando a regra da demonstração mudar. Como o estado
do serviço varia, não fixe o TPS encontrado em uma rodada como garantia para a
rodada seguinte.

## Query canônica

A consulta agrega filmes, locações e receita por ator usando joins, `COUNT
DISTINCT`, `SUM`, agrupamento e ordenação. Sua definição única fica em
`sakila_read_common.py` e é importada pelos dois scripts, impedindo divergência
acidental entre baseline e carga.

### O que a query representa

A `actor_popularity` responde: **quais atores estão associados aos filmes com
mais locações e receita?** Ela percorre o relacionamento:

`actor -> film_actor -> inventory -> rental -> payment`

Cada linha do resultado representa um ator e contém:

- `films`: quantidade de filmes distintos do ator que possuem itens no
  estoque;
- `rentals`: quantidade de locações distintas desses filmes;
- `revenue`: soma dos pagamentos ligados às locações desses filmes.

O resultado é limitado aos 100 atores com maior `revenue`, usando `rentals` e
`actor_id` como critérios de desempate. A ordenação forma o ranking, mas a
consulta não retorna uma coluna numérica de posição.

### Limite de interpretação

`revenue` deve ser entendido como **receita associada aos filmes em que o ator
participou**, e não como receita gerada exclusivamente pelo ator. Quando um
filme possui vários atores, o valor integral de cada pagamento fica associado a
cada um deles. Portanto, somar a receita das linhas do resultado superestima a
receita total da locadora.

Essa semântica é adequada para comparar popularidade e produzir um workload
analítico de leitura. A consulta não deve ser usada como fechamento financeiro
global sem alterar a regra de atribuição da receita.

## 0. Warm-up não contabilizado

Antes da comparação, aqueça o caminho de leitura com exatamente 10 consultas.
O relatório desta etapa não entra no comparador. O limite determinante é
`--total-queries 10`; os três segundos abaixo são apenas a janela suficiente
para oferecer esse volume a 5 TPS:

```sh
python3 agents/dba/load-tests/sakila-read-only/sakila_read_steady.py \
  --execute \
  --confirm-target sakila \
  --confirm-read-only READ_STEADY_SAKILA \
  --target-tps 5 \
  --duration-seconds 3 \
  --total-queries 10 \
  --workers 16 \
  --queue-size 10000 \
  --query-timeout-seconds 30 \
  --max-retries 0 \
  --progress-interval-seconds 10 \
  --drain-queue
```

## 1. Carga 1: fluxo contínuo de referência

`sakila_read_steady.py` oferece 5 execuções/s por 60 segundos usando 16
workers. A expectativa é produzir 300 amostras. A fila é drenada e retries são
desabilitados: qualquer falha invalida a demo.

```sh
python3 agents/dba/load-tests/sakila-read-only/sakila_read_steady.py \
  --execute \
  --confirm-target sakila \
  --confirm-read-only READ_STEADY_SAKILA \
  --target-tps 5 \
  --duration-seconds 60 \
  --workers 16 \
  --queue-size 10000 \
  --query-timeout-seconds 30 \
  --max-retries 0 \
  --progress-interval-seconds 10 \
  --drain-queue
```

## 2. Carga 2: pressão controlada

`sakila_read_heavy.py` oferece a mesma query 5,65 vezes/s por 60 segundos
usando 20 workers. A expectativa é produzir 339 amostras e aproximar a latência
média de 35% de aumento, sem erro, rejeição ou retry. Esse TPS foi obtido por
calibração pontual da instância em 2026-09-14, mas a repetição mostrou que ele
não garante o percentual quando cache, CPU ou uso concorrente mudam.

```sh
python3 agents/dba/load-tests/sakila-read-only/sakila_read_heavy.py \
  --execute \
  --confirm-target sakila \
  --confirm-read-only READ_HEAVY_SAKILA \
  --target-tps 5.65 \
  --duration-seconds 60 \
  --workers 20 \
  --queue-size 10000 \
  --query-timeout-seconds 30 \
  --max-retries 0 \
  --progress-interval-seconds 10 \
  --drain-queue
```

O valor configurado é a taxa oferecida. O relatório separa `offered` de
`completed`. A demo somente é válida quando todas as consultas oferecidas são
concluídas e `failed`, `rejected` e `retries` permanecem em zero.

## Como interpretar a comparação

- `latency_avg_seconds`: métrica oficial usada para aprovar a demo;
- `latency_p50_seconds`: mediana, representando a consulta típica;
- `latency_p95_seconds` e `latency_p99_seconds`: cauda de latência;
- `latency_max_seconds`: maior duração observada;
- `completion_percent`: deve ser 100% nas duas cargas;
- `demo_validation.passed`: resultado final do conjunto de regras.

O limite de 35% a 50% vale para a **latência média**. P50, p95, p99 e máxima
são evidências auxiliares e podem variar fora dessa faixa sem mudar o resultado
do validador atual.

### Códigos de saída

- `0`: fluxo ou comparação aprovado;
- `2`: argumento, confirmação, preflight ou etapa inválida;
- `3`: fluxo executado, mas nenhuma tentativa oficial atingiu a faixa.

## Comparar os relatórios

```sh
python3 agents/dba/load-tests/sakila-read-only/compare_read_reports.py \
  agents/dba/load-tests/sakila-read-only/reports/<baseline>.json \
  agents/dba/load-tests/sakila-read-only/reports/<heavy>.json \
  --minimum-increase-percent 35 \
  --maximum-increase-percent 50 \
  --minimum-completed 300
```

O comparador exige que ambos os relatórios contenham exatamente a mesma query e
calcula diferença absoluta e percentual de média, p50, p95, p99 e máxima. Com
os parâmetros acima, retorna sucesso somente se as duas cargas estiverem
limpas, tiverem amostra suficiente e o aumento médio ficar entre 35% e 50%,
inclusive. A faixa explícita evita maquiar a variação natural de cache, CPU e
concorrência.

## Dry-run e testes

Sem `--execute`, nenhum acesso ao banco é realizado:

```sh
python3 agents/dba/load-tests/sakila-read-only/sakila_read_steady.py
python3 agents/dba/load-tests/sakila-read-only/sakila_read_heavy.py
```

Testes locais:

```sh
python3 -m unittest discover -s agents/dba/tests -p 'test_*.py' -v
```

Resultados antigos anteriores à adoção da query única não devem ser comparados
com os novos relatórios.

## Memória operacional

### Calibração da demo de 35% em 2026-09-14

Ensaios de 30 segundos, todos sem falhas, rejeições ou retries:

- 5 TPS: 150/150; média `1.572802 s`;
- 5,3 TPS: 159/159; média `1.737065 s` (`+10,4%` sobre 5 TPS);
- 5,5 TPS: 165/165; média `2.241776 s` (`+42,5%` sobre 5 TPS);
- 5,8 TPS: 174/174; média `2.930095 s` (`+86,3%` sobre 5 TPS);
- 7 TPS: 210/210; média `3.943679 s` (`+150,7%` sobre 5 TPS).

O ponto inicialmente calibrado para a Carga 2 é 5,65 TPS. Cada execução oficial
de 60 segundos deve ser validada pelo comparador; se sair da faixa, o resultado
é reprovado e nunca deve ser apresentado artificialmente como 35%.

### Execução oficial de 60 segundos em 2026-09-14 às 19:02

- Carga 1 a 5 TPS: 300/300, média `1.729359 s`, zero erro;
- Carga 2 a 5,45 TPS: 327/327, média `1.598086 s`, zero erro;
- diferença real da média: `-0.131273 s` (`-7.591%`).

O comparador marcou a demo como `REPROVADO`: a integridade e o tamanho das
amostras passaram, mas a meta de +35% não passou. A dispersão maior no início da
Carga 1 indica efeito de aquecimento/estado transitório. Antes da próxima demo,
é necessário incluir uma fase de warm-up não medida e recalibrar a Carga 2.
Evidência: `reports/sakila-read-comparison-2026-09-14T190521-0300.json`.

### Execução aprovada após warm-up em 2026-09-14 às 20:25

- warm-up: 5 TPS por 30 segundos, excluído da comparação;
- Carga 1 a 5 TPS: 300/300, média `1.438225 s`, zero erro;
- Carga 2 a 5,65 TPS: 339/339, média `1.935405 s`, zero erro;
- aumento real da média: `+0.497180 s` (`+34.569%`);
- validação de 35% ±2 pontos: `APROVADO`.

Evidências: `reports/sakila-read-steady-2026-09-14T202643-0300.json`,
`reports/sakila-read-heavy-2026-09-14T202930-0300.json` e
`reports/sakila-read-comparison-2026-09-14T202948-0300.json`.

### Teste de repetibilidade em 2026-09-14 às 20:33

- warm-up: 150/150, zero erro;
- Carga 1 a 5 TPS: 300/300, média `1.442950 s`, zero erro;
- Carga 2 a 5,65 TPS: 339/339, média `1.896978 s`, aumento `+31.465%`;
- tentativa a 5,675 TPS: 341/341, média `1.819897 s`, aumento `+26.123%`;
- ambas as comparações: `REPROVADO` para a faixa rígida de 33% a 37%.

A repetição prova que TPS fixo não controla exatamente a latência média de um
banco real. Para uma demo repetível sem falsificar a métrica, o próximo passo é
um perfil adaptativo que calibra a taxa antes da janela oficial e mede somente
a fase estabilizada. Evidências:
`reports/sakila-read-comparison-2026-09-14T203740-0300.json` e
`reports/sakila-read-comparison-2026-09-14T203943-0300.json`.

### Nova repetição em 2026-09-14 às 20:42

- warm-up: 150/150, zero erro, excluído da comparação;
- Carga 1 a 5 TPS: 300/300, média `1.460610 s`, zero erro;
- Carga 2 a 5,65 TPS: 339/339, média `1.852656 s`, zero erro;
- aumento da média: `+26.841%`, reprovado para a faixa de 33% a 37%;
- aumento do p95: `+36.040%`, dentro da faixa, mas não substitui a meta de
  média solicitada.

Evidência: `reports/sakila-read-comparison-2026-09-14T204618-0300.json`.

### Primeira execução adaptativa aprovada em 2026-09-14 às 20:50

- baseline: 300/300 a 5 TPS, média `1.450236 s`;
- alvo calculado: `1.957819 s`;
- Carga 2 oficial aprovada: 344/344 a 5,732 TPS, média `1.960560 s`;
- aumento real da média: `+35.189%`;
- aumento do p95: `+35.067%`;
- falhas, rejeições e retries: zero;
- validação de 35% ±2 pontos: `APROVADO`.

Evidência: `reports/sakila-read-comparison-2026-09-14T205656-0300.json`.

### Execução invalidada após correção do limite máximo

- baseline: 300/300 a 5 TPS, média `1.947885 s`;
- Carga 2 aceita: 366/366 a 6,087 TPS, média `3.370266 s`;
- aumento real da média: `+73.022%`;
- falhas, rejeições e retries: zero;
- validação aplicada durante a execução: 35% a 100%, `APROVADO` naquele momento;
- regra corrigida posteriormente pelo usuário: 35% a 50%, portanto este
  resultado de `+73.022%` passa a ser `REPROVADO`.

O fluxo ainda executou uma terceira tentativa porque havia iniciado com o
validador anterior de 33% a 37%. O código foi corrigido depois da execução; nas
próximas rodadas, ele encerra somente quando a Carga 2 oficial estiver entre
35% e 50%. Evidência histórica:
`reports/sakila-read-comparison-2026-09-14T210736-0300.json`.

### Primeira execução aprovada com a faixa final de 35% a 50%

- baseline: 300/300 a 5 TPS, média `1.507246 s`;
- primeira Carga 2 oficial a 5,45 TPS: aumento `+9.202%`, reprovada;
- Carga 2 recalibrada a 5,708 TPS: 343/343, média `2.186046 s`;
- aumento real da média: `+45.036%`;
- falhas, rejeições e retries: zero;
- validação inclusiva de 35% a 50%: `APROVADO`.

Evidência atual da demo:
`reports/sakila-read-comparison-2026-09-14T211328-0300.json`.

### Segunda execução aprovada com a faixa final de 35% a 50%

- baseline: 300/300 a 5 TPS, média `1.431439 s`;
- Carga 2 final: 341/341 a 5,677 TPS, média `2.083108 s`;
- aumento real da média: `+45.525%`;
- aumento do p95: `+48.100%`;
- falhas, rejeições e retries: zero;
- validação inclusiva de 35% a 50%: `APROVADO`.

Evidência:
`reports/sakila-read-comparison-2026-09-14T212553-0300.json`.

### Terceira execução aprovada com a faixa final de 35% a 50%

- baseline: 300/300 a 5 TPS, média `1.441576 s`;
- Carga 2 final: 343/343 a 5,704 TPS, média `1.966358 s`;
- aumento real da média: `+36.403%`;
- aumento do p95: `+36.763%`;
- falhas, rejeições e retries: zero;
- validação inclusiva de 35% a 50%: `APROVADO`.

Esta rodada precisou da quinta tentativa oficial. O limite padrão do controlador
foi ampliado de três para cinco tentativas naquele momento. Evidência:
`reports/sakila-read-comparison-2026-09-14T222837-0300.json`.

### Quarta execução aprovada com a faixa final de 35% a 50%

- baseline: 300/300 a 5 TPS, média `1.491232 s`;
- último ponto abaixo da faixa: 5,71 TPS, aumento `+21.049%`;
- ponto acima da faixa: 5,85 TPS, aumento `+65.293%`;
- Carga 2 interpolada: 347/347 a 5,778 TPS, média `2.111506 s`;
- aumento real da média: `+41.595%`;
- falhas, rejeições e retries: zero;
- validação inclusiva de 35% a 50%: `APROVADO`.

O controlador passou a guardar os pontos oficiais abaixo e acima da faixa,
interpolar o próximo TPS e permitir até seis tentativas. Evidência:
`reports/sakila-read-comparison-2026-09-14T224133-0300.json`.

### Saturação histórica de 1.000 TPS em 2026-09-14

As duas execuções abaixo usaram a mesma query `actor_popularity` e a mesma
janela de oferta de 60 segundos:

- baseline: 60 oferecidas, 60 concluídas, zero falhas; média `1.256906 s` e
  máxima `2.325347 s`;
- carga: 60.000 oferecidas, 2 concluídas, 512 falhas por timeout e 59.486
  rejeitadas localmente; média das conclusões `26.564081 s` e máxima
  `28.663822 s`;
- diferença observada nas consultas concluídas: `+25.307175 s` na média
  (`+2013.45%`) e `+26.338475 s` na máxima (`+1132.669%`).

Relatórios: `reports/sakila-read-steady-2026-09-14T180708-0300.json`,
`reports/sakila-read-heavy-2026-09-14T180902-0300.json` e
`reports/sakila-read-comparison-2026-09-14T181024-0300.json`.

A média pesada contém somente duas conclusões e exclui as execuções que
atingiram o timeout de 30 segundos. Ela comprova saturação nessa configuração,
mas não deve ser tratada como estimativa estatística estável da latência.
