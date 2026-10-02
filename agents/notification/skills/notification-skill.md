# Notification skill

Revalida eventos já aceitos pelo MCP Central e usa o Advisor para rotear a
severidade recebida ao canal permitido pela política local. Hoje, somente o
canal e-mail está implementado. Não consulta MySQL, não recalcula severidade e
não decide alertas.

Consulte `../notification/advisor/rules.md` para a role em português e
`../AGENTS.md` para os controles de SMTP e confidencialidade.
