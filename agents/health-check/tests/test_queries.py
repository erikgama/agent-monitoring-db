from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from general_report.collectors import GENERAL_REPORT_SQL_FILES
from src.db import ALLOWED_SQL, DatabaseRunner, UnsafeSqlError, parse_sql_file


class UnusedConnection:
    pass


def sql_path(filename: str) -> Path:
    if filename == "120_slow_query_refactor.sql":
        return ROOT / "refactor_collector" / "sql" / filename
    directory = ROOT / "select_latency" / "sql"
    if filename != "110_select_latency.sql":
        directory = ROOT / "general_report" / "sql"
    return directory / filename


class QuerySafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = DatabaseRunner(
            UnusedConnection(), ROOT / "general_report" / "sql", 15
        )

    def test_all_files_and_blocks_match_allowlist_and_are_read_only(self) -> None:
        self.runner.validate_all(GENERAL_REPORT_SQL_FILES)
        DatabaseRunner(
            UnusedConnection(), ROOT / "select_latency" / "sql", 15
        ).validate_all(("110_select_latency.sql",))
        DatabaseRunner(
            UnusedConnection(), ROOT / "refactor_collector" / "sql", 15
        ).validate_all(("120_slow_query_refactor.sql",))

    def test_every_expected_sql_file_exists(self) -> None:
        self.assertEqual(
            set(ALLOWED_SQL),
            {path.name for path in (ROOT / "general_report" / "sql").glob("*.sql")}
            | {path.name for path in (ROOT / "select_latency" / "sql").glob("*.sql")}
            | {
                path.name
                for path in (ROOT / "refactor_collector" / "sql").glob("*.sql")
            },
        )

    def test_no_sensitive_statement_sources_are_selected(self) -> None:
        combined = "\n".join(
            sql.lower()
            for filename in ALLOWED_SQL
            for sql in parse_sql_file(sql_path(filename)).values()
        )
        self.assertNotIn("query_sample_text", combined)
        self.assertNotIn("current_statement", combined)
        self.assertNotIn("last_statement", combined)
        self.assertNotIn("sakila_dev", combined)

    def test_arbitrary_file_is_rejected(self) -> None:
        with self.assertRaises(UnsafeSqlError):
            self.runner.execute_file("arbitrary.sql", {})

    def test_numeric_parameters_are_bounded(self) -> None:
        with self.assertRaises(UnsafeSqlError):
            self.runner.execute_file(
                "20_connections.sql", {"connections_limit": 0}, ["top_users"]
            )


if __name__ == "__main__":
    unittest.main()
