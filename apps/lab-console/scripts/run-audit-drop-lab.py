#!/usr/bin/env python3
"""Executa diretamente a tentativa controlada de DROP da demonstração."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT))

from agent_monitoring.config import database_settings  # noqa: E402

DATABASE_SETTINGS = database_settings("audit-lab")
LOGIN_FILE = DATABASE_SETTINGS.login_file
MYSQL_COMMAND = (
    DATABASE_SETTINGS.mysql_binary,
    f"--login-path={DATABASE_SETTINGS.login_path}",
    *DATABASE_SETTINGS.tls_flags(),
    "--database=sakila",
    "--connect-timeout=15",
    "--execute=DROP TABLE `sakila`.`audit_security_demo_events`;",
)
DENIED_ERRORS = ("ERROR 1044", "ERROR 1142", "ERROR 1227")
ATTEMPT_TIMEOUT_SECONDS = 300


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    arguments = parser.parse_args()

    if not arguments.execute:
        print(f"dry-run: {MYSQL_COMMAND[-1].removeprefix('--execute=')}")
        return 0

    environment = DATABASE_SETTINGS.environment()
    environment["MYSQL_TEST_LOGIN_FILE"] = str(LOGIN_FILE)
    try:
        result = subprocess.run(
            MYSQL_COMMAND,
            env=environment,
            text=True,
            capture_output=True,
            timeout=ATTEMPT_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        print("attempt_timeout")
        return 1

    if result.returncode != 0 and any(
        error in result.stderr for error in DENIED_ERRORS
    ):
        print("expected_permission_denied")
        return 0
    if result.returncode == 0:
        print("unsafe_unexpected_success")
        return 1
    print("unexpected_failure")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
