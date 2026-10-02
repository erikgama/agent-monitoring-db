from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from general_report.cache import replace_latest_snapshot
from general_report.main import build_health_snapshot, read_latest_snapshot
from general_report.renderer import render_snapshot_html


class FakeCursor:
    def __init__(self) -> None:
        self.description = []
        self._rows = []

    def execute(self, sql: str) -> None:
        lowered = sql.lower()
        if "version() as mysql_version" in lowered:
            self.description = [
                ("mysql_version",),
                ("version_comment",),
                ("performance_schema_enabled",),
            ]
            self._rows = [("8.4.0", "MySQL HeatWave", 1)]
        elif (
            "from information_schema.tables" in lowered
            and "events_statements_summary_by_digest" in lowered
        ):
            self.description = [("table_schema",), ("table_name",)]
            self._rows = [
                ("performance_schema", "events_statements_summary_by_digest"),
                ("performance_schema", "events_errors_summary_global_by_error"),
                ("performance_schema", "data_locks"),
                ("performance_schema", "data_lock_waits"),
                ("performance_schema", "threads"),
                ("performance_schema", "global_variables"),
                ("performance_schema", "global_status"),
                ("performance_schema", "replication_connection_status"),
                ("performance_schema", "replication_applier_status"),
                ("performance_schema", "replication_group_members"),
                ("information_schema", "INNODB_METRICS"),
                ("sys", "user_summary"),
                ("sys", "host_summary"),
                ("sys", "processlist"),
                ("sys", "schema_table_lock_waits"),
                ("sys", "schema_unused_indexes"),
                ("sys", "schema_redundant_indexes"),
            ]
        elif "from performance_schema.setup_consumers" in lowered:
            self.description = [("NAME",), ("ENABLED",)]
            self._rows = [("statements_digest", "YES")]
        elif "from information_schema.columns" in lowered:
            self.description = [("COLUMN_NAME",)]
            self._rows = [("QUANTILE_95",), ("COUNT_SECONDARY",)]
        elif "from performance_schema.global_variables" in lowered:
            self.description = [("VARIABLE_NAME",), ("VARIABLE_VALUE",)]
            self._rows = [
                ("max_connections", "100"),
                ("performance_schema", "ON"),
                ("innodb_buffer_pool_size", "1048576"),
                ("innodb_redo_log_capacity", "1048576"),
            ]
        elif "from performance_schema.global_status" in lowered:
            self.description = [("VARIABLE_NAME",), ("VARIABLE_VALUE",)]
            self._rows = [
                ("Uptime", "1000"),
                ("Threads_connected", "2"),
                ("Threads_running", "1"),
                ("Connections", "100"),
                ("Aborted_connects", "0"),
                ("Innodb_buffer_pool_read_requests", "10000"),
                ("Innodb_buffer_pool_reads", "1"),
                ("Innodb_log_waits", "0"),
            ]
        else:
            self.description = []
            self._rows = []

    def fetchall(self):
        return self._rows

    def close(self) -> None:
        pass


class FakeConnection:
    ssl_active = True
    parallel_safe = True

    def __init__(self) -> None:
        self.closed = False

    def cursor(self) -> FakeCursor:
        return FakeCursor()

    def close(self) -> None:
        self.closed = True


class NullLogger:
    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass


