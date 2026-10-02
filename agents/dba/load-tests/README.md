# Testes de carga do DBA

> Guia atual de instalação: README.md da raiz e docs/SETUP.md.
> Nos exemplos abaixo, defina `AGENT_MONITORING_ROOT="$(pwd)"` na raiz do clone.

Esta pasta reúne workloads controlados para o schema `sakila`. Eles servem a
objetivos diferentes e não devem ser tratados como variantes equivalentes do
mesmo teste.

## Catálogo

| Cenário | Script principal | Operações | Objetivo |
|---|---|---|---|
| Carga 1 de leitura | `sakila-read-only/sakila_read_steady.py` | somente SELECT | criar a referência de latência |
| Carga 2 de leitura | `sakila-read-only/sakila_read_heavy.py` | somente SELECT | medir a mesma query sob maior pressão |
| Demo adaptativa | `sakila-read-only/sakila_read_demo_35.py` | somente SELECT | manter o aumento médio entre 35% e 50% |
| Carga pesada controlada | `sakila-heavy/sakila_heavy_load.py` | INSERT, READ, UPDATE e DELETE | simular fluxo transacional sustentado |
| Open-loop de 1.000 TPS | `sakila-heavy/sakila_open_loop_1000tps.py` | INSERT, READ, UPDATE e DELETE | oferecer taxa fixa, aceitando formação de fila |
| Demo Audit Security | `sakila-audit-security/sakila_audit_security_demo.py` | DML controlado, DDL temporário e DROP negado | validar Enterprise Audit, alerta e rotas MCP com conta restrita |

Arquivos auxiliares como `sakila_read_common.py` e
`compare_read_reports.py` não são workloads independentes. Eles concentram a
query comum, as proteções read-only, a geração dos relatórios e a comparação.

## Qual cenário usar

- Para demonstrar apenas aumento de latência de leitura, use a demo adaptativa.
- Para executar manualmente e comparar Carga 1 e Carga 2, use os dois scripts
  read-only e depois `compare_read_reports.py`.
- Para um fluxo realista de escrita e leitura com taxa limitada, use
  `sakila_heavy_load.py`.
- Para oferecer 1.000 transações lógicas por segundo independentemente da
  capacidade de conclusão do banco, use `sakila_open_loop_1000tps.py`.
- Para validar a captura do Enterprise Audit e o alerta de DDL destrutivo
  negado, use a conta restrita e o roteiro de `sakila-audit-security/README.md`.

## Regras comuns

- Execute os comandos a partir da raiz
  `${AGENT_MONITORING_ROOT}`.
- O único schema autorizado para o DBA é `sakila`.
- Sem `--execute`, os scripts fazem dry-run e não acessam o banco.
- Uma execução real exige os tokens explícitos de confirmação do cenário.
- Nenhum script armazena credenciais nesta pasta; o perfil de conexão local já
  configurado é apenas referenciado.
- Cada executor faz preflight do alvo e, quando aplicável, de capacidade de
  conexões e estrutura necessária.
- Relatórios ficam dentro da subpasta `reports/` do próprio cenário.
- Não conclua que a meta foi atingida olhando apenas o progresso do terminal;
  use o JSON final e os critérios documentados no cenário.

## Diferença entre taxa oferecida e concluída

`target_tps` é a taxa que o produtor tenta oferecer. Ela não garante que o
banco conclua a mesma taxa. Os relatórios separam:

- `offered_tps`: trabalho produzido por segundo;
- `completed_tps` ou `throughput_tps`: trabalho realmente concluído;
- `rejected`: trabalho que não entrou ou foi descartado localmente;
- `failed`: trabalho enviado, mas encerrado com erro;
- `retries`: novas tentativas; quando o cenário exige zero retry, qualquer
  valor diferente de zero reprova a rodada.

## Segurança e efeitos

Os testes read-only usam `START TRANSACTION READ ONLY` e não alteram dados. Os
testes em `sakila-heavy/` alteram somente registros sintéticos conhecidos pelo
executor, tentam removê-los ao final e deixam como efeito persistente esperado
o avanço dos `AUTO_INCREMENT` de `rental` e `payment`.

Uma carga alta pode aumentar latência, consumir CPU, I/O e conexões ou afetar
outros usuários. Sempre revise duração, TPS, workers, timeout e comportamento
da fila antes de usar `--execute`.

## Guias detalhados

- `sakila-read-only/README.md`: Carga 1, Carga 2, demo adaptativa e comparação.
- `sakila-heavy/README.md`: carga transacional e open-loop de 1.000 TPS.
- `sakila-audit-security/README.md`: demonstração de segurança com conta
  dedicada, fixture descartável e DDL negado.

## Testes locais do código

```sh
PYTHONDONTWRITEBYTECODE=1 \
python3 -m unittest discover -s agents/dba/tests -p 'test_*.py' -v
```

Esses testes validam a lógica local e não executam workload no MySQL.
