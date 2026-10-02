"""Minimal DB-API-shaped adapter backed by the approved MySQL login-path."""

from __future__ import annotations

import csv
import os
import re
import subprocess
from pathlib import Path

from agent_monitoring.config import database_settings

_ERROR_CODE = re.compile(r"\bERROR\s+(\d+)\b", re.I)


class MysqlCliError(RuntimeError):
    """Sanitized client failure carrying a MySQL errno when available."""

    def __init__(self, stderr: str) -> None:
        match = _ERROR_CODE.search(stderr)
        self.errno = int(match.group(1)) if match else None
        super().__init__(self.errno, "mysql_client_error")


class MysqlCliCursor:
    def __init__(self, connection: MysqlCliConnection) -> None:
        self._connection = connection
        self.description: list[tuple[str]] = []
        self._rows: list[tuple[str | None, ...]] = []

    def execute(self, sql: str) -> None:
        completed = self._connection._run(sql)
        if completed.returncode != 0:
            raise MysqlCliError(completed.stderr)
        parsed = list(csv.reader(completed.stdout.splitlines(), delimiter="\t"))
        if not parsed:
            self.description = []
            self._rows = []
            return
        self.description = [(column,) for column in parsed[0]]
        self._rows = [
            tuple(None if value == "NULL" else value for value in row)
            for row in parsed[1:]
        ]

    def fetchall(self) -> list[tuple[str | None, ...]]:
        return self._rows

    def close(self) -> None:
        pass


class MysqlCliConnection:
    """Connection facade sufficient for ``DatabaseRunner``.

    A client process is used for each allowed statement. This keeps credentials
    out of Python while retaining the approved login-path and TLS policy.
    """

    ssl_active = True
    parallel_safe = True

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
        self._closed = False

    @classmethod
    def from_environment(cls, timeout_seconds: int) -> MysqlCliConnection:
        settings = database_settings("health-check")
        client = cls(settings.login_file, settings.login_path, timeout_seconds)
        client._binary = settings.mysql_binary
        client._tls_flags = settings.tls_flags()
        return client

    def cursor(self) -> MysqlCliCursor:
        if self._closed:
            raise RuntimeError("connection_closed")
        return MysqlCliCursor(self)

    def close(self) -> None:
        self._closed = True

    def _run(self, sql: str) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment.pop("MYSQL_PWD", None)
        environment["MYSQL_TEST_LOGIN_FILE"] = str(self._config)
        try:
            return subprocess.run(
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
