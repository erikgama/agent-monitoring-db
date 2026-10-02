from __future__ import annotations

import json
import sys
import tempfile
import unittest
import uuid
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from advisor.agent import (  # noqa: E402
    ANALYSIS_SCHEMA_PATH,
    MODEL,
    REASONING_EFFORT,
    _codex_command,
    _validate_analysis,
    build_agent_alert,
    build_analysis_payload,
    run_agent,
)
from select_latency.collector import (  # noqa: E402
    TARGET_DIGEST,
    render_select_latency_html,
)
from src.alerting import NoOpAlertPublisher  # noqa: E402


def report(audit_id: str | None = None, *, p99_seconds: float = 1.9) -> dict:
    return {
        "schema_version": "select_latency_window.v9",
        "audit_id": audit_id or str(uuid.uuid4()),
        "started_at": "2026-09-15T14:59:30.000Z",
        "finished_at": "2026-09-15T15:00:00.000Z",
        "collected_at": "2026-09-15T15:00:00.000Z",
        "duration_ms": 30000,
        "status": "available",
        "overall_status": "unknown",
        "window_started_at": "2026-09-15T14:59:30.000Z",
        "window_ended_at": "2026-09-15T15:00:00.000Z",
        "window_target_seconds": 30,
        "configured_window_seconds": 30,
        "window_elapsed_seconds": 30,
        "window_full": True,
        "window_phase": "rolling",
        "refresh_interval_seconds": 7,
        "select_digest_count": 1,
        "activity_session": {"status": "active"},
        "target": {"engine": "MySQL", "service": "MySQL HeatWave"},
        "scope": {"schemas": ["sakila"], "target_digest": TARGET_DIGEST},
        "capabilities": {"statement_digests": "available"},
        "instance": {},
        "domains": {
            "select_latency": {
                "status": "unknown",
                "metrics": {"decision_owner": "health-check-luna"},
                "findings": [],
            }
        },
        "findings": [],
        "data_retention": {
            "mode": "latest_only",
            "raw_query_text_persisted": False,
            "raw_logs_persisted": False,
        },
        "select_digests": [
            {
                "schema_name": "sakila",
                "digest": TARGET_DIGEST,
                "digest_text": "SELECT normalized actor popularity",
                "executions_in_window": 10,
                "total_latency_seconds": 0.01,
                "avg_latency_seconds": 0.001,
                "p95_latency_upper_bound_seconds": p99_seconds,
                "p99_latency_upper_bound_seconds": p99_seconds,
                "percentile_sample_count": 10,
                "percentile_sample_complete": True,
                "percentile_sample_coverage_pct": 100.0,
                "rows_examined_in_window": 100,
                "rows_sent_in_window": 10,
                "avg_lock_time_seconds": 0.0,
            }
        ],
    }


def decision(kind: str = "no_alert", *, observed: float | None = 1.9) -> dict:
    return {
        "decision": kind,
        "summary": "Luna evaluated the P99 evidence in the HTML report.",
        "metric": "p99_seconds",
        "observed_value_seconds": observed,
        "threshold_seconds": 2.0,
        "evidence_quality": "sufficient" if observed is not None else "unavailable",
        "safety_notes": ["Observation only."],
    }


def general_report() -> tuple[dict, str]:
    value = report()
    value["schema_version"] = "1.0"
    value["overall_status"] = "healthy"
    html = (
        "<!doctype html><html><body>"
        f"<p>Audit ID: {value['audit_id']}</p>"
        f"<p>Collected at: {value['collected_at']}</p>"
        "<h1>General Health Report</h1>"
        "</body></html>"
    )
    return value, html


class FakeAnalyzer:
    def __init__(self, result: dict) -> None:
        self.result = result
        self.calls: list[dict] = []

    def analyze(self, payload: dict) -> dict:
        self.calls.append(payload)
        return self.result


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.value += seconds


