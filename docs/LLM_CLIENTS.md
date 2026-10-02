# Codex, Claude Code e Kimi Code

Os três clientes podem trabalhar sobre o clone e também executar os advisors.
O repositório fornece instruções e launchers MCP; cada usuário instala e
autentica seu próprio cliente. Nenhuma chave de API, login ou configuração
global de outra máquina faz parte do Git.

## Instruções do projeto

`AGENTS.md` é a fonte canônica. `MEMORY.md` fornece contexto histórico;
evidência atual prevalece. Antes de agir em um papel, leia também o
`agents/<papel>/AGENTS.md`. Health Check e Audit exigem seus
`docs/LLM_ONBOARDING.md`. O DBA continua sendo o único coordenador operacional.

Codex descobre a cadeia de `AGENTS.md` da raiz até o diretório de trabalho.
Claude Code recebe os mesmos documentos por `CLAUDE.md` e pelos bridges de cada
papel. Kimi Code aceita as instruções `AGENTS.md`; o cliente deve seguir a mesma
leitura por papel. Abrir um cliente na raiz não transforma as cinco pastas em
cinco processos: os coletores/advisors são executados pelos entrypoints Python
ou pelo runner do console.

## MCP

Execute na raiz:

```sh
uv run --locked agent-monitoring configure-clients
```

O gerador produz `.codex/config.toml`, `.mcp.json` e `.kimi-code/mcp.json`,
ignorados pelo Git, com `uv run --locked --directory <este-clone>/mcp
agent-monitoring-mcp`. O processo é stdio e usa o mesmo MCP para todos os
clientes. Arquivos existentes são preservados; mescle a entrada manualmente
se já houver configuração local. Depois de mover o clone, regenere ou atualize
os caminhos locais. Nenhuma entrada contém credenciais de banco.

Codex: confira `codex mcp list` e `/mcp`. Claude: `claude mcp list` e `/mcp`.
Kimi Code: abra `/mcp` na sessão. Reinicie o cliente após gerar os arquivos e
confirme a confiança do projeto/MCP quando solicitada. Configuração de um MCP
nunca é autorização para DDL, DML, e-mail ou infraestrutura.

## Advisors e chat DBA

`agent_monitoring/llm.py` aplica as escolhas de `config/agent-monitoring.toml`
a Health Check, Audit, triagem Slow Query Log, Refactor e chat/resumos DBA.
O Notification continua determinístico e apenas entrega contratos validados.

| Provedor | Execução | Saída |
| --- | --- | --- |
| Codex | `codex exec`, sandbox read-only, shell desabilitado, sessão efêmera | JSON por schema ou texto final |
| Claude | `claude --print`, ferramentas desabilitadas e MCP vazio | `structured_output` ou `result` |
| Kimi Code | `kimi --prompt`, perfil `tools: []` e `subagents: []` | última mensagem Assistant no stream JSON |

Cada chamada ocorre num diretório temporário, com timeout, sem variáveis
MySQL/SMTP no ambiente do LLM. As respostas JSON passam pelo validador local e
pelas regras do papel antes de qualquer publicação. A autenticação permanece
no cliente. Sessões/metadados que o próprio cliente criar são locais; não são
publicados. A saída de erro do transporte não é registrada em relatórios.

Verificação real do provedor selecionado:

```sh
uv run --locked agent-monitoring llm-check
```

Esperado: `status=ok`. Isso confirma a combinação cliente/login/modelo nessa
máquina, usando um prompt de teste sem dados do banco. Não confirma qualidade
de refatoração nem equivalência SQL; essas continuam dependentes das regras e
da validação read-only em laboratório.

Referências oficiais de configuração:
[Codex MCP](https://developers.openai.com/codex/mcp/),
[Codex AGENTS.md](https://developers.openai.com/codex/guides/agents-md/),
[Claude MCP](https://code.claude.com/docs/en/mcp),
[Claude modo programático](https://code.claude.com/docs/en/headless),
[Kimi MCP](https://moonshotai.github.io/kimi-code/en/customization/mcp.html),
[Kimi perfis](https://moonshotai.github.io/kimi-code/en/customization/agents.html).
