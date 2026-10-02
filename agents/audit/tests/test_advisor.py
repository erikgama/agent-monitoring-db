from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from test_audit_security import FakeClient, decision_for

from audit_security.advisor.agent import (
    ANALYSIS_SCHEMA_PATH,
    INTERVAL_SECONDS,
    MODEL,
    REASONING_EFFORT,
    build_analysis_payload,
    run_agent,
)
from audit_security.alerting import AlertState, PublicationResult
from audit_security.collector import collect_snapshot
from audit_security.renderer import render_html


class FakeAnalyzer:
    def __init__(self, analysis: dict[str, Any]) -> None:
        self.analysis = analysis
        self.calls: list[dict[str, Any]] = []

    def analyze(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(payload)
        return self.analysis


class FakePublisher:
    def __init__(self, result: PublicationResult | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.result = result or PublicationResult(
            published_to_mcp=True,
            accepted=True,
            mcp_status="validated",
            delivery_status="dry_run",
            dba_status="recorded",
        )

    def publish(self, alert: dict[str, Any]) -> PublicationResult:
        self.calls.append(alert)
        return self.result


def no_alert() -> dict[str, Any]:
    return {
        "decision": "no_alert",
        "summary": "No current Audit event satisfies the alert rules.",
        "evidence_quality": "sufficient",
        "alerts": [],
    }


class AuditAdvisorTests(unittest.TestCase):
    def test_structured_output_enums_and_constants_declare_types(self) -> None:
        schema = json.loads(ANALYSIS_SCHEMA_PATH.read_text(encoding="utf-8"))

        def visit(value: Any) -> None:
            if isinstance(value, dict):
                if "enum" in value or "const" in value:
                    self.assertIn("type", value)
                for nested in value.values():
                    visit(nested)
            elif isinstance(value, list):
                for nested in value:
                    visit(nested)

        visit(schema)

    def test_configuration_matches_health_agent_pattern(self) -> None:
        self.assertEqual(MODEL, "gpt-5.6-luna")
        self.assertEqual(REASONING_EFFORT, "low")
        self.assertEqual(INTERVAL_SECONDS, 15.0)

    def test_payload_contains_complete_html_without_duplicating_rules(self) -> None:
        report = collect_snapshot(
            FakeClient(), window_minutes=1, max_events=100, max_rows=50
        )
        html_document = render_html(report)
        payload = build_analysis_payload(
            html_document,
            agent_started_at=datetime(2026, 9, 16, 12, 0, tzinfo=UTC),
        )
        self.assertEqual(payload["report_html"], html_document)
        self.assertNotIn("rules_markdown", payload)
        self.assertIn("agent_started_at", payload)

    def test_no_alert_never_calls_mcp(self) -> None:
        report = collect_snapshot(
            FakeClient(), window_minutes=1, max_events=100, max_rows=50
        )
        analyzer = FakeAnalyzer(no_alert())
        publisher = FakePublisher()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            json_path = root / "latest.json"
            html_path = root / "latest.html"
            json_path.write_text(json.dumps(report), encoding="utf-8")
            html_path.write_text(render_html(report), encoding="utf-8")
            count = run_agent(
                analyzer=analyzer,
                publisher=publisher,
                state=AlertState(root / "state.json"),
                report_json_path=json_path,
                report_html_path=html_path,
                output_directory=root / "advisor",
                max_cycles=1,
                alerting_enabled=True,
                now_fn=lambda: datetime(2026, 9, 16, 12, 0, tzinfo=UTC),
            )
        self.assertEqual(count, 1)
        self.assertEqual(publisher.calls, [])

    def test_agent_alert_calls_mcp_and_records_state(self) -> None:
        report = collect_snapshot(
            FakeClient(), window_minutes=1, max_events=100, max_rows=50
        )
        selected = decision_for(report)
        analysis = {
            "decision": "alert",
            "summary": "One current blocked destructive DDL event was found.",
            "evidence_quality": "sufficient",
            "alerts": [selected],
        }
        analyzer = FakeAnalyzer(analysis)
        publisher = FakePublisher()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            json_path = root / "latest.json"
            html_path = root / "latest.html"
            json_path.write_text(json.dumps(report), encoding="utf-8")
            html_path.write_text(render_html(report), encoding="utf-8")
            state_path = root / "state.json"
            count = run_agent(
                analyzer=analyzer,
                publisher=publisher,
                state=AlertState(state_path),
                report_json_path=json_path,
                report_html_path=html_path,
                output_directory=root / "advisor",
                max_cycles=1,
                alerting_enabled=True,
                environment="test",
                now_fn=lambda: datetime(2026, 9, 16, 12, 0, tzinfo=UTC),
            )
            envelope = json.loads((root / "advisor" / "latest.json").read_text())
            state = json.loads(state_path.read_text())
        self.assertEqual(count, 1)
        self.assertEqual(len(publisher.calls), 1)
        self.assertEqual(envelope["decision_owner"], "audit-luna")
        self.assertEqual(envelope["publications"][0]["status"], "accepted")
        self.assertEqual(len(state["published"]), 1)

    def test_same_collection_is_not_analyzed_twice(self) -> None:
        report = collect_snapshot(
            FakeClient(), window_minutes=1, max_events=100, max_rows=50
        )
        analyzer = FakeAnalyzer(no_alert())
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            json_path = root / "latest.json"
            html_path = root / "latest.html"
            json_path.write_text(json.dumps(report), encoding="utf-8")
            html_path.write_text(render_html(report), encoding="utf-8")
            count = run_agent(
                analyzer=analyzer,
                state=AlertState(root / "state.json"),
                report_json_path=json_path,
                report_html_path=html_path,
                output_directory=root / "advisor",
                interval_seconds=0.001,
                max_cycles=2,
                alerting_enabled=False,
                now_fn=lambda: datetime(2026, 9, 16, 12, 0, tzinfo=UTC),
            )
        self.assertEqual(count, 1)
        self.assertEqual(len(analyzer.calls), 1)

    def test_start_from_current_marks_existing_report_as_baseline(self) -> None:
        report = collect_snapshot(
            FakeClient(), window_minutes=1, max_events=100, max_rows=50
        )
        analyzer = FakeAnalyzer(no_alert())
        events: list[dict[str, Any]] = []
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            json_path = root / "latest.json"
            html_path = root / "latest.html"
            json_path.write_text(json.dumps(report), encoding="utf-8")
            html_path.write_text(render_html(report), encoding="utf-8")
            count = run_agent(
                analyzer=analyzer,
                state=AlertState(root / "state.json"),
                report_json_path=json_path,
                report_html_path=html_path,
                output_directory=root / "advisor",
                max_cycles=1,
                start_from_current=True,
                alerting_enabled=False,
                on_event=events.append,
                now_fn=lambda: datetime(2026, 9, 16, 12, 0, tzinfo=UTC),
            )

        self.assertEqual(count, 0)
        self.assertEqual(analyzer.calls, [])
        self.assertEqual(events[0]["status"], "waiting_for_new_collection")
        self.assertEqual(events[0]["source_audit_id"], report["audit_id"])

    def test_mcp_acceptance_prevents_automatic_delivery_retry(self) -> None:
        report = collect_snapshot(
            FakeClient(), window_minutes=1, max_events=100, max_rows=50
        )
        analysis = {
            "decision": "alert",
            "summary": "One current event satisfies the rule.",
            "evidence_quality": "sufficient",
            "alerts": [decision_for(report)],
        }
        publisher = FakePublisher(
            PublicationResult(
                published_to_mcp=True,
                accepted=True,
                mcp_status="validated",
                delivery_status="failed",
                dba_status="recorded",
                error_code="notification_dispatch_failed",
            )
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            json_path = root / "latest.json"
            html_path = root / "latest.html"
            json_path.write_text(json.dumps(report), encoding="utf-8")
            html_path.write_text(render_html(report), encoding="utf-8")
            state_path = root / "state.json"
            run_agent(
                analyzer=FakeAnalyzer(analysis),
                publisher=publisher,
                state=AlertState(state_path),
                report_json_path=json_path,
                report_html_path=html_path,
                output_directory=root / "advisor",
                max_cycles=1,
                alerting_enabled=True,
                environment="test",
                now_fn=lambda: datetime(2026, 9, 16, 12, 0, tzinfo=UTC),
            )
            state = json.loads(state_path.read_text())
        self.assertEqual(len(state["published"]), 1)


if __name__ == "__main__":
    unittest.main()
