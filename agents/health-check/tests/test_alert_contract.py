from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.alert_contract import AlertContractError, validate_alert

EXAMPLES = ROOT / "alerts" / "examples"


def load_example(name: str = "select-latency-p99-critical.json") -> dict:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


class AlertContractTests(unittest.TestCase):
    def test_all_examples_are_valid(self) -> None:
        for path in sorted(EXAMPLES.glob("*.json")):
            with self.subTest(path=path.name):
                validate_alert(json.loads(path.read_text(encoding="utf-8")))

    def test_invalid_severity_fails(self) -> None:
        alert = load_example()
        alert["severity"] = "urgent"
        with self.assertRaisesRegex(AlertContractError, "invalid_severity"):
            validate_alert(alert)

    def test_invalid_category_fails(self) -> None:
        alert = load_example()
        alert["category"] = "security"
        with self.assertRaisesRegex(AlertContractError, "invalid_category"):
            validate_alert(alert)

    def test_missing_report_artifact_fails(self) -> None:
        for field in ("json", "html"):
            alert = load_example()
            del alert["report"][field]
            with (
                self.subTest(field=field),
                self.assertRaisesRegex(AlertContractError, "report_missing_fields"),
            ):
                validate_alert(alert)

    def test_inconsistent_audit_id_fails(self) -> None:
        alert = load_example()
        alert["report"]["json"]["audit_id"] = "44444444-4444-4444-8444-444444444444"
        with self.assertRaisesRegex(
            AlertContractError, "report_json_audit_id_mismatch"
        ):
            validate_alert(alert)

    def test_missing_dedupe_key_fails(self) -> None:
        alert = load_example()
        del alert["dedupe_key"]
        with self.assertRaisesRegex(
            AlertContractError, "alert_missing_fields:dedupe_key"
        ):
            validate_alert(alert)

    def test_sensitive_field_fails(self) -> None:
        alert = load_example()
        alert["metadata"]["password"] = "example-only"
        with self.assertRaisesRegex(AlertContractError, "sensitive_field"):
            validate_alert(alert)

    def test_private_host_value_fails(self) -> None:
        alert = load_example()
        alert["metadata"]["observed_address"] = "10.20.30.40"
        with self.assertRaisesRegex(AlertContractError, "sensitive_value"):
            validate_alert(alert)

    def test_html_from_another_collection_fails(self) -> None:
        alert = load_example()
        alert["report"]["html"] = alert["report"]["html"].replace(
            alert["audit_id"], "55555555-5555-4555-8555-555555555555"
        )
        with self.assertRaisesRegex(
            AlertContractError, "report_html_audit_id_mismatch"
        ):
            validate_alert(alert)

    def test_detection_before_collection_fails(self) -> None:
        alert = load_example()
        alert["detected_at"] = "2026-01-15T13:00:00.000Z"
        with self.assertRaisesRegex(AlertContractError, "detected_before_collection"):
            validate_alert(alert)

    def test_schema_declares_closed_v1_values(self) -> None:
        schema = json.loads(
            (ROOT / "contracts" / "health_check_alert.v1.schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(schema["$id"], "health_check_alert.v1")
        self.assertEqual(
            set(schema["properties"]["severity"]["enum"]),
            {"info", "warning", "critical"},
        )
        self.assertEqual(
            set(schema["properties"]["category"]["enum"]),
            {
                "deadlock",
                "lock_wait",
                "query_latency",
                "connections",
                "innodb",
                "replication",
                "database_error",
            },
        )

    def test_example_describes_the_only_current_emitter(self) -> None:
        alert = load_example()
        self.assertEqual(alert["category"], "query_latency")
        self.assertEqual(alert["severity"], "critical")
        self.assertEqual(
            alert["findings"][0]["check_id"],
            "health-check-luna.select-latency.p99-gt-2s",
        )


if __name__ == "__main__":
    unittest.main()
