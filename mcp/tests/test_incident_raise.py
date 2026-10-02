from __future__ import annotations

import json
import logging
import os
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from mysqlconf_mcp.delivery import DbaDelivery, NotificationDelivery
from mysqlconf_mcp.server import mcp
from mysqlconf_mcp.tools.incidents import process_incident_raise

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
HEALTH_ALERT_FIXTURE = (
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


def load_health_alert(*, severity: str = "critical") -> dict:
    alert = json.loads(HEALTH_ALERT_FIXTURE.read_text(encoding="utf-8"))
    alert["severity"] = severity
    return alert


def load_audit_fixture() -> dict:
    return json.loads(AUDIT_FIXTURE.read_text(encoding="utf-8"))


async def call_incident_raise(alert: dict) -> dict:
    with tempfile.TemporaryDirectory() as temporary:
        environment = {
            "MCP_NOTIFICATION_ENABLED": "false",
            "MCP_DBA_ENABLED": "true",
            "MCP_DBA_ALERTS_DIR": str(Path(temporary) / "health"),
            "MCP_DBA_AUDIT_ALERTS_DIR": str(Path(temporary) / "audit"),
        }
        with patch.dict(os.environ, environment, clear=True):
            _, structured = await mcp.call_tool("incident_raise", {"alert": alert})
    assert structured is not None
    return structured


def forwarding_environment() -> dict[str, str]:
    return {
        "MCP_NOTIFICATION_ENABLED": "true",
        "NOTIFICATION_DELIVERY_ENABLED": "false",
        "NOTIFICATION_EMAIL_RECIPIENTS_WARNING": "warning@example.invalid",
        "NOTIFICATION_EMAIL_RECIPIENTS_CRITICAL": "critical@example.invalid",
    }


class SpyGateway:
    def __init__(self, result: NotificationDelivery | None = None) -> None:
        self.calls = 0
        self.alert: dict | None = None
        self.result = result or NotificationDelivery(
            target="notification",
            channel="email",
            status="dry_run",
            delivered=False,
        )

    def deliver(self, alert: dict) -> NotificationDelivery:
        self.calls += 1
        self.alert = alert
        return self.result


class SpyDbaGateway:
    def __init__(self, result: DbaDelivery | None = None) -> None:
        self.calls = 0
        self.alert: dict | None = None
        self.result = result or DbaDelivery(
            target="dba",
            status="recorded",
            recorded=True,
            record_id=(
                "2026-09-15T16-38-13.975000Z__query_latency__"
                "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
            ),
            directory=(
                "2026-09-15T16-38-13.975000Z__query_latency__"
                "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
            ),
            alert_file=(
                "2026-09-15T16-38-13.975000Z__query_latency__"
                "dddddddd-dddd-4ddd-8ddd-dddddddddddd/alert.json"
            ),
            report_json_file=(
                "2026-09-15T16-38-13.975000Z__query_latency__"
                "dddddddd-dddd-4ddd-8ddd-dddddddddddd/latest.json"
            ),
            report_html_file=(
                "2026-09-15T16-38-13.975000Z__query_latency__"
                "dddddddd-dddd-4ddd-8ddd-dddddddddddd/latest.html"
            ),
        )

    def deliver(self, alert: dict) -> DbaDelivery:
        self.calls += 1
        self.alert = alert
        return self.result


class IncidentRaiseMcpTests(unittest.IsolatedAsyncioTestCase):
    async def test_tool_discovery_exposes_supported_workflows(self) -> None:
        tools = await mcp.list_tools()
        self.assertEqual(
            [tool.name for tool in tools],
            [
                "incident_raise",
                "refactor_request_raise",
                "refactor_result_raise",
            ],
        )
        self.assertIn("health_check_alert.v1", tools[0].description)
        self.assertEqual(tools[0].inputSchema["required"], ["alert"])

    async def test_luna_latency_critical_fixture_is_accepted(self) -> None:
        result = await call_incident_raise(load_health_alert())
        self.assertTrue(result["accepted"])
        self.assertEqual(result["status"], "validated")
        self.assertEqual(result["delivery"]["status"], "not_configured")
        self.assertEqual(result["delivery"]["target"], "notification")

    async def test_luna_latency_warning_variant_is_accepted(self) -> None:
        result = await call_incident_raise(load_health_alert(severity="warning"))
        self.assertTrue(result["accepted"])
        self.assertEqual(result["status"], "validated")
        self.assertEqual(result["delivery"]["status"], "not_configured")

    async def test_incident_requires_dba_persistence_before_acceptance(self) -> None:
        result = process_incident_raise(
            load_audit_fixture(),
            environ={
                "MCP_NOTIFICATION_ENABLED": "false",
                "MCP_DBA_ENABLED": "false",
            },
        )
        self.assertFalse(result["accepted"])
        self.assertEqual(result["status"], "persistence_required")
        self.assertEqual(result["dba"]["status"], "not_configured")
        self.assertEqual(result["delivery"]["status"], "not_attempted")
        self.assertEqual(result["delivery"]["error_code"], "dba_persistence_required")

    async def test_audit_schema_change_is_accepted_and_fanned_out(self) -> None:
        alert = deepcopy(load_audit_fixture())
        alert["category"] = "schema_change"
        alert["findings"][0]["check_id"] = "audit_security.sakila.blocked_alter_table"
        alert["findings"][0]["metric"] = "blocked_schema_change_attempt"
        alert["findings"][0]["evidence"]["sql_command"] = "alter_table"
        notification = SpyGateway()
        dba = SpyDbaGateway()

        result = process_incident_raise(
            alert,
            environ={
                "MCP_NOTIFICATION_ENABLED": "true",
                "MCP_DBA_ENABLED": "true",
            },
            delivery_factory=lambda environ: notification,
            dba_factory=lambda environ: dba,
        )

        self.assertTrue(result["accepted"])
        self.assertEqual(result["delivery"]["status"], "dry_run")
        self.assertEqual(result["dba"]["status"], "recorded")
        self.assertIs(notification.alert, alert)
        self.assertIs(dba.alert, alert)

    async def test_unimplemented_audit_category_is_rejected(self) -> None:
        alert = load_audit_fixture()
        alert["category"] = "unimplemented_category"
        notification = SpyGateway()
        dba = SpyDbaGateway()

        result = process_incident_raise(
            alert,
            environ={
                "MCP_NOTIFICATION_ENABLED": "true",
                "MCP_DBA_ENABLED": "true",
            },
            delivery_factory=lambda environ: notification,
            dba_factory=lambda environ: dba,
        )

        self.assert_rejected_with(result, "invalid_category")
        self.assertEqual(notification.calls, 0)
        self.assertEqual(dba.calls, 0)

    async def test_invalid_alert_is_rejected_before_notification(self) -> None:
        alert = load_health_alert()
        alert["severity"] = "urgent"
        spy = SpyGateway()
        dba_spy = SpyDbaGateway()

        result = process_incident_raise(
            alert,
            environ=forwarding_environment(),
            delivery_factory=lambda environ: spy,
            dba_factory=lambda environ: dba_spy,
        )

        self.assert_rejected_with(result, "invalid_severity")
        self.assertEqual(spy.calls, 0)
        self.assertEqual(dba_spy.calls, 0)

    async def test_disabled_forwarding_does_not_construct_notification(self) -> None:
        alert = load_health_alert(severity="warning")
        factory_calls = 0

        def forbidden_factory(environ: object) -> SpyGateway:
            nonlocal factory_calls
            factory_calls += 1
            raise AssertionError("notification must remain disabled")

        result = process_incident_raise(
            alert,
            environ={
                "MCP_NOTIFICATION_ENABLED": "false",
                "MCP_DBA_ENABLED": "true",
            },
            delivery_factory=forbidden_factory,
            dba_factory=lambda environ: SpyDbaGateway(),
        )

        self.assertTrue(result["accepted"])
        self.assertEqual(
            result["delivery"],
            {
                "target": "notification",
                "status": "not_configured",
            },
        )
        self.assertEqual(factory_calls, 0)

    async def test_enabled_forwarding_passes_complete_alert_to_gateway(self) -> None:
        alert = load_health_alert(severity="warning")
        spy = SpyGateway()
        dba = SpyDbaGateway()

        result = process_incident_raise(
            alert,
            environ={**forwarding_environment(), "MCP_DBA_ENABLED": "true"},
            delivery_factory=lambda environ: spy,
            dba_factory=lambda environ: dba,
        )

        self.assertTrue(result["accepted"])
        self.assertIs(spy.alert, alert)
        self.assertEqual(spy.calls, 1)

    async def test_dba_route_is_independent_from_notification(self) -> None:
        alert = load_health_alert()
        dba_spy = SpyDbaGateway()

        result = process_incident_raise(
            alert,
            environ={
                "MCP_NOTIFICATION_ENABLED": "false",
                "MCP_DBA_ENABLED": "true",
            },
            dba_factory=lambda environ: dba_spy,
        )

        self.assertTrue(result["accepted"])
        self.assertEqual(result["delivery"]["status"], "not_configured")
        self.assertEqual(result["dba"]["status"], "recorded")
        self.assertTrue(result["dba"]["recorded"])
        self.assertIs(dba_spy.alert, alert)
        self.assertEqual(dba_spy.calls, 1)

    async def test_invalid_notification_flag_does_not_block_dba(self) -> None:
        alert = load_health_alert()
        dba_spy = SpyDbaGateway()

        result = process_incident_raise(
            alert,
            environ={
                "MCP_NOTIFICATION_ENABLED": "invalid",
                "MCP_DBA_ENABLED": "true",
            },
            dba_factory=lambda environ: dba_spy,
        )

        self.assertEqual(result["delivery"]["status"], "failed")
        self.assertEqual(
            result["delivery"]["error_code"],
            "mcp_notification_enabled_invalid",
        )
        self.assertEqual(result["dba"]["status"], "recorded")
        self.assertEqual(dba_spy.calls, 1)

    async def test_invalid_dba_flag_blocks_notification(self) -> None:
        alert = load_health_alert()
        notification_spy = SpyGateway()

        result = process_incident_raise(
            alert,
            environ={
                "MCP_NOTIFICATION_ENABLED": "true",
                "MCP_DBA_ENABLED": "invalid",
            },
            delivery_factory=lambda environ: notification_spy,
        )

        self.assertFalse(result["accepted"])
        self.assertEqual(result["status"], "persistence_required")
        self.assertEqual(result["delivery"]["status"], "not_attempted")
        self.assertEqual(result["delivery"]["error_code"], "dba_persistence_required")
        self.assertEqual(notification_spy.calls, 0)
        self.assertEqual(result["dba"]["status"], "failed")
        self.assertEqual(result["dba"]["error_code"], "mcp_dba_enabled_invalid")

    async def test_dba_is_persisted_before_notification_is_called(self) -> None:
        alert = load_health_alert()
        calls: list[str] = []

        class OrderedDbaGateway(SpyDbaGateway):
            def deliver(self, alert: dict) -> DbaDelivery:
                calls.append("dba")
                return super().deliver(alert)

        class OrderedNotificationGateway(SpyGateway):
            def deliver(self, alert: dict) -> NotificationDelivery:
                calls.append("notification")
                return super().deliver(alert)

        result = process_incident_raise(
            alert,
            environ={
                **forwarding_environment(),
                "MCP_DBA_ENABLED": "true",
            },
            delivery_factory=lambda environ: OrderedNotificationGateway(),
            dba_factory=lambda environ: OrderedDbaGateway(),
        )

        self.assertTrue(result["accepted"])
        self.assertEqual(calls, ["dba", "notification"])

    async def test_dba_failure_prevents_notification(self) -> None:
        alert = load_health_alert()
        notification = SpyGateway()
        dba = SpyDbaGateway(DbaDelivery.failed("dba_persistence_failed"))

        result = process_incident_raise(
            alert,
            environ={
                **forwarding_environment(),
                "MCP_DBA_ENABLED": "true",
            },
            delivery_factory=lambda environ: notification,
            dba_factory=lambda environ: dba,
        )

        self.assertFalse(result["accepted"])
        self.assertEqual(result["status"], "persistence_required")
        self.assertEqual(result["dba"]["status"], "failed")
        self.assertEqual(result["delivery"]["status"], "not_attempted")
        self.assertEqual(notification.calls, 0)

    async def test_real_dba_adapter_records_full_alert_without_email(self) -> None:
        alert = load_health_alert()
        with tempfile.TemporaryDirectory() as temporary:
            result = process_incident_raise(
                alert,
                environ={
                    "MCP_NOTIFICATION_ENABLED": "false",
                    "MCP_DBA_ENABLED": "true",
                    "MCP_DBA_ALERTS_DIR": temporary,
                },
            )
            directories = sorted(Path(temporary).iterdir())
            record_directory = directories[0]
            record_directory_was_directory = record_directory.is_dir()
            files = sorted(path.name for path in record_directory.iterdir())
            record = json.loads(
                (record_directory / "alert.json").read_text(encoding="utf-8")
            )
            report_json = json.loads(
                (record_directory / "latest.json").read_text(encoding="utf-8")
            )
            report_html = (record_directory / "latest.html").read_text(encoding="utf-8")

        self.assertEqual(result["delivery"]["status"], "not_configured")
        self.assertEqual(result["dba"]["status"], "recorded")
        self.assertEqual(len(directories), 1)
        self.assertTrue(record_directory_was_directory)
        self.assertEqual(files, ["alert.json", "latest.html", "latest.json"])
        self.assertEqual(record["alert"]["alert_id"], alert["alert_id"])
        self.assertEqual(record["alert"]["audit_id"], alert["audit_id"])
        self.assertEqual(record["alert"]["report"]["json_file"], "latest.json")
        self.assertEqual(record["alert"]["report"]["html_file"], "latest.html")
        self.assertNotIn("json", record["alert"]["report"])
        self.assertNotIn("html", record["alert"]["report"])
        self.assertEqual(report_json, alert["report"]["json"])
        self.assertEqual(report_html, alert["report"]["html"])

    async def test_audit_routes_to_sanitized_dba_inbox_and_notification_dry_run(
        self,
    ) -> None:
        alert = load_audit_fixture()
        with tempfile.TemporaryDirectory() as temporary:
            audit_inbox = Path(temporary) / "audit"
            health_inbox = Path(temporary) / "health"
            with patch(
                "notification.channels.email.EmailChannel.send",
                side_effect=AssertionError("real e-mail is forbidden in tests"),
            ) as send:
                result = process_incident_raise(
                    alert,
                    environ={
                        **forwarding_environment(),
                        "MCP_DBA_ENABLED": "true",
                        "MCP_DBA_ALERTS_DIR": str(health_inbox),
                        "MCP_DBA_AUDIT_ALERTS_DIR": str(audit_inbox),
                    },
                )
            directories = list(audit_inbox.iterdir())
            files = sorted(path.name for path in directories[0].iterdir())
            event_summary = json.loads(
                (directories[0] / "audit-event-summary.json").read_text(
                    encoding="utf-8"
                )
            )
            health_inbox_created = health_inbox.exists()

        self.assertEqual(result["delivery"]["status"], "dry_run")
        self.assertFalse(result["delivery"]["delivered"])
        self.assertEqual(result["dba"]["status"], "recorded")
        self.assertTrue(result["dba"]["recorded"])
        self.assertEqual(files, ["alert-summary.json", "audit-event-summary.json"])
        self.assertEqual(event_summary["sql_command"], "drop_table")
        self.assertEqual(event_summary["status_code"], 1142)
        self.assertFalse(health_inbox_created)
        send.assert_not_called()

    async def test_audit_dba_route_deduplicates_by_event_key(self) -> None:
        first = load_audit_fixture()
        second = deepcopy(first)
        second["alert_id"] = "dddddddd-4444-4444-8444-dddddddddddd"
        with tempfile.TemporaryDirectory() as temporary:
            environment = {
                "MCP_NOTIFICATION_ENABLED": "false",
                "MCP_DBA_ENABLED": "true",
                "MCP_DBA_AUDIT_ALERTS_DIR": temporary,
            }
            initial = process_incident_raise(first, environ=environment)
            duplicate = process_incident_raise(second, environ=environment)

        self.assertEqual(initial["dba"]["status"], "recorded")
        self.assertEqual(duplicate["dba"]["status"], "duplicate")
        self.assertFalse(duplicate["dba"]["recorded"])

    async def test_warning_dry_run_uses_real_notification_dispatcher(self) -> None:
        alert = load_health_alert(severity="warning")
        dba = SpyDbaGateway()
        with patch(
            "notification.channels.email.EmailChannel.send",
            side_effect=AssertionError("e-mail real is forbidden in tests"),
        ) as send:
            result = process_incident_raise(
                alert,
                environ={**forwarding_environment(), "MCP_DBA_ENABLED": "true"},
                dba_factory=lambda environ: dba,
            )

        self.assertEqual(
            result["delivery"],
            {
                "target": "notification",
                "status": "dry_run",
                "delivered": False,
                "channel": "email",
            },
        )
        send.assert_not_called()

    async def test_critical_dry_run_uses_real_notification_dispatcher(self) -> None:
        alert = load_health_alert()
        dba = SpyDbaGateway()
        with patch(
            "notification.channels.email.EmailChannel.send",
            side_effect=AssertionError("e-mail real is forbidden in tests"),
        ) as send:
            result = process_incident_raise(
                alert,
                environ={**forwarding_environment(), "MCP_DBA_ENABLED": "true"},
                dba_factory=lambda environ: dba,
            )

        self.assertEqual(result["delivery"]["status"], "dry_run")
        self.assertFalse(result["delivery"]["delivered"])
        self.assertEqual(result["delivery"]["channel"], "email")
        send.assert_not_called()

    async def test_info_result_comes_from_notification_policy(self) -> None:
        alert = load_health_alert()
        alert["severity"] = "info"
        dba = SpyDbaGateway()
        with patch(
            "notification.channels.email.EmailChannel.send",
            side_effect=AssertionError("e-mail real is forbidden in tests"),
        ) as send:
            result = process_incident_raise(
                alert,
                environ={**forwarding_environment(), "MCP_DBA_ENABLED": "true"},
                dba_factory=lambda environ: dba,
            )

        self.assertEqual(result["delivery"]["status"], "suppressed_by_policy")
        self.assertFalse(result["delivery"]["delivered"])
        self.assertEqual(result["delivery"]["channel"], "email")
        send.assert_not_called()

    async def test_controlled_notification_failure_keeps_alert_accepted(self) -> None:
        alert = load_health_alert(severity="warning")
        result = process_incident_raise(
            alert,
            environ={
                "MCP_NOTIFICATION_ENABLED": "true",
                "MCP_DBA_ENABLED": "true",
                "NOTIFICATION_DELIVERY_ENABLED": "false",
            },
            dba_factory=lambda environ: SpyDbaGateway(),
        )

        self.assertTrue(result["accepted"])
        self.assertEqual(result["status"], "validated")
        self.assertEqual(
            result["delivery"],
            {
                "target": "notification",
                "status": "failed",
                "delivered": False,
                "error_code": "recipients_not_configured",
            },
        )

    async def test_sent_result_is_normalized_without_direct_email(self) -> None:
        alert = load_health_alert(severity="warning")
        spy = SpyGateway(
            NotificationDelivery(
                target="notification",
                channel="email",
                status="sent",
                delivered=True,
            )
        )

        result = process_incident_raise(
            alert,
            environ={**forwarding_environment(), "MCP_DBA_ENABLED": "true"},
            delivery_factory=lambda environ: spy,
            dba_factory=lambda environ: SpyDbaGateway(),
        )

        self.assertEqual(result["delivery"]["status"], "sent")
        self.assertTrue(result["delivery"]["delivered"])

    async def test_logs_exclude_reports_and_include_safe_statuses(self) -> None:
        alert = load_health_alert(severity="warning")
        with self.assertLogs(
            "mysqlconf_mcp.tools.incidents", level=logging.INFO
        ) as logs:
            result = process_incident_raise(
                alert,
                environ={
                    "MCP_NOTIFICATION_ENABLED": "false",
                    "MCP_DBA_ENABLED": "true",
                },
                dba_factory=lambda environ: SpyDbaGateway(),
            )

        rendered = "\n".join(logs.output)
        self.assertTrue(result["accepted"])
        self.assertIn("validation=validated", rendered)
        self.assertIn("delivery_status=not_configured", rendered)
        self.assertIn("dba_status=recorded", rendered)
        self.assertNotIn(alert["summary"], rendered)
        self.assertNotIn(alert["report"]["html"], rendered)

    async def test_invalid_contract_version_is_rejected(self) -> None:
        alert = load_health_alert()
        alert["contract_version"] = "health_check_alert.v2"
        result = await call_incident_raise(alert)
        self.assert_rejected_with(result, "invalid_contract_version")

    async def test_invalid_severity_is_rejected(self) -> None:
        alert = load_health_alert()
        alert["severity"] = "urgent"
        result = await call_incident_raise(alert)
        self.assert_rejected_with(result, "invalid_severity")

    async def test_invalid_category_is_rejected(self) -> None:
        alert = load_health_alert()
        alert["category"] = "security"
        result = await call_incident_raise(alert)
        self.assert_rejected_with(result, "invalid_category")

    async def test_missing_json_or_html_is_rejected(self) -> None:
        for field in ("json", "html"):
            with self.subTest(field=field):
                alert = load_health_alert()
                del alert["report"][field]
                result = await call_incident_raise(alert)
                self.assert_rejected_with(result, "required_field_missing")
                self.assertIn(
                    f"alert.report.{field}",
                    {error["field"] for error in result["errors"]},
                )

    async def test_inconsistent_audit_id_is_rejected(self) -> None:
        alert = load_health_alert()
        alert["report"]["json"]["audit_id"] = "44444444-4444-4444-8444-444444444444"
        result = await call_incident_raise(alert)
        self.assert_rejected_with(result, "audit_id_mismatch")
        self.assertIn(
            "alert.report.json.audit_id",
            {error["field"] for error in result["errors"]},
        )

    async def test_sensitive_field_is_rejected_without_echoing_value(self) -> None:
        alert = load_health_alert()
        secret = "never-echo-this-value"
        alert["metadata"]["password"] = secret
        result = await call_incident_raise(alert)
        self.assert_rejected_with(result, "sensitive_field")
        self.assertNotIn(secret, json.dumps(result))

    async def test_private_ip_is_rejected_without_echoing_value(self) -> None:
        alert = load_health_alert()
        private_ip = "10.20.30.40"
        alert["metadata"]["observed_address"] = private_ip
        result = await call_incident_raise(alert)
        self.assert_rejected_with(result, "sensitive_value")
        self.assertNotIn(private_ip, json.dumps(result))

    async def test_token_and_connection_string_are_rejected(self) -> None:
        cases = {
            "token": {"token": "example-token"},
            "connection_string": {
                "note": "mysql://example-user:example-password@example.invalid/db"
            },
        }
        for expected_code, metadata in cases.items():
            with self.subTest(case=expected_code):
                alert = load_health_alert()
                alert["metadata"] = metadata
                result = await call_incident_raise(alert)
                self.assertFalse(result["accepted"])
                self.assertTrue(
                    {"sensitive_field", "sensitive_value"}
                    & {error["code"] for error in result["errors"]}
                )

    async def test_detection_before_collection_is_rejected(self) -> None:
        alert = load_health_alert(severity="warning")
        alert["detected_at"] = "2026-01-15T12:59:59.000Z"
        result = await call_incident_raise(alert)
        self.assert_rejected_with(result, "detected_before_collection")

    async def test_validation_does_not_mutate_alert(self) -> None:
        alert = load_health_alert()
        original = deepcopy(alert)
        await call_incident_raise(alert)
        self.assertEqual(alert, original)

    def assert_rejected_with(self, result: dict, code: str) -> None:
        self.assertFalse(result["accepted"])
        self.assertEqual(result["status"], "rejected")
        self.assertIn(code, {error["code"] for error in result["errors"]})


if __name__ == "__main__":
    unittest.main()
