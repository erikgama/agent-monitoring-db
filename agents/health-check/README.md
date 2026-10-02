# MySQL HeatWave Health Check

> Instalação atual: `docs/SETUP.md` da raiz. Banco e LLM são definidos em
> `config/agent-monitoring.toml`; Codex, Claude Code e Kimi Code usam os mesmos
> contratos. Modelos e execuções citados no histórico são registros anteriores.

Health Check somente leitura para MySQL HeatWave. Coleta sinais de saúde e
performance, gera JSON e HTML e pode publicar alertas pelo MCP central.

## Requisitos

- Python 3.11 ou superior;
- [uv](https://docs.astral.sh/uv/);
- cliente MySQL configurado conforme [CONEXAO.md](CONEXAO.md).

## Instalação

No diretório `agents/health-check`:

```sh
uv sync --extra dev
```

## Executar o Health Check

```sh
uv run mysql-health-check collect
```

O comando atualiza:

- `general_report/results/report.json`: resultado completo;
- `general_report/results/report.html`: visualização para abrir no navegador;
- `logs/collector.log`: log operacional sanitizado.

Cada domínio do relatório declara seu escopo. Workload, sessões ativas,
tabelas e índices são filtrados por `sakila`; conexões, InnoDB, erros e
replicação representam toda a instância; locks combinam esperas de `sakila`
com o contador global de deadlocks. Métricas globais nunca devem ser
atribuídas exclusivamente ao schema.

Para ler o último resultado sem acessar o MySQL:

```sh
uv run mysql-health-check read-latest
```

## Recursos opcionais

Monitor contínuo da latência da query configurada:

```sh
uv run mysql-health-latency monitor
```

Agente Luna responsável pela decisão e publicação, executado separadamente:

```sh
uv run mysql-health-advisor
```

O Luna verifica a existência de uma nova coleta a cada 15 segundos, tanto no
entrypoint direto quanto no laboratório do app.

Sem habilitação explícita, a coleta básica não inicia o MCP e não envia e-mail.

## Regra do agente e recorrência

O coletor captura a latência e grava `select_latency/results/latest.html`, sem
avaliar thresholds e sem chamar o MCP. O agente Luna lê o HTML completo e a
[regra do agente](advisor/rules.md). P99 estritamente maior que 2 segundos
produz uma decisão de alerta crítico `query_latency`; exatamente 2 segundos
não alerta. Evidência ausente ou inconsistente produz `inconclusive`.

Somente depois da decisão `alert`, o processo do Luna executa a coleta geral
somente leitura, monta e valida `health_check_alert.v1` e chama
`incident_raise` no MCP. O MCP registra no DBA os arquivos atualizados
`general_report/results/report.json` e `report.html` e, após essa confirmação,
solicita o e-mail via Notification. O
relatório de latência permanece identificado como a evidência que originou a
decisão. O cooldown crítico padrão continua em 120 segundos.

## Laboratório integrado

O orquestrador do repositório executa o fluxo completo com feedback no console:

```text
carga read-only do DBA -> coletor HTML -> Luna -> MCP -> inbox do DBA -> Notification
```

A partir da raiz do repositório, confira o plano sem iniciar processos:

```sh
python3 apps/lab-console/scripts/run-health-check-lab.py
```

Para executar a carga read-only e habilitar entrega real durante o processo:

```sh
python3 apps/lab-console/scripts/run-health-check-lab.py --execute
```

O script valida dependências e capacidades, inicia `mysql-health-latency` e
`mysql-health-advisor`, aciona o workload oficial
`agents/dba/load-tests/sakila-read-only/sakila_read_demo_35.py` e deixa o
Luna decidir e publicar pelo MCP. A carga usa somente `sakila`, com 10 queries de
aquecimento, baseline de 10 TPS, carga entre 10 e 15 TPS e medição configurada
para 300 segundos.

O modo `--execute` habilita MCP, inbox do DBA e e-mail real somente no ambiente
dos processos filhos. O orquestrador carrega apenas a configuração não secreta
do Notification; a senha SMTP não é lida nem encaminhada e só pode ser
resolvida pelo próprio Notification no Chaves do macOS. `Ctrl-C` interrompe a
carga e encerra monitor e advisor por grupo de processos.

Em 2026-09-17, o laboratório foi validado em paralelo com o Audit: o alerta
crítico foi aceito pelo MCP, entregue por e-mail e registrado na inbox do DBA;
as ocorrências seguintes foram suprimidas pelo cooldown. O encerramento foi
validado sem processos residuais.

## Testes

Os testes usam conexões simuladas e não acessam o MySQL:

```sh
uv run python -m unittest discover -s tests -v
uv run ruff check .
uv run ruff format --check .
```

## Segurança

- somente os arquivos SQL versionados nos diretórios de cada fluxo podem ser executados;
- o schema de aplicação permitido é exclusivamente `sakila`;
- nenhuma consulta modifica dados ou configuração;
- credenciais, SQL literal de sessões e erros brutos não são persistidos;
- o Health Check não implementa SMTP nem envia e-mail diretamente.

## Estrutura

- `general_report/`: implementação, SQL, regras e resultados do relatório geral;
- `select_latency/`: SQL e resultados do coletor SELECT;
- `advisor/`: regra, prompt, schema e resultados das decisões do Luna;
- `src/`: bibliotecas compartilhadas, coleta de latência e transporte de alertas;
- `policy.json`: escopo e timeout compartilhados das coletas;
- `alerts/`: contrato dos alertas;
- `tests/`: testes automatizados;
- `docs/`: documentação avançada.

## Documentação avançada

- [Onboarding obrigatório para LLMs](docs/LLM_ONBOARDING.md)
- [Guia operacional completo](docs/operations.md)
- [Conexão com o MySQL](CONEXAO.md)

Os comandos antigos com `python -m src...` continuam funcionando, mas os
comandos acima são a interface recomendada para novos usuários.
