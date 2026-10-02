from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from refactor_collector.collect_query_tuning_snapshot import (  # noqa: E402
    POLICY_PATH,
    build_snapshot,
    load_query_catalog,
    publish_snapshot,
    render_html,
    sanitize_query_template,
)
from src.models import QueryResult, SourceStatus  # noqa: E402


def policy() -> dict:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


class RefactorCollectorTests(unittest.TestCase):
    def test_catalog_contains_only_versioned_original_queries(self) -> None:
        catalog = load_query_catalog()

        self.assertEqual(
            set(catalog),
            {
                "correlated_running_total",
                "daily_customer_pair_ranking",
                "daily_distinct_customer_pairs",
            },
        )
        self.assertTrue(all(isinstance(sql, str) and sql for sql in catalog.values()))

    def test_sanitize_query_template_removes_comments_and_literals(self) -> None:
        raw = """
            SELECT customer_id, email
            FROM sakila.customer
            WHERE email = 'secret@example.com'
              AND customer_id = 42
              AND token = 0xABC123 /* private token */;
        """

        sanitized = sanitize_query_template(raw)

        self.assertIsNotNone(sanitized)
        self.assertNotIn("secret@example.com", sanitized)
        self.assertNotIn("42", sanitized)
        self.assertNotIn("ABC123", sanitized)
        self.assertNotIn("private token", sanitized)
        self.assertIn("email = ?", sanitized)
        self.assertIn("customer_id = ?", sanitized)

    def test_snapshot_contains_only_sanitized_sakila_selects(self) -> None:
        settings = QueryResult(
            status=SourceStatus.AVAILABLE,
            rows=[
                {
                    "slow_query_log_enabled": "YES",
                    "log_output": "FILE,TABLE",
                    "long_query_time_seconds": "2.000000",
                }
            ],
        )
        queries = QueryResult(
            status=SourceStatus.AVAILABLE,
            rows=[
                {
                    "started_at_mysql": "2026-09-21 12:00:00.000000",
                    "query_time_seconds": "3.750000",
                    "lock_time_seconds": "0.010000",
                    "rows_sent": "1",
                    "rows_examined": "200",
                    "schema_name": "sakila",
                    "sql_text": (
                        "SELECT actor_id FROM sakila.actor "
                        "WHERE first_name = 'PRIVATE_NAME' AND actor_id = 99"
                    ),
                },
                {
                    "schema_name": "other_schema",
                    "sql_text": "SELECT secret FROM private_table",
                },
            ],
        )

        snapshot = build_snapshot(
            settings,
            queries,
            policy=policy(),
            collected_at="2026-09-21T15:00:00.000Z",
            snapshot_id="snapshot-test",
        )
        serialized = json.dumps(snapshot)

        self.assertEqual(snapshot["status"], "attention")
        self.assertEqual(snapshot["collection"]["returned_rows"], 1)
        self.assertEqual(snapshot["scope"]["schemas"], ["sakila"])
        self.assertNotIn("PRIVATE_NAME", serialized)
        self.assertNotIn("actor_id = 99", serialized)
        self.assertNotIn("private_table", serialized)
        self.assertFalse(snapshot["retention"]["raw_query_text_persisted"])

    def test_unavailable_slow_log_is_explicit(self) -> None:
        snapshot = build_snapshot(
            QueryResult(
                status=SourceStatus.AVAILABLE,
                rows=[
                    {
                        "slow_query_log_enabled": "NO",
                        "log_output": "FILE",
                        "long_query_time_seconds": "10",
                    }
                ],
            ),
            QueryResult(
                status=SourceStatus.NOT_AVAILABLE,
                reason="permission_denied",
            ),
            policy=policy(),
            collected_at="2026-09-21T15:00:00.000Z",
            snapshot_id="snapshot-unavailable",
        )

        self.assertEqual(snapshot["status"], "not_available")
        self.assertFalse(snapshot["data_quality"]["complete"])
        self.assertIn(
            "slow_query_log_disabled", snapshot["data_quality"]["limitations"]
        )
        self.assertIn("permission_denied", snapshot["data_quality"]["limitations"])

    def test_publish_snapshot_writes_latest_pair(self) -> None:
        snapshot = build_snapshot(
            QueryResult(
                status=SourceStatus.AVAILABLE,
                rows=[
                    {
                        "slow_query_log_enabled": "YES",
                        "log_output": "TABLE",
                        "long_query_time_seconds": "2",
                    }
                ],
            ),
            QueryResult(status=SourceStatus.AVAILABLE, rows=[]),
            policy=policy(),
            collected_at="2026-09-21T15:00:00.000Z",
            snapshot_id="snapshot-files",
        )

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            publish_snapshot(output, snapshot, render_html(snapshot))

            stored = json.loads((output / "latest.json").read_text(encoding="utf-8"))
            html_document = (output / "latest.html").read_text(encoding="utf-8")

        self.assertEqual(stored["snapshot_id"], "snapshot-files")
        self.assertIn("snapshot-files", html_document)
        self.assertIn("2026-09-21T15:00:00.000Z", html_document)


if __name__ == "__main__":
    unittest.main()
