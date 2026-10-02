"""Validate, deduplicate and publish decisions made by the Audit agent."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import uuid
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .alert_contract import validate_alert

_MCP_ENVIRONMENT_KEYS = {
    "MCP_DBA_AUDIT_ALERTS_DIR",
    "MCP_DBA_ALERTS_DIR",
    "MCP_DBA_ENABLED",
    "MCP_NOTIFICATION_ENABLED",
    "NOTIFICATION_DELIVERY_ENABLED",
    "NOTIFICATION_EMAIL_FROM",
    "NOTIFICATION_EMAIL_RECIPIENTS_WARNING",
    "NOTIFICATION_EMAIL_RECIPIENTS_CRITICAL",
    "NOTIFICATION_SMTP_KEYCHAIN_SERVICE",
    "SMTP_HOST",
    "SMTP_PORT",
    "SMTP_USERNAME",
    "SMTP_USE_STARTTLS",
    "UV_CACHE_DIR",
}
_EVIDENCE_FIELDS = (
    "occurred_at_utc",
    "event_id",
    "event_key",
    "sql_command",
    "outcome",
    "status_code",
    "schema_scope",
    "scope_evidence",
    "sql_length",
)


def _iso(value: datetime) -> str:
    return (
        value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    )


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp_must_include_timezone")
    return parsed.astimezone(UTC)


def _report_event(report: dict[str, Any], event_key: str) -> dict[str, Any]:
    rows = report.get("domains", {}).get("audit_ddl", {}).get("rows", [])
    matches = [row for row in rows if row.get("event_key") == event_key]
    if len(matches) != 1:
        raise ValueError("agent_event_not_found_in_report")
    return matches[0]


def build_agent_alert(
    report: dict[str, Any],
    html_document: str,
    decision: dict[str, Any],
    environment: str,
    *,
    agent_started_at: datetime,
    detected_at: datetime | None = None,
) -> dict[str, Any]:
    """Translate one agent decision without re-evaluating its natural-language rule."""
    if report.get("scope", {}).get("functional_schemas") != ["sakila"]:
        raise ValueError("report_schema_out_of_scope")
    if report.get("audit_id") not in html_document:
        raise ValueError("report_html_audit_id_mismatch")
    if report.get("collected_at") not in html_document:
        raise ValueError("report_html_collected_at_mismatch")

    event_key = decision["event_key"]
    row = _report_event(report, event_key)
    expected_evidence = {name: row.get(name) for name in _EVIDENCE_FIELDS}
    if decision["evidence"] != expected_evidence:
        raise ValueError("agent_evidence_does_not_match_report")
    occurred_at = _parse_utc(str(row["occurred_at_utc"]))
    if occurred_at < agent_started_at.astimezone(UTC):
        raise ValueError("agent_selected_historical_event")

    collection_time = _parse_utc(report["collected_at"])
    detection = max((detected_at or datetime.now(UTC)).astimezone(UTC), collection_time)
    category = decision["category"]
    alert = {
        "contract_version": "audit_security_alert.v1",
        "alert_id": str(uuid.uuid4()),
        "audit_id": report["audit_id"],
        "detected_at": _iso(detection),
        "environment": environment,
        "source": "audit-security",
        "severity": decision["severity"],
        "category": category,
        "title": decision["title"],
        "summary": decision["summary"],
        "findings": [
            {
                "check_id": decision["rule_id"],
                "metric": decision["metric"],
                "observed_value": True,
                "threshold": False,
                "unit": "event",
                "evidence": decision["evidence"],
                "affected_objects": ["schema:sakila"],
            }
        ],
        "dedupe_key": f"audit-security:sakila:{category}:{event_key}",
        "report": {
            "json": report,
            "html": html_document,
            "report_generated_at": report["collected_at"],
            "report_format_version": report["schema_version"],
        },
        "metadata": {
            "decision_owner": "audit-luna",
            "model": "gpt-5.6-luna",
            "rule_source": "agents/audit/audit_security/advisor/rules.md",
            "event_id": row.get("event_id"),
            "event_key": event_key,
        },
    }
    validate_alert(alert)
    return alert


@dataclass(frozen=True, slots=True)
class PublicationResult:
    published_to_mcp: bool
    accepted: bool | None
    mcp_status: str
    delivery_status: str | None = None
    dba_status: str | None = None
    error_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value is not None}

    @property
    def fully_routed(self) -> bool:
        return (
            self.accepted is True
            and self.delivery_status in {"sent", "delivered", "dry_run"}
            and self.dba_status in {"recorded", "duplicate"}
        )


def resolve_repository_root(start: Path | None = None) -> Path:
    origin = (start or Path(__file__)).resolve()
    for candidate in (origin, *origin.parents):
        if (candidate / "mcp" / "pyproject.toml").is_file() and (
            candidate / "agents" / "audit"
        ).is_dir():
            return candidate
    raise RuntimeError("repository_root_not_found")


class McpIncidentPublisher:
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
        self.logger = logger or logging.getLogger("audit_security.alerting")

    def _parameters(self) -> Any:
        from mcp import StdioServerParameters
        from mcp.client.stdio import get_default_environment

        mcp_directory = (self.repository_root / "mcp").resolve()
        environment = get_default_environment()
        environment.pop("SMTP_PASSWORD", None)
        environment.setdefault(
            "UV_CACHE_DIR", str(Path(tempfile.gettempdir()) / "mysqlconf-uv-cache")
        )
        for key in _MCP_ENVIRONMENT_KEYS:
            if key in os.environ:
                environment[key] = os.environ[key]
        if self.server_environment:
            for key, value in self.server_environment.items():
                if key in _MCP_ENVIRONMENT_KEYS:
                    environment[key] = value
        return StdioServerParameters(
            command="uv",
            args=["run", "--directory", str(mcp_directory), "mysqlconf-mcp"],
            env=environment,
        )

    async def _publish_async(self, alert: dict[str, Any]) -> PublicationResult:
        from mcp import ClientSession
        from mcp.client.stdio import stdio_client

        async with (
            stdio_client(self._parameters()) as (read_stream, write_stream),
            ClientSession(read_stream, write_stream) as session,
        ):
            await session.initialize()
            tools = await session.list_tools()
            if "incident_raise" not in {tool.name for tool in tools.tools}:
                return PublicationResult(False, None, "tool_not_available")
            called = await session.call_tool(
                "incident_raise", arguments={"alert": alert}
            )
            if called.isError or not isinstance(called.structuredContent, dict):
                return PublicationResult(False, None, "tool_error")
            response = called.structuredContent
            delivery = response.get("delivery")
            dba = response.get("dba")
            return PublicationResult(
                published_to_mcp=response.get("accepted") is True,
                accepted=(
                    response.get("accepted")
                    if isinstance(response.get("accepted"), bool)
                    else None
                ),
                mcp_status=str(response.get("status") or "unknown"),
                delivery_status=(
                    str(delivery.get("status")) if isinstance(delivery, dict) else None
                ),
                dba_status=(str(dba.get("status")) if isinstance(dba, dict) else None),
                error_code=(
                    "mcp_rejected_alert" if response.get("accepted") is False else None
                ),
            )

    def publish(self, alert: dict[str, Any]) -> PublicationResult:
        validate_alert(alert)
        try:
            return asyncio.run(self._publish_async(alert))
        except Exception as error:
            self.logger.warning(
                "mcp_publish_failed alert_id=%s error_code=%s",
                alert["alert_id"],
                type(error).__name__,
            )
            return PublicationResult(
                False, None, "transport_error", error_code="mcp_transport_error"
            )


class AlertState:
    """Minimal private state containing only successfully routed event keys."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schema_version": "audit_agent_alert_state.v1", "published": {}}
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or not isinstance(value.get("published"), dict):
            raise ValueError("alert_state_invalid")
        return value

    def was_published(self, dedupe_key: str) -> bool:
        return dedupe_key in self._load()["published"]

    def record(self, dedupe_key: str, alert: dict[str, Any]) -> None:
        value = self._load()
        value["published"][dedupe_key] = {
            "last_sent_at": alert["detected_at"],
            "last_audit_id": alert["audit_id"],
            "alert_id": alert["alert_id"],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=self.path.parent, delete=False
        ) as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            temporary = Path(handle.name)
        os.chmod(temporary, 0o600)
        os.replace(temporary, self.path)
