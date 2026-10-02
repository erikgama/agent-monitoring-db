from __future__ import annotations

import unittest
from pathlib import Path

from notification.advisor import NotificationAdvisor
from notification.advisor.agent import read_role

ROOT = Path(__file__).resolve().parents[1]


class NotificationAdvisorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.advisor = NotificationAdvisor()

    def test_warning_routes_to_normal_priority_email(self) -> None:
        decision = self.advisor.decide_alert("warning")

        self.assertTrue(decision.should_send)
        self.assertEqual(decision.channel, "email")
        self.assertFalse(decision.high_priority)

    def test_critical_routes_to_high_priority_email(self) -> None:
        decision = self.advisor.decide_alert("critical")

        self.assertTrue(decision.should_send)
        self.assertEqual(decision.channel, "email")
        self.assertTrue(decision.high_priority)

    def test_info_is_suppressed_without_reclassification(self) -> None:
        decision = self.advisor.decide_alert("info")

        self.assertFalse(decision.should_send)
        self.assertEqual(decision.status, "suppressed_by_policy")

    def test_unknown_severity_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid_severity"):
            self.advisor.decide_alert("urgent")

    def test_portuguese_role_documents_the_safety_boundary(self) -> None:
        role = read_role()

        self.assertIn("Preservar literalmente a severidade", role)
        self.assertIn("WhatsApp", role)
        self.assertIn("não estão implementados", role)

    def test_notification_does_not_have_persistent_advisor_results(self) -> None:
        self.assertFalse((ROOT / "notification" / "advisor" / "results").exists())


if __name__ == "__main__":
    unittest.main()
