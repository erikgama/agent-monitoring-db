# Operação, segurança e continuidade

## Limites de confiança

O runner tem privilégios para chamar os scripts oficiais na máquina do DBA. Trate a chave de runner como credencial operacional privilegiada, distinta de MySQL/SMTP. Há **um runner por Control API** nesta versão. `--actions` limita seu escopo; `--allow-execute` é opt-in. Autenticação é compartilhada por chave de deployment, com HMAC direcional; não há provisionamento multi-tenant de runners.

Use um único worker da API: sessões, nonces, locks e socket são mantidos em memória. PostgreSQL torna metadados persistentes, mas não transforma esta versão num cluster distribuído. Reinício exige recarregar a página e reconciliar jobs; não configure autoscaling horizontal sem externalizar esses estados. Em 17/09/2026, o DBA autorizou retirar login e senha de aprovação do acesso local. Todos os visitantes locais operam como `local-dba`; não há atribuição individual de identidade. O integrado aceita apenas origem loopback e requests HTTP locais, inclusive Host/Forwarded-Host local. Mantenha API e Web em 127.0.0.1, sem proxy público ou túnel.

Tokens de sessão aleatórios são guardados como digest no servidor e cookie HttpOnly/SameSite Strict no navegador. O endpoint POST `/api/access` abre/renova automaticamente a sessão sem senha; isso não autentica uma pessoa. CSRF e Origin continuam obrigatórios para mutações. Rate limit permanece ativo; não há token no localStorage. A chave do runner continua obrigatória e distinta das credenciais do banco. Senha SMTP só é resolvida pelo Notification via Chaves do macOS ou helper corporativo no Linux; credenciais do login-path não são lidas pelo app.

O catálogo e eventos usam restrições complementares: roots/nomes fixos, leitura sem symlink, limite de tamanho, identidade audit_id/timestamp, filtro de conteúdo sensível e CSP. O runtime do runner cria `UV_CACHE_DIR` próprio. Logs brutos dos scripts não são enviados: somente campos reconhecidos e allowlisted. Campos desconhecidos são descartados, não mostrados numa aba de “log bruto”.

## Recuperação

| Situação | Comportamento / ação do DBA |
|---|---|
| Fecha/reabre a UI | Jobs continuam visíveis e controlados pela API; reconectar recupera snapshot. Jobs têm deadlines. |
| “Parar tudo” | API envia stop para cada job; runner encerra descendentes, inclusive novas sessões, com SIGINT → SIGTERM → SIGKILL. |
| Queda curta do WebSocket | Runner preserva os processos e as mensagens durante até 15 segundos enquanto reconecta; nada é reiniciado ou reexecutado. |
| WebSocket indisponível por mais de 15 segundos | Runner encerra com segurança seus processos; API marca os jobs ativos como `interrupted`. |
| Reinício da API | Jobs persistidos ativos viram interrupted; recarregue a página para abrir nova sessão automática e verifique processos antes de nova rodada. |
| Runner encerrado | Shutdown cancela jobs. Watchdog independente tenta recolher descendentes se o runner morrer abruptamente. |
| Monitor falha durante “3 alertas” | Guard aborta a orquestração e encerra os demais processos; não espera completar com confirmação fictícia. |
| DROP/ALTER durante SELECT lenta | As duas ações podem permanecer em execução enquanto o MySQL conclui a tentativa. Cada conexão tem limite de 15 segundos; uma tentativa já conectada tem até 300 segundos e nunca é repetida automaticamente. |
| SMTP falha | Evento de falha quando reportado pelo executor; sem retry implementado pelo app. Gates/recorrência internos continuam responsabilidade dos agentes. |
| HTML substituído/ausente | Mostrar indisponível/não retido. Não copiar latest atual como histórico do alerta. |
| Cache global uv/npm sem permissão | Configurar diretórios de cache próprios e graváveis para `uv`/npm; não corrigir com chmod/chown global nem apagar caches de terceiros. |

O watchdog reduz risco de órfãos, mas nenhuma rotina userspace garante cleanup sob perda do sistema operacional, SIGKILL de todos os processos ou falhas extremas entre fork e descoberta do descendente. Nesses casos, o DBA deve conferir os processos oficiais antes de religar. O app não mata processos que não criou e não adota sessões manuais preexistentes.

O rastreamento dos descendentes roda fora do event loop e é serializado entre
jobs. Isso preserva o heartbeat do runner mesmo com Health Check, carga SELECT,
Refactor, Audit, DROP e ALTER concorrentes. A varredura ocorre a cada segundo;
o watchdog independente continua sendo a proteção adicional caso o processo do
runner termine abruptamente.

Interromper uma execução não desfaz uma mensagem SMTP já aceita. O fluxo de três alertas mantém Health e Audit ativos enquanto dispara SELECTs, DROP e ALTER, encerrando os monitores após as três rotas confirmadas ou em qualquer falha. Ainda existe uma janela assíncrona: não prometa exatamente três e-mails sob qualquer falha. As confirmações MCP/Notification/DBA são independentes e preservadas na timeline.

