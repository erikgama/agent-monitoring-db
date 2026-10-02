"""Local stdio entry point for MySQL Conf MCP Central."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from mysqlconf_mcp.tools.incidents import incident_raise
from mysqlconf_mcp.tools.refactor_workflow import (
    refactor_request_raise,
    refactor_result_raise,
)

mcp = FastMCP(
    name="MySQL Conf MCP Central",
    instructions=(
        "Valida alertas e contratos do fluxo de refatoração. Encaminha alertas "
        "ao Notification e ao DBA, pedidos de query lenta ao Refactor e "
        "resultados do Refactor ao DBA. Não executa ações no banco."
    ),
    json_response=True,
    log_level="WARNING",
)

mcp.tool(
    name="incident_raise",
    description=(
        "Recebe um alerta estruturado do Health Check ou Audit Security. Use "
        "somente quando uma coleta identificar condição relevante. O alerta deve "
        "seguir health_check_alert.v1 ou audit_security_alert.v1 e incluir "
        "os relatórios JSON e "
        "HTML da mesma coleta. Quando o encaminhamento está habilitado, o agente "
        "MCP primeiro registra a evidência técnica no DBA e somente depois permite "
        "que o agente notification decida a entrega por e-mail. Esta ferramenta não "
        "implementa SMTP nem executa ações no banco."
    ),
    structured_output=True,
)(incident_raise)

mcp.tool(
    name="refactor_request_raise",
    description=(
        "Valida query_refactor_request.v1 e registra uma única solicitação "
        "deduplicada na inbox do Refactor. Não executa SQL."
    ),
    structured_output=True,
)(refactor_request_raise)

mcp.tool(
    name="refactor_result_raise",
    description=(
        "Valida query_refactor_result.v1, registra o resultado na inbox do DBA e, "
        "quando habilitado, solicita ao Notification um aviso de conclusão sem "
        "SQL literal. Não envia a proposta para produção."
    ),
    structured_output=True,
)(refactor_result_raise)


def main() -> None:
    """Run the development-only local MCP transport."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
