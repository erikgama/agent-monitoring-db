from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

DBA_DIR = Path(__file__).resolve().parents[1]
PACKAGE_SRC = DBA_DIR / "health-check-alerts" / "src"
FIXTURE = DBA_DIR / "health-check-alerts" / "fixtures" / "query-latency-critical.json"
sys.path.insert(0, str(PACKAGE_SRC))

from dba_health_check_alerts import DbaAlertInbox, DbaAlertInboxError


def load_alert() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class DbaHealthCheckAlertInboxTests(unittest.TestCase):
    def test_records_alert_and_both_reports_in_one_directory(self) -> None:
        alert = load_alert()
        with tempfile.TemporaryDirectory() as temporary:
            receipt = DbaAlertInbox(Path(temporary)).record(alert)
            record = json.loads(
                (Path(temporary) / receipt.alert_file).read_text(encoding="utf-8")
            )
            report_json = json.loads(
                (Path(temporary) / receipt.report_json_file).read_text(encoding="utf-8")
            )
            report_html = (Path(temporary) / receipt.report_html_file).read_text(
                encoding="utf-8"
            )
            files = sorted(
                path.name for path in (Path(temporary) / receipt.directory).iterdir()
            )

        self.assertEqual(receipt.status, "recorded")
        self.assertTrue(receipt.recorded)
        self.assertEqual(record["record_version"], "dba_health_check_incident.v2")
        self.assertEqual(record["alert"]["alert_id"], alert["alert_id"])
        self.assertEqual(record["alert"]["audit_id"], alert["audit_id"])
        self.assertEqual(
            record["alert"]["report"],
            {
                "html_file": "latest.html",
                "json_file": "latest.json",
                "report_format_version": alert["report"]["report_format_version"],
                "report_generated_at": alert["report"]["report_generated_at"],
            },
        )
        self.assertNotIn(alert["report"]["html"], json.dumps(record))
        self.assertEqual(report_json, alert["report"]["json"])
        self.assertEqual(report_html, alert["report"]["html"])
        self.assertEqual(files, ["alert.json", "latest.html", "latest.json"])
        self.assertIn("__query_latency__", receipt.directory)
        self.assertTrue(receipt.directory.endswith(alert["alert_id"]))

    def test_same_alert_is_idempotent(self) -> None:
        alert = load_alert()
        with tempfile.TemporaryDirectory() as temporary:
            inbox = DbaAlertInbox(Path(temporary))
            first = inbox.record(alert)
            second = inbox.record(alert)
            entries = sorted(Path(temporary).iterdir())
            files = sorted((Path(temporary) / first.directory).iterdir())

        self.assertEqual(first.status, "recorded")
        self.assertEqual(second.status, "duplicate")
        self.assertFalse(second.recorded)
        self.assertEqual(len(entries), 1)
        self.assertEqual(len(files), 3)

    def test_incomplete_existing_directory_is_rejected(self) -> None:
        alert = load_alert()
        detected = (
            datetime.fromisoformat(alert["detected_at"].replace("Z", "+00:00"))
            .astimezone(UTC)
            .strftime("%Y-%m-%dT%H-%M-%S.%fZ")
        )
        record_id = f"{detected}__{alert['category']}__{alert['alert_id']}"
        with tempfile.TemporaryDirectory() as temporary:
            record_directory = Path(temporary) / record_id
            record_directory.mkdir()
            (record_directory / "alert.json").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(
                DbaAlertInboxError,
                "dba_record_incomplete",
            ):
                DbaAlertInbox(Path(temporary)).record(alert)

    def test_rejects_unvalidated_contract_identity(self) -> None:
        cases = (
            ("dba_contract_version_invalid", {"contract_version": "other.v1"}),
            (
                "dba_contract_version_invalid",
                {"contract_version": "audit_security_alert.v1"},
            ),
            ("dba_alert_source_invalid", {"source": "other-agent"}),
        )
        for code, change in cases:
            with self.subTest(code=code), tempfile.TemporaryDirectory() as temporary:
                alert = load_alert()
                alert.update(change)
                with self.assertRaisesRegex(DbaAlertInboxError, code):
                    DbaAlertInbox(Path(temporary)).record(alert)

    def test_rejects_report_from_another_audit(self) -> None:
        alert = load_alert()
        alert["report"]["json"]["audit_id"] = "44444444-4444-4444-8444-444444444444"
        with (
            tempfile.TemporaryDirectory() as temporary,
            self.assertRaisesRegex(
                DbaAlertInboxError,
                "dba_audit_id_mismatch",
            ),
        ):
            DbaAlertInbox(Path(temporary)).record(alert)

    def test_rejects_schema_outside_sakila(self) -> None:
        alert = copy.deepcopy(load_alert())
        alert["report"]["json"]["scope"]["schemas"] = ["sakila_dev"]
        with (
            tempfile.TemporaryDirectory() as temporary,
            self.assertRaisesRegex(
                DbaAlertInboxError,
                "dba_schema_scope_not_allowed",
            ),
        ):
            DbaAlertInbox(Path(temporary)).record(alert)


if __name__ == "__main__":
    unittest.main()
