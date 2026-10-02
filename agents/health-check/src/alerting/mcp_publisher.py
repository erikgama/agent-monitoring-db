"""Real stdio client for the central MCP incident_raise tool."""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..alert_contract import validate_alert
from .publisher import AlertPublisher, PublicationResult

_MCP_ENVIRONMENT_KEYS = frozenset(
    {
        "MCP_DBA_ALERTS_DIR",
        "MCP_DBA_ENABLED",
        "MCP_NOTIFICATION_ENABLED",
        "NOTIFICATION_DELIVERY_ENABLED",
        "NOTIFICATION_EMAIL_FROM",
        "NOTIFICATION_EMAIL_RECIPIENTS_WARNING",
        "NOTIFICATION_EMAIL_RECIPIENTS_CRITICAL",
        "SMTP_HOST",
        "SMTP_PORT",
        "SMTP_USERNAME",
        "SMTP_PASSWORD",
        "SMTP_USE_STARTTLS",
        "UV_CACHE_DIR",
    }
)


def resolve_repository_root(start: Path | None = None) -> Path:
    origin = (start or Path(__file__)).resolve()
    candidates = [origin, *origin.parents]
    for candidate in candidates:
        mcp_directory = (candidate / "mcp").resolve()
        health_directory = (candidate / "agents" / "health-check").resolve()
        if (
            mcp_directory.parent == candidate
            and (mcp_directory / "pyproject.toml").is_file()
            and health_directory.is_dir()
        ):
            return candidate
    raise RuntimeError("repository_root_not_found")


class McpIncidentPublisher(AlertPublisher):
    def __init__(
        self,
        *,
        repository_root: Path | None = None,
        server_environment: Mapping[str, str] | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.repository_root = (
            repository_root.resolve()
            if repository_root is not None
            else resolve_repository_root()
        )
        self.server_environment = server_environment
        self.logger = logger or logging.getLogger("heatwave_healthcheck.alerting")

    def _parameters(self) -> Any:
        from mcp import StdioServerParameters
        from mcp.client.stdio import get_default_environment

        mcp_directory = (self.repository_root / "mcp").resolve()
        if (
            mcp_directory.parent != self.repository_root
            or not (mcp_directory / "pyproject.toml").is_file()
        ):
            raise RuntimeError("invalid_mcp_directory")
        # The MCP SDK intentionally inherits only a small safe environment by
        # default. Forward only explicit routing variables and the optional
        # uv cache location needed by the child MCP process; unrelated
        # variables, including database credentials, remain excluded.
        environment = get_default_environment()
        environment.setdefault(
            "UV_CACHE_DIR", str(Path(tempfile.gettempdir()) / "mysqlconf-uv-cache")
        )
        for key in _MCP_ENVIRONMENT_KEYS:
            if key in os.environ:
                environment[key] = os.environ[key]
        if self.server_environment is not None:
            for key, value in self.server_environment.items():
                if key in _MCP_ENVIRONMENT_KEYS:
                    environment[key] = value
        return StdioServerParameters(
            command="uv",
            args=[
                "run",
                "--directory",
                str(mcp_directory),
                "mysqlconf-mcp",
            ],
            env=environment,
        )

    async def _publish_async(self, alert: dict[str, Any]) -> PublicationResult:
        from mcp import ClientSession
        from mcp.client.stdio import stdio_client

        parameters = self._parameters()
        async with (
            stdio_client(parameters) as (read_stream, write_stream),
            ClientSession(read_stream, write_stream) as session,
        ):
            await session.initialize()
            listed = await session.list_tools()
            if "incident_raise" not in {tool.name for tool in listed.tools}:
                return PublicationResult(
                    published_to_mcp=False,
                    accepted=None,
                    mcp_status="tool_not_available",
                    error_code="incident_raise_not_available",
                )
            called = await session.call_tool(
                "incident_raise",
                arguments={"alert": alert},
            )
            if called.isError or not isinstance(called.structuredContent, dict):
                return PublicationResult(
                    published_to_mcp=False,
                    accepted=None,
                    mcp_status="tool_error",
                    error_code="incident_raise_tool_error",
                )
            response = called.structuredContent
            accepted = response.get("accepted") is True
            delivery = response.get("delivery")
            if not isinstance(delivery, dict):
                delivery = None
            dba = response.get("dba")
            if not isinstance(dba, dict):
                dba = None
            return PublicationResult(
                published_to_mcp=accepted,
                accepted=accepted,
                mcp_status=str(response.get("status") or "unknown"),
                delivery_status=(
                    str(delivery.get("status")) if delivery is not None else None
                ),
                delivery=delivery,
                dba_status=str(dba.get("status")) if dba is not None else None,
                dba=dba,
                error_code=(
                    "mcp_rejected_alert" if response.get("accepted") is False else None
                ),
            )

    def publish(self, alert: dict[str, Any]) -> PublicationResult:
        validate_alert(alert)
        self.logger.info(
            "mcp_publish_started alert_id=%s audit_id=%s severity=%s category=%s",
            alert["alert_id"],
            alert["audit_id"],
            alert["severity"],
            alert["category"],
        )
        try:
            result = asyncio.run(self._publish_async(alert))
        except Exception as error:
            self.logger.warning(
                "mcp_publish_failed alert_id=%s audit_id=%s severity=%s "
                "category=%s error_code=%s",
                alert["alert_id"],
                alert["audit_id"],
                alert["severity"],
                alert["category"],
                type(error).__name__,
            )
            return PublicationResult(
                published_to_mcp=False,
                accepted=None,
                mcp_status="transport_error",
                error_code="mcp_transport_error",
            )
        self.logger.info(
            "mcp_publish_finished alert_id=%s audit_id=%s severity=%s "
            "category=%s published_to_mcp=%s delivery_status=%s dba_status=%s",
            alert["alert_id"],
            alert["audit_id"],
            alert["severity"],
            alert["category"],
            result.published_to_mcp,
            result.delivery_status or "none",
            result.dba_status or "none",
        )
        return result
