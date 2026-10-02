"""MySQL CLI adapter backed only by the approved encrypted login-path."""

from __future__ import annotations

import csv
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from agent_monitoring.config import database_settings

from .sql_safety import validate_read_only_sql

_ERROR_CODE = re.compile(r"\bERROR\s+(\d+)\b", re.IGNORECASE)


class MysqlCliError(RuntimeError):
    """Sanitized failure; raw stderr is deliberately discarded."""

    def __init__(self, stderr: str) -> None:
        match = _ERROR_CODE.search(stderr)
        self.errno = int(match.group(1)) if match else None
        super().__init__("mysql_client_error")


class MysqlCli:
    def __init__(self, config: Path, login_path: str, timeout_seconds: int) -> None:
        if not config.is_file():
            raise ValueError("approved_login_file_not_found")
        if not login_path:
            raise ValueError("login_path_required")
        if not 1 <= timeout_seconds <= 120:
            raise ValueError("query_timeout_seconds_out_of_range")
        self._binary = "mysql"
        self._tls_flags = ["--ssl-mode=REQUIRED"]
        self._config = config.resolve()
        self._login_path = login_path
        self._timeout_seconds = timeout_seconds

    @classmethod
    def from_environment(cls, timeout_seconds: int = 30) -> MysqlCli:
        settings = database_settings("audit")
        client = cls(settings.login_file, settings.login_path, timeout_seconds)
        client._binary = settings.mysql_binary
        client._tls_flags = settings.tls_flags()
        return client

    def query(self, sql: str) -> list[dict[str, Any]]:
        validate_read_only_sql(sql)
        environment = os.environ.copy()
        environment.pop("MYSQL_PWD", None)
        environment["MYSQL_TEST_LOGIN_FILE"] = str(self._config)
        try:
            completed = subprocess.run(
                [
                    self._binary,
                    f"--login-path={self._login_path}",
                    *self._tls_flags,
                    "--batch",
                    "--raw",
                    f"--execute={sql}",
                ],
                text=True,
                capture_output=True,
                timeout=self._timeout_seconds + 15,
                check=False,
                env=environment,
            )
        except subprocess.TimeoutExpired as error:
            raise TimeoutError("mysql_client_timeout") from error
        if completed.returncode != 0:
            raise MysqlCliError(completed.stderr)
        parsed = list(csv.reader(completed.stdout.splitlines(), delimiter="\t"))
        if not parsed:
            return []
        headers = parsed[0]
        rows: list[dict[str, Any]] = []
        for raw in parsed[1:]:
            if len(raw) != len(headers):
                raise RuntimeError("mysql_result_shape_invalid")
            rows.append(
                {
                    key: None if value == "NULL" else value
                    for key, value in zip(headers, raw, strict=True)
                }
            )
        return rows
