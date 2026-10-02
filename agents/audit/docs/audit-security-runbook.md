# Runbook do Audit Security

1. `mysql-audit-security monitor` coleta fatos mascarados em modo read-only.
2. JSON e HTML pareados são gravados em `audit_security/results/`.
3. `mysql-audit-advisor` detecta o novo `audit_id`.
4. Luna lê o HTML e as regras naturais de `audit_security/advisor/rules.md`.
5. O código confirma que cada evidência escolhida existe no relatório.
6. O agente cria `audit_security_alert.v1` e chama `incident_raise` no MCP.
7. O MCP revalida, registra primeiro no DBA e só então chama o Notification.

Falhas de coleta produzem estado de coleta incompleta, nunca um alerta
inventado. Falha de análise ou evidência divergente impede publicação. Falha de
persistência no DBA impede o e-mail e não é registrada como entrega confirmada.

Nenhuma etapa altera filtro Audit, usuários, privilégios, banco, configuração
ou infraestrutura. Notification não recebe segredo, SQL literal ou identidade
em claro e decide apenas a entrega, nunca a existência do incidente.
