from __future__ import annotations

import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from dba_audit_security_alerts import (
    DbaAuditSecurityInbox,
    DbaAuditSecurityInboxError,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = (
    ROOT.parents[1]
    / "notification"
    / "fixtures"
    / "audit-destructive-ddl-critical.json"
)


def load_alert() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class DbaAuditSecurityInboxTests(unittest.TestCase):
    def test_records_only_two_sanitized_summaries(self) -> None:
        alert = load_alert()
        with tempfile.TemporaryDirectory() as temporary:
            inbox = DbaAuditSecurityInbox(Path(temporary))
            receipt = inbox.record(alert)
            directory = Path(temporary) / receipt.record_id
            files = sorted(path.name for path in directory.iterdir())
            alert_summary = json.loads(
                (directory / "alert-summary.json").read_text(encoding="utf-8")
            )
            event_summary = json.loads(
                (directory / "audit-event-summary.json").read_text(encoding="utf-8")
            )

        self.assertEqual(receipt.status, "recorded")
        self.assertTrue(receipt.recorded)
        self.assertEqual(files, ["alert-summary.json", "audit-event-summary.json"])
        self.assertEqual(alert_summary["alert_id"], alert["alert_id"])
        self.assertEqual(event_summary["event_key"], alert["metadata"]["event_key"])
        self.assertEqual(event_summary["sql_command"], "drop_table")
        self.assertEqual(event_summary["status_code"], 1142)
        self.assertNotIn("report", alert_summary)
        self.assertNotIn("report", event_summary)
        self.assertNotIn("domains", alert_summary)
        self.assertNotIn("domains", event_summary)
        self.assertNotIn("html", alert_summary)
        self.assertNotIn("html", event_summary)
        serialized = json.dumps(event_summary, ensure_ascii=False)
        for excluded in (
            "actor_id",
            "source_id",
            "session_id",
            "sql_fingerprint",
            "sha256:1111111111111111",
        ):
            self.assertNotIn(excluded, serialized)

    def test_same_event_key_is_idempotent_even_with_new_alert_id(self) -> None:
        first = load_alert()
        second = deepcopy(first)
        second["alert_id"] = "cccccccc-3333-4333-8333-cccccccccccc"
        with tempfile.TemporaryDirectory() as temporary:
            inbox = DbaAuditSecurityInbox(Path(temporary))
            initial = inbox.record(first)
            duplicate = inbox.record(second)
            directories = [path for path in Path(temporary).iterdir() if path.is_dir()]

        self.assertEqual(initial.status, "recorded")
        self.assertEqual(duplicate.status, "duplicate")
        self.assertFalse(duplicate.recorded)
        self.assertEqual(duplicate.record_id, initial.record_id)
        self.assertEqual(len(directories), 1)

    def test_rejects_health_check_contract(self) -> None:
        alert = load_alert()
        alert["contract_version"] = "health_check_alert.v1"
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(
                DbaAuditSecurityInboxError, "dba_audit_contract_invalid"
            ):
                DbaAuditSecurityInbox(Path(temporary)).record(alert)

    def test_rejects_out_of_scope_schema(self) -> None:
        alert = load_alert()
        alert["report"]["json"]["scope"]["functional_schemas"] = ["mysql"]
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(
                DbaAuditSecurityInboxError, "dba_audit_schema_scope_not_allowed"
            ):
                DbaAuditSecurityInbox(Path(temporary)).record(alert)

    def test_rejects_inconsistent_event_key(self) -> None:
        alert = load_alert()
        alert["metadata"]["event_key"] = "sha256:" + "b" * 64
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(
                DbaAuditSecurityInboxError, "dba_audit_event_key_invalid"
            ):
                DbaAuditSecurityInbox(Path(temporary)).record(alert)

    def test_records_schema_change_category(self) -> None:
        alert = load_alert()
        alert["category"] = "schema_change"
        alert["findings"][0]["check_id"] = "audit_security.sakila.blocked_alter_table"
        alert["findings"][0]["metric"] = "blocked_schema_change_attempt"
        alert["findings"][0]["evidence"]["sql_command"] = "alter_table"
        with tempfile.TemporaryDirectory() as temporary:
            receipt = DbaAuditSecurityInbox(Path(temporary)).record(alert)
            summary = json.loads(
                (
                    Path(temporary) / receipt.record_id / "audit-event-summary.json"
                ).read_text(encoding="utf-8")
            )

        self.assertEqual(receipt.status, "recorded")
        self.assertEqual(summary["metric"], "blocked_schema_change_attempt")
        self.assertEqual(summary["sql_command"], "alter_table")

    def test_rejects_unimplemented_audit_category(self) -> None:
        alert = load_alert()
        alert["category"] = "unimplemented_category"
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(
                DbaAuditSecurityInboxError, "dba_audit_category_invalid"
            ):
                DbaAuditSecurityInbox(Path(temporary)).record(alert)


if __name__ == "__main__":
    unittest.main()
