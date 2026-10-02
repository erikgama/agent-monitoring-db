from __future__ import annotations

import json
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = (
    REPOSITORY_ROOT
    / "agents"
    / "health-check"
    / "alerts"
    / "examples"
    / "select-latency-p99-critical.json"
)
AUDIT_FIXTURE = (
    REPOSITORY_ROOT
    / "agents"
    / "notification"
    / "fixtures"
    / "audit-destructive-ddl-critical.json"
)


class StdioTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_client_discovers_and_calls_incident_raise_over_stdio(self) -> None:
        alert = json.loads(FIXTURE.read_text(encoding="utf-8"))

        with tempfile.TemporaryDirectory() as temporary:
            parameters = StdioServerParameters(
                command=sys.executable,
                args=["-m", "mysqlconf_mcp.server"],
                env={
                    "MCP_NOTIFICATION_ENABLED": "true",
                    "MCP_DBA_ENABLED": "true",
                    "MCP_DBA_ALERTS_DIR": temporary,
                    "NOTIFICATION_DELIVERY_ENABLED": "false",
                    "NOTIFICATION_EMAIL_RECIPIENTS_WARNING": (
                        "warning-operator@example.invalid"
                    ),
                    "NOTIFICATION_EMAIL_RECIPIENTS_CRITICAL": (
                        "critical-operator@example.invalid"
                    ),
                },
            )

            async with (
                stdio_client(parameters) as (read_stream, write_stream),
                ClientSession(read_stream, write_stream) as session,
            ):
                await session.initialize()
                listed = await session.list_tools()
                self.assertEqual(
                    [tool.name for tool in listed.tools],
                    [
                        "incident_raise",
                        "refactor_request_raise",
                        "refactor_result_raise",
                    ],
                )

                called = await session.call_tool(
                    "incident_raise",
                    arguments={"alert": alert},
                )
                self.assertFalse(called.isError)
                self.assertIsNotNone(called.structuredContent)
                self.assertTrue(called.structuredContent["accepted"])
                self.assertEqual(called.structuredContent["status"], "validated")
                self.assertEqual(
                    called.structuredContent["delivery"],
                    {
                        "target": "notification",
                        "status": "dry_run",
                        "delivered": False,
                        "channel": "email",
                    },
                )
                self.assertEqual(called.structuredContent["dba"]["status"], "recorded")
                self.assertTrue(called.structuredContent["dba"]["recorded"])

            directories = list(Path(temporary).iterdir())
            self.assertEqual(len(directories), 1)
            self.assertTrue(directories[0].is_dir())
            self.assertEqual(
                sorted(path.name for path in directories[0].iterdir()),
                ["alert.json", "latest.html", "latest.json"],
            )

    async def test_audit_alert_reaches_notification_and_dedicated_dba_inbox(
        self,
    ) -> None:
        alert = json.loads(AUDIT_FIXTURE.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as temporary:
            inbox = Path(temporary) / "audit"
            parameters = StdioServerParameters(
                command=sys.executable,
                args=["-m", "mysqlconf_mcp.server"],
                env={
                    "MCP_NOTIFICATION_ENABLED": "true",
                    "MCP_DBA_ENABLED": "true",
                    "MCP_DBA_AUDIT_ALERTS_DIR": str(inbox),
                    "NOTIFICATION_DELIVERY_ENABLED": "false",
                    "NOTIFICATION_EMAIL_RECIPIENTS_CRITICAL": (
                        "critical-operator@example.invalid"
                    ),
                },
            )
            async with (
                stdio_client(parameters) as (read_stream, write_stream),
                ClientSession(read_stream, write_stream) as session,
            ):
                await session.initialize()
                called = await session.call_tool(
                    "incident_raise", arguments={"alert": alert}
                )
                result = called.structuredContent

            self.assertIsNotNone(result)
            self.assertTrue(result["accepted"])
            self.assertEqual(result["delivery"]["status"], "dry_run")
            self.assertFalse(result["delivery"]["delivered"])
            self.assertEqual(result["dba"]["status"], "recorded")
            directories = list(inbox.iterdir())
            self.assertEqual(len(directories), 1)
            self.assertEqual(
                sorted(path.name for path in directories[0].iterdir()),
                ["alert-summary.json", "audit-event-summary.json"],
            )

    async def test_schema_change_traverses_real_stdio_in_dry_run(self) -> None:
        alert = deepcopy(json.loads(AUDIT_FIXTURE.read_text(encoding="utf-8")))
        alert["category"] = "schema_change"
        alert["title"] = "Tentativa sem permissão de ALTER TABLE em sakila"
        alert["findings"][0]["check_id"] = "audit_security.sakila.blocked_alter_table"
        alert["findings"][0]["metric"] = "blocked_schema_change_attempt"
        alert["findings"][0]["evidence"]["sql_command"] = "alter_table"
        alert["dedupe_key"] = alert["dedupe_key"].replace(
            "blocked-destructive-ddl", "blocked-alter-table"
        )
        with tempfile.TemporaryDirectory() as temporary:
            inbox = Path(temporary) / "audit"
            parameters = StdioServerParameters(
                command=sys.executable,
                args=["-m", "mysqlconf_mcp.server"],
                env={
                    "MCP_NOTIFICATION_ENABLED": "true",
                    "MCP_DBA_ENABLED": "true",
                    "MCP_DBA_AUDIT_ALERTS_DIR": str(inbox),
                    "NOTIFICATION_DELIVERY_ENABLED": "false",
                    "NOTIFICATION_EMAIL_RECIPIENTS_CRITICAL": (
                        "critical-operator@example.invalid"
                    ),
                },
            )
            async with (
                stdio_client(parameters) as (read_stream, write_stream),
                ClientSession(read_stream, write_stream) as session,
            ):
                await session.initialize()
                called = await session.call_tool(
                    "incident_raise", arguments={"alert": alert}
                )
                result = called.structuredContent

            self.assertIsNotNone(result)
            self.assertTrue(result["accepted"])
            self.assertEqual(result["delivery"]["status"], "dry_run")
            self.assertEqual(result["dba"]["status"], "recorded")
            directory = next(inbox.iterdir())
            summary = json.loads(
                (directory / "audit-event-summary.json").read_text(encoding="utf-8")
            )
            self.assertEqual(summary["metric"], "blocked_schema_change_attempt")
            self.assertEqual(summary["sql_command"], "alter_table")


if __name__ == "__main__":
    unittest.main()
