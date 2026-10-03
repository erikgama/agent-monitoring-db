# Agente DBA

Coordenador operacional dos agentes MySQL. Consolida evidências do Health Check
e do Audit, recebe pelo MCP os resultados automáticos do Refactor e mantém
planos, políticas e relatórios. O escopo de banco do DBA é exclusivamente
`sakila`.

## Estrutura

- `health-check-alerts/`: inbox privada para alertas de saúde validados pelo MCP;
- `audit-security-alerts/`: inbox privada e sanitizada para eventos de segurança;
- `analise-ocorrencia-health-check/`: prompt e resumos factuais dos arquivos
  recebidos do Health Check;
- `analise-ocorrencia-audit/`: prompt e resumos factuais dos arquivos
  sanitizados recebidos do Audit;
- `load-tests/`: workloads isolados, documentação e relatórios próprios;
- `playbooks/` e `policies/`: procedimentos e controles operacionais;
- `requests/`: registros históricos anteriores ao fluxo automático;
- `reports/`: diagnósticos e resultados consolidados;
- `scripts/`: utilitários operacionais;
- `tests/`: testes dos contratos e workloads mantidos pelo DBA.

## Segurança operacional

- um alerta recebido é evidência, não autorização para alterar o banco;
- mudanças exigem aprovação explícita, validação e plano de reversão;
- `sakila_dev` pertence exclusivamente ao Refactor;
- credenciais e dados sensíveis não devem ser persistidos nos artefatos.

As regras completas estão em [AGENTS.md](AGENTS.md).

## Resumos das ocorrências

A Central de ocorrências do Lab Console lista diretamente as duas inboxes. Com
o serviço integrado ativo, a API detecta cada nova ocorrência, entrega ao
LLM de análise configurado uma cópia mascarada somente dos JSONs
essenciais e persiste `results/<record-id>/summary.md` na pasta de análise
correspondente. Abrir uma ocorrência nunca aciona o LLM: a tela apenas lê o
resumo que já estiver salvo. Ocorrências históricas sem resumo permanecem
disponíveis com suas evidências originais.

O LLM atua apenas como ajudante de leitura: organiza os fatos registrados e
indica informações ausentes. Ele não procura causa raiz, não recomenda
melhorias, não decide ações, não chama MCP e não acessa o MySQL. Os arquivos
originais das inboxes permanecem imutáveis.

O chat contextual usa `chat/prompt.md`. A cada pergunta, o provedor e modelo
configurados recebem somente a ocorrência selecionada:
metadados, JSONs autorizados, resumo já persistido e até as 12 mensagens mais
recentes da conversa. Ele atua como DBA sênior de MySQL para explicar essas
evidências, mantém separados os escopos de schema e instância e faz perguntas
curtas quando faltam contexto ou objetivo. O chat não consulta o banco, não
executa SQL e não recomenda mudanças. O resumo automático continua separado no
LLM de análise configurado.

## Laboratórios controlados

O DBA mantém os workloads que produzem atividade de laboratório; Health Check
e Audit continuam observadores e não criam carga por conta própria.

### Health Check

O cenário `load-tests/sakila-read-only/` produz somente SELECTs em `sakila`.
O orquestrador oficial da raiz valida capacidades, inicia Health Check, MCP e
Notification e executa esse workload com feedback no console:

```sh
python3 apps/lab-console/scripts/run-health-check-lab.py
python3 apps/lab-console/scripts/run-health-check-lab.py --execute
```

O primeiro comando é conferência. `--execute` inicia a carga e habilita entrega
real de alertas no ambiente dos processos filhos.

### Audit Security

O cenário `load-tests/sakila-audit-security/` usa exclusivamente a conta
restrita `sakila_audit_demo`. O preflight impede a execução se houver role
ativa ou privilégios perigosos. Para validar cada categoria separadamente:

```sh
# terminal 1
python3 apps/lab-console/scripts/run-audit-lab.py --execute

# terminal 2, depois que o monitor estiver pronto
python3 apps/lab-console/scripts/run-audit-drop-lab.py --execute
python3 apps/lab-console/scripts/run-audit-alter-lab.py --execute
```

O resultado correto dos dois workloads é `expected_permission_denied`; nenhum
DROP ou ALTER deve ser aplicado. Cada wrapper executa uma única tentativa e
grava relatório JSON e Markdown em
`load-tests/sakila-audit-security/reports/`. Encerre o orquestrador com
`Ctrl-C`.

Em 2026-09-17, os três cenários foram validados simultaneamente: um alerta de
latência do Health Check e os alertas Audit `destructive_ddl` e
`schema_change`. Os três foram aceitos pelo MCP, enviados pelo Notification e
registrados nas respectivas inboxes do DBA.

## Validação local

Os testes não executam workloads nem acessam o MySQL:

```sh
python3 -m unittest discover -s tests -p 'test_*.py' -v

cd audit-security-alerts
.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
```

Para verificar o estilo de todo o código Python do agente:

```sh
audit-security-alerts/.venv/bin/ruff check .
audit-security-alerts/.venv/bin/ruff format --check .
```
