from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

from audit_security.alerting import McpIncidentPublisher, build_agent_alert
from audit_security.artifacts import write_artifacts
from audit_security.collector import (
    BASE_DIR,
    SQL_DIR,
    audit_rows,
    build_audit_query,
    collect_snapshot,
)
from audit_security.main import run_collector
from audit_security.renderer import render_html
from audit_security.sql_safety import UnsafeSqlError, validate_read_only_sql


class FakeClient:
    def query(self, sql: str) -> list[dict[str, Any]]:
        if "collector_domain: instance_security" in sql:
            return [{"audit_plugin_status": "ACTIVE", "require_secure_transport": "1"}]
        if "collector_domain: audit_configuration" in sql:
            return [{"filter_name": "sakila_security_monitoring", "json_valid": "1"}]
        if "collector_domain: audit_events" in sql:
            events = [
                {
                    "timestamp": "2026-09-16T12:00:01Z",
                    "id": 7,
                    "class": "general",
                    "event": "status",
                    "connection_id": "42",
                    "general_data": {
                        "db": "sakila",
                        "status": 1142,
                        "sql_command": "drop_table",
                        "query": "DROP TABLE sakila.audit_security_demo_events",
                    },
                },
                None,
            ]
            return [{"page_index": 0, "audit_payload": json.dumps(events)}]
        return []


