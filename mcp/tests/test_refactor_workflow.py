from __future__ import annotations

import hashlib
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from mysqlconf_mcp.delivery import NotificationDelivery
from mysqlconf_mcp.delivery.refactor_workflow import _root
from mysqlconf_mcp.tools.refactor_workflow import (
    process_refactor_result_raise,
    refactor_request_raise,
    refactor_result_raise,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def request_payload() -> dict:
    original = "SELECT customer_id, SUM(amount) FROM payment GROUP BY customer_id"
    snapshot_id = str(uuid.uuid4())
    fingerprint = "sha256:" + "a" * 64
    return {
        "contract_version": "query_refactor_request.v1",
        "request_id": str(uuid.uuid4()),
        "created_at": "2026-09-21T20:00:00.000Z",
        "source": "health-check",
        "query_id": "correlated_running_total",
        "query_fingerprint": fingerprint,
        "dedupe_key": f"sakila:correlated_running_total:{fingerprint}",
        "schema": "sakila",
        "catalog_source": (
            "agents/health-check/refactor_collector/bad_queries_with_llm_refactor.sql"
        ),
        "original_sql": original,
        "original_sql_sha256": hashlib.sha256(original.encode()).hexdigest(),
        "evidence": {
            "threshold_seconds": 80.0,
            "max_query_time_seconds": 86.0,
            "occurrences": 3,
            "qualifying_occurrences": 3,
            "rows_examined": 100,
            "rows_sent": 10,
        },
        "report": {
            "snapshot_id": snapshot_id,
            "collected_at": "2026-09-21T20:00:00.000Z",
            "json": {"snapshot_id": snapshot_id},
            "html": f"<html>{snapshot_id}{' evidence' * 20}</html>",
        },
    }


def result_payload(request: dict) -> dict:
    original = request["original_sql"]
    proposed = "SELECT customer_id, SUM(amount) total FROM payment GROUP BY customer_id"
    return {
        "contract_version": "query_refactor_result.v1",
        "result_id": str(uuid.uuid4()),
        "request_id": request["request_id"],
        "completed_at": "2026-09-21T20:05:00.000Z",
        "source": "refactor",
        "destination": "dba",
        "query_id": request["query_id"],
        "query_fingerprint": request["query_fingerprint"],
        "status": "approved_lab",
        "validation_schema": "sakila_dev",
        "original_sql": original,
        "original_sql_sha256": hashlib.sha256(original.encode()).hexdigest(),
        "proposed_sql": proposed,
        "proposed_sql_sha256": hashlib.sha256(proposed.encode()).hexdigest(),
        "change_summary": "Agregação equivalente validada no laboratório.",
        "timings": {
            "before_seconds": 86.0,
            "after_seconds": 1.0,
            "saved_seconds": 85.0,
            "improvement_percent": 98.837,
            "speedup": 86.0,
        },
        "validation": {
            "equivalent": True,
            "original_exit_code": 0,
            "proposed_exit_code": 0,
            "original_rows": 10,
            "proposed_rows": 10,
            "original_result_sha256": "b" * 64,
            "proposed_result_sha256": "b" * 64,
        },
        "artifacts": {
            "original_path": "query_refactor/advisor/results/demo/original.sql",
            "proposal_path": "query_refactor/advisor/results/demo/proposed.sql",
            "report_path": "query_refactor/advisor/results/demo/report.md",
        },
        "limitations": ["Somente sakila_dev."],
    }


class RefactorWorkflowTests(unittest.TestCase):
    def test_default_inboxes_are_resolved_from_repository_root(self) -> None:
        self.assertEqual(_root(), REPOSITORY_ROOT)

    def test_request_is_recorded_once_by_fingerprint(self) -> None:
        request = request_payload()
        with tempfile.TemporaryDirectory() as temporary:
            with patch.dict("os.environ", {"MCP_REFACTOR_REQUESTS_DIR": temporary}):
                first = refactor_request_raise(request)
                second = refactor_request_raise(request)

            self.assertTrue(first["accepted"])
            self.assertEqual(first["refactor"]["status"], "recorded")
            self.assertEqual(second["refactor"]["status"], "duplicate")
            records = list(Path(temporary).glob("*/request.json"))
            self.assertEqual(len(records), 1)

    def test_result_is_recorded_for_dba(self) -> None:
        request = request_payload()
        result = result_payload(request)
        with tempfile.TemporaryDirectory() as temporary:
            with patch.dict("os.environ", {"MCP_DBA_REFACTOR_RESULTS_DIR": temporary}):
                response = refactor_result_raise(result)

            self.assertTrue(response["accepted"])
            self.assertEqual(response["dba"]["status"], "recorded")
            self.assertEqual(response["notification"]["status"], "not_configured")
            self.assertEqual(len(list(Path(temporary).glob("*/result.json"))), 1)

    def test_recorded_result_sends_one_completion_notice(self) -> None:
        request = request_payload()
        result = result_payload(request)
        calls: list[str] = []

        class Gateway:
            def deliver(self, payload: dict) -> NotificationDelivery:
                calls.append(payload["result_id"])
                return NotificationDelivery(
                    target="notification",
                    status="sent",
                    delivered=True,
                    channel="email",
                )

        def factory(_environ):
            return Gateway()

        with tempfile.TemporaryDirectory() as temporary:
            with patch.dict(
                "os.environ",
                {
                    "MCP_DBA_REFACTOR_RESULTS_DIR": temporary,
                    "MCP_NOTIFICATION_ENABLED": "true",
                },
            ):
                first = process_refactor_result_raise(
                    result,
                    notification_factory=factory,
                )
                second = process_refactor_result_raise(
                    result,
                    notification_factory=factory,
                )

        self.assertEqual(first["dba"]["status"], "recorded")
        self.assertEqual(first["notification"]["status"], "sent")
        self.assertEqual(second["dba"]["status"], "duplicate")
        self.assertEqual(second["notification"]["status"], "duplicate")
        self.assertEqual(calls, [result["result_id"]])

    def test_real_notification_adapter_accepts_refactor_result_in_dry_run(self) -> None:
        request = request_payload()
        result = result_payload(request)
        with tempfile.TemporaryDirectory() as temporary:
            with patch.dict(
                "os.environ",
                {
                    "MCP_DBA_REFACTOR_RESULTS_DIR": temporary,
                    "MCP_NOTIFICATION_ENABLED": "true",
                    "NOTIFICATION_DELIVERY_ENABLED": "false",
                    "NOTIFICATION_EMAIL_RECIPIENTS_WARNING": ("dba@example.invalid"),
                },
            ):
                response = refactor_result_raise(result)

        self.assertTrue(response["accepted"])
        self.assertEqual(response["dba"]["status"], "recorded")
        self.assertEqual(response["notification"]["status"], "dry_run")
        self.assertFalse(response["notification"]["delivered"])

    def test_request_with_wrong_hash_is_rejected(self) -> None:
        request = request_payload()
        request["original_sql_sha256"] = "0" * 64

        response = refactor_request_raise(request)

        self.assertFalse(response["accepted"])
        self.assertEqual(response["status"], "rejected")

    def test_request_with_inconsistent_dedupe_key_is_rejected(self) -> None:
        request = request_payload()
        request["dedupe_key"] = "sakila:correlated_running_total:sha256:" + "f" * 64

        response = refactor_request_raise(request)

        self.assertFalse(response["accepted"])
        self.assertEqual(response["status"], "rejected")


if __name__ == "__main__":
    unittest.main()
