# Health Check skill

Interpreta sinais de saúde e desempenho do MySQL HeatWave em modo somente leitura.
O escopo funcional por schema é `sakila`; a coleta oficial usa `src/` e os blocos
allowlisted em `sql/`.

Consulte `../AGENTS.md` para os limites operacionais. O coletor produz
evidências em HTML sem decidir alertas. Use `../advisor/` para a regra, a
decisão do Luna e a publicação ao MCP.
