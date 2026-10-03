# Actions, eventos e artefatos · v1

## Ações permitidas

| Action ID | Executor | Efeito com `execute=true` | Prazo |
|---|---|---|---|
| `health.lab` | `apps/lab-console/scripts/run-health-check-lab.py --monitor-only` | Mantém monitor e advisor ativos; não inicia carga automaticamente | 3600s |
| `health.load` | `apps/lab-console/scripts/run-health-select-simulation.py` | Executa 60 s de aquecimento read-only a 1 TPS e um worker; depois inicia em paralelo a carga de latência e a SELECT versionada que alimenta o Slow Query Log. Exige Health pronto e permanece ativa até cancelamento | Até cancelamento |
| `health.collect` | `.venv/bin/mysql-health-check collect` | Coleta read-only; publicação desabilitada neste adapter | 120s |
| `audit.lab` | `apps/lab-console/scripts/run-audit-lab.py` | Monitor e advisor oficiais | 3600s |
| `audit.drop` | `apps/lab-console/scripts/run-audit-drop-lab.py` | Uma tentativa protegida; exige monitor pronto | 60s |
| `audit.alter` | `apps/lab-console/scripts/run-audit-alter-lab.py` | Uma tentativa protegida; exige monitor pronto | 60s |
| `refactor.lab` | `apps/lab-console/scripts/run-refactor-lab.py` | Mantém o worker ativo; resultado validado vai ao DBA e pode gerar um aviso por e-mail sem SQL literal | 3600s |
| `lab.three` | Orquestra monitores + três simulações | Health e Audit ativos; SELECTs/DROP/ALTER após ready; aguarda três rotas completas | janelas de 90s para ready e 600s para confirmações |
| `notification.test` | Adapter → CLI manual no dry-run / runtime público Notification no envio | Um teste real com `--send` + delivery habilitado; sem retry | 30s |
| `dba.actor_count` | cliente MySQL local | COUNT de `sakila.actor`, somente após proposta e aprovação | 5s SQL / 15s processo |
| `dba.film_count` | cliente MySQL local | COUNT de `sakila.film`, somente após proposta e aprovação | 5s SQL / 15s processo |

Dry-run omite `--execute` dos scripts oficiais. `health.load` não tem dry-run: a UI só o libera após Health pronto e sempre exige `sakila`. `health.collect` dry-run lê `read-latest`; queries do DBA não têm execução dry-run: a própria proposta é o plano. Notification dry-run utiliza remetente/destinatários fictícios e jamais habilita SMTP. O Refactor carrega apenas a configuracao nao secreta e deixa a senha sob responsabilidade do runtime Notification. O runner nunca recebe argumentos livres. `StartJob` rejeita campos adicionais pelo schema Pydantic.

Resources impedem dois monitores Health, duas simulações SELECT, dois monitores Audit ou laboratório global concorrente. A simulação Health pode rodar junto do monitor somente depois de `ready`. DROP e ALTER são recursos distintos e podem rodar em paralelo, com Audit pronto. Nos três cenários manuais, a UI mantém o job visível enquanto o subprocesso oficial estiver vivo; a simulação SELECT exibe o tempo decorrido e continua executando a query conhecida do Slow Query Log até o operador clicar em `Cancelar`. O cancelamento solicita ao runner o encerramento de toda a árvore de processos. `request_id` é idempotente por usuário: repetir action/execute devolve o mesmo job; reutilizar a chave com outra ação falha.

Acesso atual: operador local único `local-dba` com papel `dba_approver`, sem login ou senha nas aprovações, por autorização do DBA. Execução exige confirmação `sakila`; propostas expiram em 5 minutos e são consumidas uma vez. Sessões de 30 minutos são renovadas automaticamente por POST `/api/access`, com cookie HttpOnly e CSRF. O integrado rejeita origem/HTTP não locais. As rotas antigas de login/RBAC permanecem compatíveis para consumidores explícitos, mas não são usadas pela UI e não constituem proteção multiusuário da instalação sem login.

## API

OpenAPI em `/openapi.json` (API versão `0.1.0`), documentação em `/docs`. Endpoints operacionais sob `/api`: access/session, state, events (SSE), jobs/start/stop, emergency-stop, chat, approvals e artifacts. Login/logout/reauth legados permanecem compatíveis, mas não são necessários para a UI. `GET /api/health` só informa saúde e modo do serviço, não estado MySQL.

A Central de ocorrências usa endpoints sob `/api/dba/incidents`. A listagem lê
somente as inboxes Health Check e Audit no modo integrado. Uma tarefa interna da
API resume automaticamente apenas novos registros e persiste `summary.md`; o
detalhe nunca chama o modelo e apenas devolve o resumo já existente.
`evidence/{filename}` aceita apenas os nomes fixos de cada contrato e mascara
indicadores sensíveis sem alterar a origem. A rota `question` usa o conjunto
fechado de arquivos e não aceita paths, SQL ou instruções de ação.

