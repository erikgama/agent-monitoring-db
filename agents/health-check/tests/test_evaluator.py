from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from general_report.evaluator import (
    evaluate_active_sessions,
    evaluate_connections,
    overall_status,
)
from src.models import QueryResult, SourceStatus


class EvaluatorTests(unittest.TestCase):
    def test_connection_thresholds(self) -> None:
        domain = evaluate_connections(
            {"top_users": QueryResult(SourceStatus.AVAILABLE)},
            {"config": {"max_connections": 100}},
            {"threads_connected": 90, "connections": 100, "aborted_connects": 0},
            {
                "attention_usage_pct": 70,
                "critical_usage_pct": 85,
                "attention_aborted_connect_pct": 5,
            },
        )
        self.assertEqual(domain["status"], "critical")
        self.assertEqual(domain["metrics"]["connection_usage_pct"], 90.0)

    def test_connection_users_are_masked_in_persisted_evidence(self) -> None:
        domain = evaluate_connections(
            {
                "top_users": QueryResult(
                    SourceStatus.AVAILABLE,
                    rows=[{"user": "admin@database.example", "statements": "4"}],
                ),
                "top_hosts": QueryResult(
                    SourceStatus.AVAILABLE,
                    rows=[{"host": "192.0.2.10", "statements": "4"}],
                ),
            },
            {"config": {"max_connections": 100}},
            {"threads_connected": 1, "connections": 4, "aborted_connects": 0},
            {
                "attention_usage_pct": 70,
                "critical_usage_pct": 85,
                "attention_aborted_connect_pct": 5,
            },
        )
        self.assertEqual(domain["metrics"]["top_users"][0]["user"], "masked")
        self.assertEqual(domain["metrics"]["top_hosts"][0]["host"], "masked")

    def test_active_session_identities_are_masked_in_persisted_evidence(self) -> None:
        domain = evaluate_active_sessions(
            {
                "sys_processlist": QueryResult(
                    SourceStatus.AVAILABLE,
                    rows=[
                        {
                            "user": "admin@database.example",
                            "host": "192.0.2.10",
                            "running_seconds": "3",
                        }
                    ],
                )
            },
            {
                "attention_long_running_seconds": 30,
                "critical_long_running_seconds": 60,
            },
        )
        session = domain["metrics"]["sessions"][0]
        self.assertEqual(session["user"], "masked")
        self.assertEqual(session["host"], "masked")

    def test_overall_status_precedence(self) -> None:
        domains = {
            "connections": {"status": "healthy"},
            "workload": {"status": "attention"},
            "locks": {"status": "critical"},
        }
        self.assertEqual(overall_status(domains), "critical")

    def test_insufficient_core_evidence_is_unknown(self) -> None:
        domains = {
            "connections": {"status": "not_available"},
            "workload": {"status": "unknown"},
            "replication": {"status": "not_available"},
        }
        self.assertEqual(overall_status(domains), "unknown")


if __name__ == "__main__":
    unittest.main()
