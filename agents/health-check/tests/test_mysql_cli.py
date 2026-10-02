from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.mysql_cli import MysqlCliConnection, MysqlCliError


class MysqlCliAdapterTests(unittest.TestCase):
    def test_uses_login_path_tls_and_parses_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "login.cnf"
            config.touch()
            completed = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="name\tvalue\na\tNULL\n", stderr=""
            )
            with patch("src.mysql_cli.subprocess.run", return_value=completed) as run:
                cursor = MysqlCliConnection(config, "sakila-admin", 15).cursor()
                cursor.execute("SELECT 1")

            command = run.call_args.args[0]
            environment = run.call_args.kwargs["env"]
            self.assertIn("--login-path=sakila-admin", command)
            self.assertIn("--ssl-mode=REQUIRED", command)
            self.assertEqual(
                environment["MYSQL_TEST_LOGIN_FILE"], str(config.resolve())
            )
            self.assertEqual(cursor.description, [("name",), ("value",)])
            self.assertEqual(cursor.fetchall(), [("a", None)])

    def test_client_error_exposes_errno_without_stderr(self) -> None:
        error = MysqlCliError("ERROR 1045 (28000): Access denied")
        self.assertEqual(error.errno, 1045)
        self.assertNotIn("Access denied", str(error))

    def test_missing_profile_is_rejected_without_reading_it(self) -> None:
        with self.assertRaisesRegex(ValueError, "approved_login_file_not_found"):
            MysqlCliConnection(Path("/private/tmp/no-profile"), "sakila-admin", 15)


if __name__ == "__main__":
    unittest.main()
