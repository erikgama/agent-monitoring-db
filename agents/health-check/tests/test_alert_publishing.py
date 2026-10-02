from __future__ import annotations

import json
import sys
import tempfile
import unittest
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.alerting import (  # noqa: E402
    AlertStateStore,
    CooldownPolicy,
    NoOpAlertPublisher,
    PublicationResult,
    publish_validated_alerts,
)

EXAMPLE = json.loads(
    (ROOT / "alerts" / "examples" / "select-latency-p99-critical.json").read_text(
        encoding="utf-8"
    )
)
EVALUATED_AT = datetime(2026, 1, 15, 13, 10, tzinfo=UTC)


class AlertPublishingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.state = AlertStateStore(Path(self.temporary.name) / "state.json")

    def test_default_critical_cooldown_is_two_minutes(self) -> None:
        self.assertEqual(CooldownPolicy().critical_seconds, 120)

    def test_invalid_alert_never_reaches_publisher(self) -> None:
        alert = deepcopy(EXAMPLE)
        alert["severity"] = "urgent"
        publisher = NoOpAlertPublisher()

        result = publish_validated_alerts(
            [alert], publisher, self.state, evaluated_at=EVALUATED_AT
        )

        self.assertEqual(result["status"], "invalid_alert")
        self.assertEqual(publisher.calls, [])

    def test_p99_alert_is_suppressed_during_critical_cooldown(self) -> None:
        publisher = NoOpAlertPublisher()

        first = publish_validated_alerts(
            [deepcopy(EXAMPLE)], publisher, self.state, evaluated_at=EVALUATED_AT
        )
        second = publish_validated_alerts(
            [deepcopy(EXAMPLE)],
            publisher,
            self.state,
            evaluated_at=EVALUATED_AT + timedelta(seconds=30),
        )

        self.assertEqual(first["published_count"], 1)
        self.assertEqual(second["suppressed_count"], 1)
        self.assertEqual(len(publisher.calls), 1)

    def test_rejected_p99_alert_does_not_enter_state(self) -> None:
        publisher = NoOpAlertPublisher(
            result=PublicationResult(
                published_to_mcp=False,
                accepted=False,
                mcp_status="rejected",
                error_code="mcp_rejected_alert",
            )
        )

        result = publish_validated_alerts(
            [deepcopy(EXAMPLE)], publisher, self.state, evaluated_at=EVALUATED_AT
        )

        self.assertEqual(result["published_count"], 0)
        self.assertIsNone(self.state.get(EXAMPLE["dedupe_key"]))


if __name__ == "__main__":
    unittest.main()
