"""Environment configuration owned by the MCP process."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass


class McpConfigurationError(ValueError):
    """Raised with a stable code and without configuration values."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _boolean(value: str | None, *, default: bool, code: str) -> bool:
    if value is None or not value.strip():
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise McpConfigurationError(code)


def notification_enabled(environ: Mapping[str, str] | None = None) -> bool:
    source = os.environ if environ is None else environ
    return _boolean(
        source.get("MCP_NOTIFICATION_ENABLED"),
        default=False,
        code="mcp_notification_enabled_invalid",
    )


def dba_enabled(environ: Mapping[str, str] | None = None) -> bool:
    source = os.environ if environ is None else environ
    return _boolean(
        source.get("MCP_DBA_ENABLED"),
        default=False,
        code="mcp_dba_enabled_invalid",
    )


@dataclass(frozen=True, slots=True)
class McpSettings:
    """MCP-owned integration switches; consumers own their local behavior."""

    notification_enabled: bool = False
    dba_enabled: bool = False

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> McpSettings:
        return cls(
            notification_enabled=notification_enabled(environ),
            dba_enabled=dba_enabled(environ),
        )
