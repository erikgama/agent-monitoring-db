"""MCP stdio publisher for validated query-refactor requests."""

from __future__ import annotations

import asyncio
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any


def repository_root(start: Path | None = None) -> Path:
    origin = (start or Path(__file__)).resolve()
    for candidate in (origin, *origin.parents):
        if (candidate / "mcp" / "pyproject.toml").is_file():
            return candidate
    raise RuntimeError("repository_root_not_found")


class McpRefactorRequestPublisher:
    def __init__(
        self,
        *,
        root: Path | None = None,
        server_environment: Mapping[str, str] | None = None,
    ) -> None:
        self.root = (root or repository_root()).resolve()
        self.server_environment = server_environment

    def _parameters(self) -> Any:
        from mcp import StdioServerParameters
        from mcp.client.stdio import get_default_environment

        environment = get_default_environment()
        environment.setdefault(
            "UV_CACHE_DIR", str(Path(tempfile.gettempdir()) / "mysqlconf-uv-cache")
        )
        for key in ("MCP_REFACTOR_REQUESTS_DIR", "UV_CACHE_DIR"):
            if key in os.environ:
                environment[key] = os.environ[key]
        if self.server_environment:
            environment.update(
                {
                    key: value
                    for key, value in self.server_environment.items()
                    if key in {"MCP_REFACTOR_REQUESTS_DIR", "UV_CACHE_DIR"}
                }
            )
        return StdioServerParameters(
            command="uv",
            args=["run", "--directory", str(self.root / "mcp"), "mysqlconf-mcp"],
            env=environment,
        )

    async def _publish_async(self, request: dict[str, Any]) -> dict[str, Any]:
        from mcp import ClientSession
        from mcp.client.stdio import stdio_client

        async with (
            stdio_client(self._parameters()) as (read_stream, write_stream),
            ClientSession(read_stream, write_stream) as session,
        ):
            await session.initialize()
            tools = {tool.name for tool in (await session.list_tools()).tools}
            if "refactor_request_raise" not in tools:
                return {"accepted": False, "status": "tool_not_available"}
            called = await session.call_tool(
                "refactor_request_raise", arguments={"request": request}
            )
            if called.isError or not isinstance(called.structuredContent, dict):
                return {"accepted": False, "status": "tool_error"}
            return called.structuredContent

    def publish(self, request: dict[str, Any]) -> dict[str, Any]:
        try:
            return asyncio.run(self._publish_async(request))
        except Exception:
            return {"accepted": False, "status": "transport_error"}