## Validação real controlada

Em 17/09/2026, com autorização explícita do DBA, o build Web de produção foi iniciado em modo integrado local com runner allowlisted. O cenário Health/SELECTs foi exercitado de ponta a ponta: Health passou por start → ready; a carga oficial apresentou progresso e botão `Cancelar`; cancelamento encerrou a execução; o monitor Health também parou e retirou sua animação. Foram observados `query_latency`, contrato MCP aceito, e-mail enviado e evidência registrada no DBA. Uma segunda execução permaneceu ativa além de 05:00 e foi cancelada com sucesso; isso confirmou que `--measurement-seconds 300` é a janela de medição e não o limite total de parede quando calibração, tentativas e finalização estão habilitadas. Não registrar essa rodada como conclusão natural do script nem como validação de DROP/ALTER.

Para repetir de forma controlada:

1. Validar credenciais/configuração pelo procedimento oficial do DBA, sem copiá-las para o app.
2. Confirmar que `sakila` e a fixture protegida estão corretos; revisar a conta restrita de DROP/ALTER.
3. Subir API integrada e runner **sem** `--allow-execute`; revisar os planos.
4. Obter autorização atual do DBA para carga, tentativas protegidas e três e-mails. Autorizações de sessões antigas não substituem essa decisão.
5. Habilitar execução no runner e abrir a interface local. O atalho validado foi `python3 scripts/dev.py --production --integrated --allow-execute`. Para execução manual, ativar Health e Audit, aguardar `ready` e então disparar SELECTs, DROP e ALTER no bloco Simulação Aplicação. A ação SELECT inicia a carga de latência e a query versionada do Slow Query Log e permanece ativa até cancelamento. Como alternativa, executar o fluxo global com confirmação digitada.
6. Conferir query_latency, destructive_ddl e schema_change, cada um com contrato aceito, SMTP sent e DBA recorded. Abrir o HTML/JSON correspondente, respeitando a ausência de histórico Audit.
7. Conferir encerramento dos processos e quantidade efetiva de e-mails. Guardar apenas evidências sanitizadas.
8. Usar o botão `Cancelar` para encerrar uma simulação sem derrubar necessariamente o monitor; usar `Parar` no card do agente para o monitor. Remover `--allow-execute` ou parar o runner ao terminar.

## Decisões da primeira versão

- A Central de ocorrências usa o provedor LLM configurado apenas para resumir os arquivos
  já preservados pelo DBA. O resumo não investiga causa, não recomenda ações ou
  melhorias e não acessa o banco. Dados sensíveis são mascarados em memória
  antes da leitura pelo modelo ou da exibição no navegador.
- O resumo é iniciado automaticamente somente quando uma nova ocorrência entra
  na inbox com a API integrada ativa. Abrir itens históricos apenas lê arquivos
  e não dispara o modelo.
- Refactor é visualizado como especialista. O botão `Ativar Refactor` inicia o
  worker; pedidos versionados podem chegar do Health Check pelo MCP. Ao concluir,
  `refactor_result_raise` registra o resultado no DBA e pode solicitar ao
  Notification uma única tentativa de aviso sem SQL literal. Nada é enviado
  para produção.
- O filtro de artefatos pode ocultar um relatório legítimo se ele contiver IP, e-mail ou outro indicador sensível. Isso aparece como indisponibilidade, não autorização para relaxar o contrato. A política de publicação deve ser decidida separadamente.
- A regra exibida para Health Check é o `rules.md` lido pelo provedor LLM configurado para decidir o alerta P99; thresholds do relatório geral e cooldown não são expostos nesse botão.
- A UI não solicita senha. Sessões de acesso local são renovadas automaticamente; se houver erro após queda ou expiração, recarregue a página. Propostas continuam expirando em cinco minutos e exigindo confirmação digitada.
- Biblioteca filtra por origem e busca textual de categoria/data; não oferece paginação remota ou seletor avançado de intervalos nesta versão.
- O projeto foi isolado em `apps/lab-console/`. Nenhum arquivo de implementação dos agentes foi substituído.

## Para continuar em outra sessão

Comece pelo [README principal](../README.md), especialmente “Retomar sem o histórico da conversa”, e pelos três documentos de `docs/`, depois consulte o prompt original na raiz. O README inclui mapa do código, configuração, estado entregue e texto pronto para a próxima sessão. Confira os processos da demo antes de iniciar outra instância na porta 3000/8000. Não confunda resultados fictícios com validação MySQL/SMTP. Não rode os scripts reais ao tentar reproduzir a suíte: os testes de integração constroem um repositório temporário com fakes.

As rotas integradas de DROP e ALTER foram validadas em 23/09/2026; consulte `VALIDACAO.md` para a evidência e `../../docs/VALIDATION_VM.md` para a instalação no clone da VM. Docker/Compose + PostgreSQL permanecem sem validação se essa implantação for escolhida. Domínio/provedor e TLS só entram após nova decisão de controle de acesso: a instalação integrada atual sem login não deve ser publicada.