class HealthCheckLunaAgentTests(unittest.TestCase):
    def test_structured_output_enums_and_constants_declare_types(self) -> None:
        schema = json.loads(ANALYSIS_SCHEMA_PATH.read_text(encoding="utf-8"))

        def visit(value: object) -> None:
            if isinstance(value, dict):
                if "enum" in value or "const" in value:
                    self.assertIn("type", value)
                for nested in value.values():
                    visit(nested)
            elif isinstance(value, list):
                for nested in value:
                    visit(nested)

        visit(schema)

    def test_codex_command_uses_luna_read_only_and_ephemeral(self) -> None:
        command = _codex_command(Path("/tmp/example-output.json"))
        self.assertEqual(MODEL, "gpt-5.6-luna")
        self.assertEqual(REASONING_EFFORT, "low")
        self.assertIn("gpt-5.6-luna", command)
        self.assertIn('model_reasoning_effort="low"', command)
        self.assertIn("read-only", command)
        self.assertIn("--ephemeral", command)
        self.assertNotIn("danger-full-access", command)

    def test_agent_output_is_schema_validated_at_two_seconds(self) -> None:
        _validate_analysis(decision())
        invalid = decision()
        invalid["threshold_seconds"] = 0.002
        with self.assertRaisesRegex(RuntimeError, "luna_output_schema_invalid"):
            _validate_analysis(invalid)

    def test_agent_output_respects_strict_two_second_boundary(self) -> None:
        for observed in (1.9, 2.0):
            _validate_analysis(decision("no_alert", observed=observed))
        for observed in (2.1, 15.2):
            _validate_analysis(decision("alert", observed=observed))

        with self.assertRaisesRegex(
            RuntimeError,
            "luna_decision_inconsistent_with_rule",
        ):
            _validate_analysis(decision("alert", observed=2.0))
        with self.assertRaisesRegex(
            RuntimeError,
            "luna_decision_inconsistent_with_rule",
        ):
            _validate_analysis(decision("no_alert", observed=2.1))

    def test_payload_contains_complete_html_without_duplicating_rules(self) -> None:
        value = report()
        html = render_select_latency_html(value)
        payload = build_analysis_payload(html)

        self.assertEqual(payload["report_html"], html)
        self.assertNotIn("rules_markdown", payload)
        self.assertNotIn("report_json", payload)

    def test_agent_no_alert_does_not_call_mcp(self) -> None:
        analyzer = FakeAnalyzer(decision())
        publisher = NoOpAlertPublisher()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            value = report()
            html_path = directory / "latest.html"
            json_path = directory / "latest.json"
            html_path.write_text(render_select_latency_html(value), encoding="utf-8")
            json_path.write_text(json.dumps(value), encoding="utf-8")
            output = directory / "advisor"
            events: list[dict] = []

            count = run_agent(
                analyzer=analyzer,
                html_path=html_path,
                report_json_path=json_path,
                output_directory=output,
                publisher=publisher,
                alert_state_path=output / "runtime" / "state.json",
                environment="test",
                max_cycles=1,
                analyze_existing=True,
                on_event=events.append,
                now_fn=lambda: datetime(2026, 9, 15, 15, 1, tzinfo=UTC),
            )
            persisted = json.loads((output / "latest.json").read_text(encoding="utf-8"))

        self.assertEqual(count, 1)
        self.assertEqual(len(analyzer.calls), 1)
        self.assertEqual(publisher.calls, [])
        self.assertEqual(events[0]["agent_decision"], "no_alert")
        self.assertEqual(persisted["decision_owner"], "health-check-luna")
        self.assertEqual(
            persisted["source_document"],
            "select_latency/results/latest.html",
        )

    def test_agent_alert_calls_mcp_publisher_and_attaches_report(self) -> None:
        analyzer = FakeAnalyzer(decision("alert", observed=2.1))
        publisher = NoOpAlertPublisher()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            value = report(p99_seconds=2.1)
            html = render_select_latency_html(value)
            html_path = directory / "latest.html"
            json_path = directory / "latest.json"
            html_path.write_text(html, encoding="utf-8")
            json_path.write_text(json.dumps(value), encoding="utf-8")
            dba_report, dba_html = general_report()
            general_json_path = directory / "report.json"
            general_html_path = directory / "report.html"
            general_json_path.write_text(json.dumps(dba_report), encoding="utf-8")
            general_html_path.write_text(dba_html, encoding="utf-8")
            events: list[dict] = []
            general_collection_calls: list[str] = []

            count = run_agent(
                analyzer=analyzer,
                html_path=html_path,
                report_json_path=json_path,
                general_report_html_path=general_html_path,
                general_report_json_path=general_json_path,
                general_report_collector=lambda: general_collection_calls.append(
                    "collected"
                ),
                output_directory=directory / "advisor",
                publisher=publisher,
                alert_state_path=directory / "advisor" / "runtime" / "state.json",
                environment="production",
                max_cycles=1,
                analyze_existing=True,
                on_event=events.append,
                now_fn=lambda: datetime(2026, 9, 15, 15, 1, tzinfo=UTC),
            )

        self.assertEqual(count, 1)
        self.assertEqual(general_collection_calls, ["collected"])
        self.assertEqual(len(publisher.calls), 1)
        alert = publisher.calls[0]
        self.assertEqual(alert["findings"][0]["threshold"], 2.0)
        self.assertEqual(alert["findings"][0]["observed_value"], 2.1)
        self.assertEqual(alert["report"]["html"], dba_html)
        self.assertEqual(
            alert["report"]["json"]["audit_id"],
            dba_report["audit_id"],
        )
        self.assertEqual(alert["audit_id"], dba_report["audit_id"])
        self.assertEqual(
            alert["metadata"]["decision_source_audit_id"],
            value["audit_id"],
        )
        self.assertEqual(events[0]["published_count"], 1)
        outcomes = events[0]["publication_outcomes"]
        self.assertEqual(outcomes[0]["alert_id"], alert["alert_id"])
        self.assertTrue(outcomes[0]["accepted"])
        self.assertEqual(outcomes[0]["delivery_status"], "dry_run")

    def test_alert_decision_is_persisted_when_publication_is_disabled(self) -> None:
        analyzer = FakeAnalyzer(decision("alert", observed=2.1))
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            value = report(p99_seconds=2.1)
            html_path = directory / "latest.html"
            json_path = directory / "latest.json"
            html_path.write_text(
                render_select_latency_html(value),
                encoding="utf-8",
            )
            json_path.write_text(json.dumps(value), encoding="utf-8")
            output = directory / "advisor"

            count = run_agent(
                analyzer=analyzer,
                html_path=html_path,
                report_json_path=json_path,
                output_directory=output,
                max_cycles=1,
                analyze_existing=True,
            )
            persisted = json.loads((output / "latest.json").read_text(encoding="utf-8"))

        self.assertEqual(count, 1)
        self.assertEqual(persisted["decision"]["decision"], "alert")
        self.assertEqual(persisted["publication"]["status"], "disabled")

    def test_general_report_failure_prevents_mcp_publication(self) -> None:
        analyzer = FakeAnalyzer(decision("alert", observed=2.1))
        publisher = NoOpAlertPublisher()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            value = report(p99_seconds=2.1)
            html_path = directory / "latest.html"
            json_path = directory / "latest.json"
            html_path.write_text(
                render_select_latency_html(value),
                encoding="utf-8",
            )
            json_path.write_text(json.dumps(value), encoding="utf-8")
            events: list[dict] = []

            count = run_agent(
                analyzer=analyzer,
                html_path=html_path,
                report_json_path=json_path,
                output_directory=directory / "advisor",
                publisher=publisher,
                alert_state_path=directory / "advisor" / "runtime" / "state.json",
                environment="production",
                general_report_collector=lambda: (_ for _ in ()).throw(
                    OSError("database unavailable")
                ),
                max_cycles=1,
                analyze_existing=True,
                on_event=events.append,
            )

        self.assertEqual(count, 0)
        self.assertEqual(publisher.calls, [])
        self.assertEqual(
            events,
            [
                {
                    "status": "analysis_failed",
                    "error_code": "general_report_collection_failed",
                }
            ],
        )

    def test_same_html_audit_is_not_analyzed_twice(self) -> None:
        analyzer = FakeAnalyzer(decision())
        clock = FakeClock()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            value = report()
            html_path = directory / "latest.html"
            json_path = directory / "latest.json"
            html_path.write_text(render_select_latency_html(value), encoding="utf-8")
            json_path.write_text(json.dumps(value), encoding="utf-8")
            events: list[dict] = []
            count = run_agent(
                analyzer=analyzer,
                html_path=html_path,
                report_json_path=json_path,
                output_directory=directory / "advisor",
                max_cycles=2,
                analyze_existing=True,
                sleep_fn=clock.sleep,
                monotonic_fn=clock.monotonic,
                on_event=events.append,
            )

        self.assertEqual(count, 1)
        self.assertEqual(clock.sleeps, [15.0])
        self.assertEqual(
            [event["status"] for event in events],
            ["analyzed", "waiting_for_new_collection"],
        )

    def test_agent_waits_for_a_new_html_on_start_by_default(self) -> None:
        analyzer = FakeAnalyzer(decision())
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            value = report()
            html_path = directory / "latest.html"
            json_path = directory / "latest.json"
            html_path.write_text(
                render_select_latency_html(value),
                encoding="utf-8",
            )
            json_path.write_text(json.dumps(value), encoding="utf-8")
            events: list[dict] = []

            count = run_agent(
                analyzer=analyzer,
                html_path=html_path,
                report_json_path=json_path,
                output_directory=directory / "advisor",
                max_cycles=1,
                on_event=events.append,
            )

        self.assertEqual(count, 0)
        self.assertEqual(analyzer.calls, [])
        self.assertEqual(events[0]["status"], "waiting_for_new_collection")

    def test_alert_contract_is_built_only_from_agent_alert_decision(self) -> None:
        value = report(p99_seconds=2.1)
        html = render_select_latency_html(value)
        dba_report, dba_html = general_report()
        self.assertIsNone(
            build_agent_alert(
                value,
                html,
                dba_report,
                dba_html,
                decision(),
                "test",
            )
        )
        alert = build_agent_alert(
            value,
            html,
            dba_report,
            dba_html,
            decision("alert", observed=2.1),
            "test",
            detected_at=datetime(2026, 9, 15, 15, 1, tzinfo=UTC),
        )
        self.assertIsNotNone(alert)
        self.assertEqual(
            alert["dedupe_key"],
            "sakila:actor_popularity:query_latency:p99_gt_2s",
        )
        self.assertEqual(alert["report"]["json"], dba_report)
        self.assertEqual(alert["report"]["html"], dba_html)


if __name__ == "__main__":
    unittest.main()