def decision_for(report: dict[str, Any]) -> dict[str, Any]:
    row = report["domains"]["audit_ddl"]["rows"][0]
    fields = (
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
    return {
        "rule_id": "audit_security.sakila.blocked_destructive_ddl",
        "event_key": row["event_key"],
        "category": "destructive_ddl",
        "severity": "critical",
        "title": "Blocked destructive DDL attempt in sakila",
        "summary": "The database denied a destructive DDL attempt in sakila.",
        "metric": "blocked_destructive_ddl_attempt",
        "evidence": {name: row.get(name) for name in fields},
    }


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.value += seconds


class AuditSecurityTests(unittest.TestCase):
    def test_all_official_queries_are_read_only(self) -> None:
        for path in SQL_DIR.glob("*.sql"):
            sql = (
                build_audit_query(1, 100)
                if path.name == "60_audit_events.sql"
                else path.read_text(encoding="utf-8")
            )
            validate_read_only_sql(sql)

    def test_mutations_are_rejected(self) -> None:
        for sql in (
            "DROP TABLE sakila.film",
            "UPDATE sakila.film SET title = 'x'",
            "SET GLOBAL audit_log_disable = ON",
        ):
            with self.subTest(sql=sql), self.assertRaises(UnsafeSqlError):
                validate_read_only_sql(sql)

    def test_collector_persists_facts_without_making_alert_decisions(self) -> None:
        report = collect_snapshot(
            FakeClient(), window_minutes=1, max_events=100, max_rows=50
        )
        self.assertEqual(report["scope"]["functional_schemas"], ["sakila"])
        self.assertEqual(report["policy_evaluation"]["status"], "delegated_to_agent")
        self.assertNotIn("intelligence", report)
        for domain in report["domains"].values():
            self.assertNotIn("findings", domain)
        serialized = json.dumps(report)
        self.assertNotIn("DROP TABLE", serialized)
        self.assertNotIn("decisions", serialized)

    def test_report_pair_is_atomic_and_summary_is_not_generated(self) -> None:
        report = collect_snapshot(
            FakeClient(), window_minutes=1, max_events=100, max_rows=50
        )
        html_document = render_html(report)
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            write_artifacts(output, report, html_document)
            stored = json.loads((output / "latest.json").read_text())
            stored_html = (output / "latest.html").read_text()
            self.assertFalse((output / "summary.json").exists())
        self.assertEqual(stored["audit_id"], report["audit_id"])
        self.assertIn(report["audit_id"], stored_html)
        self.assertIn(report["collected_at"], stored_html)

    def test_agent_alert_uses_exact_collector_evidence(self) -> None:
        report = collect_snapshot(
            FakeClient(), window_minutes=1, max_events=100, max_rows=50
        )
        alert = build_agent_alert(
            report,
            render_html(report),
            decision_for(report),
            "test",
            agent_started_at=datetime(2026, 9, 16, 12, 0, tzinfo=UTC),
            llm_model="claude-test-model",
            llm_provider="claude",
        )
        self.assertEqual(alert["source"], "audit-security")
        self.assertEqual(alert["metadata"]["decision_owner"], "audit-luna")
        self.assertEqual(alert["metadata"]["model"], "claude-test-model")
        self.assertEqual(alert["metadata"]["provider"], "claude")
        self.assertNotIn("DROP TABLE", json.dumps(alert))

    def test_agent_cannot_select_evidence_not_present_in_report(self) -> None:
        report = collect_snapshot(
            FakeClient(), window_minutes=1, max_events=100, max_rows=50
        )
        decision = decision_for(report)
        decision["evidence"]["status_code"] = 9999
        with self.assertRaisesRegex(ValueError, "agent_evidence_does_not_match_report"):
            build_agent_alert(
                report,
                render_html(report),
                decision,
                "test",
                agent_started_at=datetime(2026, 9, 16, 12, 0, tzinfo=UTC),
            )

    def test_continuous_collector_uses_fifteen_second_interval(self) -> None:
        clock = FakeClock()
        events: list[dict[str, Any]] = []
        with tempfile.TemporaryDirectory() as temporary:
            run_collector(
                client=FakeClient(),
                output_directory=Path(temporary),
                max_cycles=2,
                sleep_fn=clock.sleep,
                monotonic_fn=clock.monotonic,
                on_event=events.append,
            )
        self.assertEqual(clock.sleeps, [15.0])
        self.assertEqual(
            [item["status"] for item in events], ["collected", "collected"]
        )

    def test_mcp_child_never_receives_smtp_password_from_audit(self) -> None:
        publisher = McpIncidentPublisher(
            repository_root=BASE_DIR.parent.parent,
            server_environment={
                "MCP_NOTIFICATION_ENABLED": "true",
                "SMTP_PASSWORD": "must-not-cross-boundary",
                "NOTIFICATION_SMTP_CREDENTIAL_HELPER": "/tmp/example-secret-helper",
            },
        )
        with patch.dict("os.environ", {"SMTP_PASSWORD": "secret"}, clear=False):
            parameters = publisher._parameters()
        self.assertNotIn("SMTP_PASSWORD", parameters.env)
        self.assertEqual(
            parameters.env["NOTIFICATION_SMTP_CREDENTIAL_HELPER"],
            "/tmp/example-secret-helper",
        )

    def test_mcp_child_has_an_isolated_uv_cache_by_default(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            parameters = McpIncidentPublisher(
                repository_root=BASE_DIR.parent.parent
            )._parameters()

        self.assertIn("UV_CACHE_DIR", parameters.env)
        self.assertTrue(parameters.env["UV_CACHE_DIR"].endswith("mysqlconf-uv-cache"))

    def test_raw_ddl_is_masked_but_scope_is_preserved(self) -> None:
        event = {
            "class": "general",
            "event": "status",
            "timestamp": "2026-09-16T12:00:01Z",
            "id": 3,
            "general_data": {
                "db": None,
                "status": 1044,
                "sql_command": "drop_db",
                "query": "DROP DATABASE `sakila`",
            },
        }
        row = audit_rows("audit_ddl", [event])[0]
        self.assertEqual(row["schema_scope"], "sakila")
        self.assertNotIn("query", row)

    def test_mysql_audit_timestamp_without_suffix_is_normalized_to_utc(self) -> None:
        event = {
            "class": "general",
            "event": "status",
            "timestamp": "2026-09-18 11:34:10",
            "id": 3,
            "general_data": {
                "db": "sakila",
                "status": 1142,
                "sql_command": "drop_table",
                "query": "DROP TABLE sakila.audit_security_demo_events",
            },
        }

        row = audit_rows("audit_ddl", [event])[0]

        self.assertEqual(row["occurred_at_utc"], "2026-09-18T11:34:10Z")


if __name__ == "__main__":
    unittest.main()