O frontend usa tipos TypeScript explícitos em `web/src/lib/api.ts`; entradas da API são validadas por Pydantic (`extra=forbid`). São contratos mantidos conjuntamente e testados, não SDK gerado. A API de runner fica em `/runner`, autenticada separadamente, sem cookies de navegador.

## Eventos

Envelope `lab_event.v1`:

```json
{"version":"lab_event.v1","event_id":"uuid","correlation_id":"flow-id","job_id":"job-id","occurred_at":"UTC ISO8601","source":"audit","type":"alert.detected","severity":"critical","payload":{"category":"schema_change","mode":"demo"}}
```

Famílias emitidas conforme evidência observada:

- `job.validating`, `job.starting`, `job.ready`, `job.running`, `job.stopping`, `job.succeeded`, `job.failed`, `job.cancelled`, `job.interrupted`, `job.timed_out`;
- `lab.started`, `lab.progress`, `agent.starting`, `agent.ready`;
- `audit.attempt.denied`, `audit.attempt.unsafe_success`;
- `alert.detected`, `alert.suppressed` (`duplicate`, `historical`, `cooldown_active`);
- `mcp.validated`, `mcp.rejected`;
- `notification.sent`, `notification.failed`, `notification.skipped`, `notification.dry_run`;
- `dba.recorded`, `dba.failed`, `query.completed`;
- `artifact.available` (demo), `artifact.viewed`, `approval.requested`, `approval.approved`.

Não inventamos etapas: eventos de agente vêm das mensagens estruturadas reconhecidas dos executores. Conectividade do runner é observada no snapshot/heartbeat; falha no socket gera `job.interrupted`. Não há simulação de “MCP online” no modo integrado nem dedução de entrega a partir do início de um processo.

Estado de job: queued → validating → starting → ready/running → stopping/terminal. Terminal inclui succeeded, failed, cancelled, interrupted, timed_out. Timeout e unsafe_success encerram a árvore. Resultado parcial mantém as evidências confirmadas sem transformar falha numa entrega bem-sucedida.

SSE invalida o cache TanStack; polling de 3 segundos recompõe o snapshot após desconexão do navegador. Snapshot limitado aos 300 eventos/jobs mais recentes; não é um sistema de busca analítica de histórico ilimitado. Heartbeat do runner a cada 5s; desconexão após 20s sem mensagem. Envelope HMAC direcional expira em 30s e usa nonce antirreplay. Rejeições nunca executam SQL alternativo.

## Artefatos

IDs opacos, hash SHA-256 do JSON, identidade de coleta e tamanho. O navegador não fornece paths. Raízes explícitas em `api/labconsole/artifacts.py`: relatório geral Health, resultados de latência, relatório Audit, resultados `result.json` do Refactor e inboxes DBA. O catálogo Refactor exige a versão `query_refactor_result.v1` e IDs obrigatórios; a validação integral do contrato cabe ao MCP. O `result.json` pode conter SQL literal e fica disponível apenas no integrado local; a demo pública bloqueia arquivos do clone. Arquivos `.sql` e Markdown arbitrário não entram na biblioteca.

Somente nomes de arquivo permitidos; até 200 candidatos por raiz; 2 MB por arquivo; RPC com deadline de 15s. Todos os componentes do caminho são abertos com `O_NOFOLLOW`; arquivos especiais, links simbólicos e travessia de diretório são rejeitados. JSON precisa ser válido e trazer a identidade exigida por sua origem (`audit_id` ou IDs do Refactor). HTML deve conter o mesmo `audit_id` e timestamp do JSON pareado. Latest substituído causa erro; não ocorre fallback silencioso.

O Health histórico pode ter HTML. A inbox Audit só tem resumos JSON: a interface mostra “HTML histórico não retido”. A Central de ocorrências abre somente os arquivos do `record_id` selecionado; sem correspondência, exibe ausência explícita.

HTML é servido com MIME fixo, CSP sandbox e `script-src 'none'`. A UI usa iframe sandbox sem permissões e CSP adicional para bloquear recursos remotos. Nunca usa `innerHTML` no DOM da aplicação. Download/impressão usam a sessão local automática no mesmo endpoint; não há autenticação individual da pessoa. Na biblioteca geral, conteúdo com indicadores sensíveis continua bloqueado integralmente. Na Central de ocorrências, a API mascara esses indicadores apenas na cópia entregue ao modelo e ao navegador; a evidência original da inbox não é modificada.

## Persistência

`api/migrations/001_initial.sql` descreve o schema aditivo inicial; startup aplica o equivalente SQLAlchemy e registra versão 1. Versões desconhecidas são recusadas. A tabela guarda jobs, eventos, propostas e número do schema. Senhas da aplicação permanecem como hashes no ambiente de configuração, não nessa tabela; senhas MySQL/SMTP nunca entram no plano de controle.

`LAB_RETENTION_DAYS` (14 por padrão) elimina eventos e propostas antigos ao iniciar. Jobs preservam metadados operacionais; HTML real permanece somente na origem. Antes de ampliar retenção ou adicionar migrações destrutivas, faça backup e aprove a mudança separadamente. Não há migração de diretórios dos agentes.
