from __future__ import annotations

import re
import stat
import unittest
from pathlib import Path

AUDIT_ROOT = Path(__file__).resolve().parents[1]
IMPORT_ROOT = (
    AUDIT_ROOT
    / "archive"
    / "reference"
    / "security-agent-import"
    / "outputs"
    / "mysql_security_monitor"
)
QUERY_ROOT = IMPORT_ROOT / "queries"


def statements(sql: str) -> list[str]:
    without_block_comments = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
    without_line_comments = re.sub(r"--[^\n]*", "", without_block_comments)
    return [item.strip() for item in without_line_comments.split(";") if item.strip()]


class ImportedSecuritySourceSafetyTests(unittest.TestCase):
    def test_reference_collector_is_not_executable(self) -> None:
        mode = (IMPORT_ROOT / "collect_security_snapshot.py").stat().st_mode
        self.assertEqual(mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH), 0)

    def test_risky_and_generated_files_were_not_imported(self) -> None:
        forbidden = {
            "demo-accounts.local.json",
            "provision_demo_accounts.py",
            "run_controlled_security_exercise.py",
            "simulate_security_scenarios.py",
        }
        imported_names = {
            path.name for path in IMPORT_ROOT.rglob("*") if path.is_file()
        }
        self.assertTrue(forbidden.isdisjoint(imported_names))
        self.assertFalse(
            any(path.name == "__pycache__" for path in IMPORT_ROOT.rglob("*"))
        )

    def test_imported_query_entrypoints_are_read_only(self) -> None:
        query_files = sorted(QUERY_ROOT.glob("*.sql"))
        self.assertEqual(len(query_files), 10)
        for path in query_files:
            with self.subTest(path=path.name):
                parsed = statements(path.read_text(encoding="utf-8"))
                self.assertTrue(parsed)
                for statement in parsed:
                    first_word = statement.split(None, 1)[0].upper()
                    self.assertIn(first_word, {"SELECT", "WITH"})
                normalized = " ".join(parsed).upper()
                self.assertNotIn("INTO OUTFILE", normalized)
                self.assertNotIn("INTO DUMPFILE", normalized)
                self.assertNotIn("LOAD_FILE(", normalized)
                self.assertNotIn("AUDIT_LOG_FILTER_SET_FILTER", normalized)
                self.assertNotIn("AUDIT_LOG_FILTER_SET_USER", normalized)

    def test_sakila_data_access_query_is_explicitly_scoped(self) -> None:
        sql = (QUERY_ROOT / "90_audit_sakila_data_access.sql").read_text(
            encoding="utf-8"
        )
        self.assertIn("object_schema = 'sakila'", sql)


if __name__ == "__main__":
    unittest.main()
