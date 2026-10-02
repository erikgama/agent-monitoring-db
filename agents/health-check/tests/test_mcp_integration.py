from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = ROOT.parents[1]
sys.path.insert(0, str(ROOT))

from advisor.agent import build_agent_alert  # noqa: E402
from src.alerting import McpIncidentPublisher  # noqa: E402


@unittest.skipUnless(
    os.environ.get("HEALTHCHECK_RUN_MCP_INTEGRATION") == "1",
    "set HEALTHCHECK_RUN_MCP_INTEGRATION=1 for the real stdio dry-run",
)
class McpIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)

    def publisher(self) -> McpIncidentPublisher:
        return McpIncidentPublisher(
            repository_root=REPOSITORY_ROOT,
            server_environment={
                "MCP_NOTIFICATION_ENABLED": "true",
                "MCP_DBA_ENABLED": "true",
                "MCP_DBA_ALERTS_DIR": self.temporary.name,
                "NOTIFICATION_DELIVERY_ENABLED": "false",
                "NOTIFICATION_EMAIL_RECIPIENTS_WARNING": (
                    "warning-operator@example.invalid"
                ),
                "NOTIFICATION_EMAIL_RECIPIENTS_CRITICAL": (
                    "critical-operator@example.invalid"
                ),
            },
        )

    def assert_dry_run(self, result) -> None:
        self.assertTrue(result.published_to_mcp)
        self.assertTrue(result.accepted)
        self.assertEqual(result.mcp_status, "validated")
        self.assertEqual(result.delivery_status, "dry_run")
        self.assertEqual(
            result.delivery,
            {
                "target": "notification",
                "channel": "email",
                "status": "dry_run",
                "delivered": False,
            },
        )
        self.assertEqual(result.dba_status, "recorded")
        self.assertIsNotNone(result.dba)
        self.assertTrue(result.dba["recorded"])
        record_directory = Path(self.temporary.name) / result.dba["directory"]
        self.assertTrue(record_directory.is_dir())
        self.assertEqual(
            sorted(path.name for path in record_directory.iterdir()),
            ["alert.json", "latest.html", "latest.json"],
        )
        self.assertEqual(
            result.dba["alert_file"],
            f"{result.dba['directory']}/alert.json",
        )
        self.assertEqual(
            result.dba["report_json_file"],
            f"{result.dba['directory']}/latest.json",
        )
        self.assertEqual(
            result.dba["report_html_file"],
            f"{result.dba['directory']}/latest.html",
        )

    def test_health_check_to_notification_dry_run_over_real_mcp(self) -> None:
        alert = json.loads(
            (
                ROOT / "alerts" / "examples" / "select-latency-p99-critical.json"
            ).read_text(encoding="utf-8")
        )
        self.assert_dry_run(self.publisher().publish(alert))

    def test_luna_alert_decision_reaches_notification_dry_run(self) -> None:
        example = json.loads(
            (
                ROOT / "alerts" / "examples" / "select-latency-p99-critical.json"
            ).read_text(encoding="utf-8")
        )
        general_report = deepcopy(example["report"]["json"])
        general_report["schema_version"] = "1.0"
        general_report["audit_id"] = "33333333-3333-4333-8333-333333333333"
        general_report_html = (
            "<!doctype html><html><body>"
            f"<p>Audit ID: {general_report['audit_id']}</p>"
            f"<p>Collected at: {general_report['collected_at']}</p>"
            "<h1>General Health Report</h1>"
            "</body></html>"
        )
        alert = build_agent_alert(
            example["report"]["json"],
            example["report"]["html"],
            general_report,
            general_report_html,
            {
                "decision": "alert",
                "summary": "Luna identified P99 above 2 seconds in the HTML report.",
                "metric": "p99_seconds",
                "observed_value_seconds": 2.5,
                "threshold_seconds": 2.0,
                "evidence_quality": "sufficient",
                "safety_notes": ["Observation only."],
            },
            "integration-test",
        )

        self.assertIsNotNone(alert)
        self.assertEqual(alert["report"]["json"], general_report)
        self.assertEqual(alert["report"]["html"], general_report_html)
        self.assert_dry_run(self.publisher().publish(alert))


if __name__ == "__main__":
    unittest.main()
