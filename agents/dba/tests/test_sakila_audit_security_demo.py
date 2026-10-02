from __future__ import annotations

import contextlib
import importlib.util
import io
import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

DBA_DIR = Path(__file__).resolve().parents[1]
MODULE_PATH = (
    DBA_DIR / "load-tests" / "sakila-audit-security" / "sakila_audit_security_demo.py"
)
SPEC = importlib.util.spec_from_file_location("sakila_audit_security_demo", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
demo = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = demo
SPEC.loader.exec_module(demo)


class AuditSecurityDemoTests(unittest.TestCase):
    def test_plan_targets_only_demo_objects(self) -> None:
        operations = demo.build_operations(str(uuid.uuid4()))
        serialized = "\n".join(item.sql for item in operations)
        self.assertIn("audit_security_demo_events", serialized)
        self.assertIn("audit_security_demo_temp", serialized)
        for protected in ("actor", "film", "customer", "rental", "payment"):
            self.assertNotIn(f"`sakila`.`{protected}`", serialized)
        self.assertEqual(operations[-1].name, "blocked_drop_fixture")
        self.assertEqual(operations[-1].expected, "permission_denied")

    def test_expected_grants_are_accepted(self) -> None:
        demo.validate_grants(
            [
                "GRANT USAGE ON *.* TO `sakila_audit_demo`@`%`",
                "GRANT CREATE TEMPORARY TABLES ON `sakila`.* "
                "TO `sakila_audit_demo`@`%`",
                "GRANT SELECT, INSERT, UPDATE, DELETE ON "
                "`sakila`.`audit_security_demo_events` "
                "TO `sakila_audit_demo`@`%`",
            ]
        )

    def test_drop_privilege_is_rejected(self) -> None:
        grants = [
            "GRANT SELECT, INSERT, UPDATE, DELETE, DROP ON "
            "`sakila`.`audit_security_demo_events` TO `sakila_audit_demo`@`%`",
            "GRANT CREATE TEMPORARY TABLES ON `sakila`.* TO `sakila_audit_demo`@`%`",
        ]
        with self.assertRaisesRegex(RuntimeError, "forbidden_privileges"):
            demo.validate_grants(grants)

    def test_dry_run_never_calls_mysql(self) -> None:
        output = io.StringIO()
        with (
            patch.object(demo, "run_sql", side_effect=AssertionError("mysql called")),
            contextlib.redirect_stdout(output),
        ):
            result = demo.main([])
        self.assertEqual(result, 0)
        self.assertIn("nenhum acesso ao banco", output.getvalue())
        self.assertIn("sakila-audit-demo", output.getvalue())

    def test_error_code_is_sanitized_to_number(self) -> None:
        self.assertEqual(
            demo.mysql_errno(
                "ERROR 1142 (42000): DROP command denied to user secret detail"
            ),
            1142,
        )
        self.assertIsNone(demo.mysql_errno("unstructured failure"))

    def test_mysql_command_uses_only_dedicated_login_path(self) -> None:
        command = demo.mysql_command("SELECT 1;")
        self.assertIn("--login-path=sakila-audit-demo", command)
        self.assertNotIn("--login-path=sakila-admin", command)
        self.assertNotIn("--login-path=admin", command)


if __name__ == "__main__":
    unittest.main()
