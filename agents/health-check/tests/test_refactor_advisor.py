from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from refactor_collector import advisor  # noqa: E402
from refactor_collector.collect_query_tuning_snapshot import (  # noqa: E402
    load_query_catalog,
    sanitize_query_template,
)


class FakeAnalyzer:
    def __init__(self, decision: dict) -> None:
        self.decision = decision
        self.calls = 0

    def analyze(self, payload: dict) -> dict:
        self.calls += 1
        return self.decision


class FakePublisher:
    def __init__(self) -> None:
        self.calls = 0
        self.request: dict | None = None

    def publish(self, request: dict) -> dict:
        self.calls += 1
        self.request = request
        return {
            "accepted": True,
            "status": "validated",
            "refactor": {"status": "recorded", "recorded": True},
        }


def snapshot(latency: float) -> tuple[dict, str, str]:
    original = load_query_catalog()["correlated_running_total"]
    template = sanitize_query_template(original)
    assert template is not None
    fingerprint = "sha256:" + hashlib.sha256(template.encode()).hexdigest()
    identifier = str(uuid.uuid4())
    value = {
        "schema_version": "slow_query_refactor_snapshot.v1",
        "snapshot_id": identifier,
        "collected_at": "2026-09-21T20:00:00.000Z",
        "queries": [
            {
                "query_id": "correlated_running_total",
                "query_fingerprint": fingerprint,
                "known_query": True,
                "query_time_seconds": latency,
                "rows_examined": 500,
                "rows_sent": 10,
            }
        ],
    }
    html = f"<html>{identifier}{' evidence' * 20}</html>"
    return value, html, fingerprint


class RefactorAdvisorTests(unittest.TestCase):
    def test_candidate_is_sent_once_and_persistently_deduplicated(self) -> None:
        report, html, fingerprint = snapshot(86.0)
        analyzer = FakeAnalyzer(
            {
                "decision": "candidate",
                "query_id": "correlated_running_total",
                "query_fingerprint": fingerprint,
                "observed_latency_seconds": 86.0,
                "evidence_quality": "sufficient",
                "summary": "Consulta conhecida acima de 80 segundos.",
            }
        )
        publisher = FakePublisher()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with (
                patch.object(advisor, "STATE_PATH", root / "runtime/state.json"),
                patch.object(advisor, "DECISION_PATH", root / "analysis.json"),
            ):
                first = advisor.run_analysis(
                    report, html, analyzer=analyzer, publisher=publisher
                )
                second = advisor.run_analysis(
                    report, html, analyzer=analyzer, publisher=publisher
                )

        self.assertEqual(first["analysis"]["decision"], "candidate")
        self.assertEqual(second["decision"], "no_candidate")
        self.assertEqual(analyzer.calls, 1)
        self.assertEqual(publisher.calls, 1)
        self.assertIsNotNone(publisher.request)
        self.assertEqual(publisher.request["evidence"]["threshold_seconds"], 80.0)

    def test_exactly_80_seconds_is_not_a_candidate(self) -> None:
        report, html, fingerprint = snapshot(80.0)
        analyzer = FakeAnalyzer(
            {
                "decision": "candidate",
                "query_id": "correlated_running_total",
                "query_fingerprint": fingerprint,
                "observed_latency_seconds": 80.0,
                "evidence_quality": "sufficient",
                "summary": "Não deveria ser chamado.",
            }
        )
        publisher = FakePublisher()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with (
                patch.object(advisor, "STATE_PATH", root / "runtime/state.json"),
                patch.object(advisor, "DECISION_PATH", root / "analysis.json"),
            ):
                result = advisor.run_analysis(
                    report, html, analyzer=analyzer, publisher=publisher
                )

        self.assertEqual(result["decision"], "no_candidate")
        self.assertEqual(analyzer.calls, 0)
        self.assertEqual(publisher.calls, 0)


if __name__ == "__main__":
    unittest.main()