class CacheAndOrchestratorTests(unittest.TestCase):
    def test_renderer_uses_each_snapshot_instead_of_fixed_report_values(self) -> None:
        report = {
            "audit_id": "audit-first",
            "collected_at": "2026-09-18T10:00:00.000Z",
            "duration_ms": 1250,
            "overall_status": "healthy",
            "target": {"mysql_version": "8.4.0"},
            "scope": {"schemas": ["sakila"]},
            "capabilities": {"performance_schema": "available"},
            "instance": {
                "uptime_seconds": 3600,
                "config": {"max_connections": 100},
            },
            "domains": {
                "active_sessions": {
                    "status": "healthy",
                    "metrics": {
                        "active_count": 0,
                        "longest_seconds": 0,
                        "sessions": [],
                    },
                    "sources": {},
                    "findings": [],
                }
            },
            "findings": [],
            "data_retention": {"mode": "latest_only"},
        }
        first = render_snapshot_html(report)

        replacement = deepcopy(report)
        replacement["audit_id"] = "audit-second"
        replacement["collected_at"] = "2026-09-18T10:05:00.000Z"
        replacement["overall_status"] = "critical"
        replacement["domains"]["active_sessions"]["status"] = "critical"
        replacement["domains"]["active_sessions"]["metrics"]["active_count"] = 7
        replacement["findings"] = [
            {
                "id": "sessions.dynamic",
                "severity": "critical",
                "domain": "active_sessions",
                "title": "Achado produzido pela nova coleta",
                "evidence": {"count": 7},
            }
        ]
        second = render_snapshot_html(replacement)

        self.assertIn("audit-first", first)
        self.assertNotIn("audit-first", second)
        self.assertIn("audit-second", second)
        self.assertIn("Achado produzido pela nova coleta", second)
        self.assertIn(">7<", second)
        self.assertNotEqual(first, second)

    def test_complete_collection_and_cache_only_read(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            connection = FakeConnection()
            with (
                patch.dict(
                    os.environ, {"HEALTHCHECK_CACHE_DIR": temporary}, clear=False
                ),
                patch("general_report.main._logger", return_value=NullLogger()),
            ):
                report = build_health_snapshot(connection)
                cached = read_latest_snapshot()
                latest = json.loads((Path(temporary) / "report.json").read_text())
                page = (Path(temporary) / "report.html").read_text()

            self.assertTrue(connection.closed)
            self.assertEqual(report["audit_id"], cached["audit_id"])
            self.assertEqual(report["audit_id"], latest["audit_id"])
            self.assertFalse((Path(temporary) / "summary.json").exists())
            self.assertIn(report["audit_id"], page)
            self.assertIn(report["collected_at"], page)
            self.assertNotIn("<script", page.lower())
            self.assertNotIn("http://", page.lower())
            self.assertNotIn("https://", page.lower())
            self.assertEqual(report["scope"]["schemas"], ["sakila"])
            self.assertEqual(report["instance"]["scope"]["kind"], "instance")
            self.assertEqual(
                {
                    name: domain["scope"]["kind"]
                    for name, domain in report["domains"].items()
                },
                {
                    "connections": "instance",
                    "workload": "schema",
                    "active_sessions": "schema",
                    "locks": "mixed",
                    "innodb": "instance",
                    "schema_tables": "schema",
                    "indexes": "schema",
                    "errors": "instance",
                    "replication": "instance",
                },
            )
            self.assertEqual(
                report["scope"]["domain_scopes"],
                {
                    "schema": [
                        "workload",
                        "active_sessions",
                        "schema_tables",
                        "indexes",
                    ],
                    "mixed": ["locks"],
                    "instance": [
                        "connections",
                        "innodb",
                        "errors",
                        "replication",
                    ],
                },
            )
            self.assertIn(
                "O status geral combina escopos diferentes",
                page,
            )
            self.assertTrue(
                report["data_retention"]["raw_query_text_persisted"] is False
            )

    def test_configuration_error_closes_injected_connection(self) -> None:
        connection = FakeConnection()
        with (
            patch(
                "general_report.main._load_json",
                side_effect=ValueError("invalid_config"),
            ),
            patch("general_report.main._logger", return_value=NullLogger()),
            self.assertRaisesRegex(ValueError, "invalid_config"),
        ):
            build_health_snapshot(connection)
        self.assertTrue(connection.closed)

    def test_failed_publication_restores_previous_cache_set(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            connection = FakeConnection()
            with (
                patch.dict(
                    os.environ, {"HEALTHCHECK_CACHE_DIR": temporary}, clear=False
                ),
                patch("general_report.main._logger", return_value=NullLogger()),
            ):
                report = build_health_snapshot(connection)
            directory = Path(temporary)
            paths = [directory / name for name in ("report.json", "report.html")]
            previous = {path: path.read_bytes() for path in paths}
            replacement = deepcopy(report)
            replacement["audit_id"] = "replacement-audit-id"
            replacement["started_at"] = "2026-09-12T20:00:00.000Z"
            replacement["finished_at"] = "2026-09-12T20:00:01.000Z"
            replacement["collected_at"] = "2026-09-12T20:00:01.000Z"

            real_replace = os.replace
            calls = 0

            def fail_second_replace(source, destination):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("simulated_publish_failure")
                return real_replace(source, destination)

            with patch(
                "general_report.cache.os.replace", side_effect=fail_second_replace
            ):
                with self.assertRaises(OSError):
                    replace_latest_snapshot(
                        directory, replacement, render_snapshot_html(replacement)
                    )

            self.assertEqual(previous, {path: path.read_bytes() for path in paths})


if __name__ == "__main__":
    unittest.main()
