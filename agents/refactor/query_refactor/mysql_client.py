#!/usr/bin/env python3
"""Abre o cliente MySQL autorizado do Refactor sem depender de shell."""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPOSITORY_ROOT))

from agent_monitoring.config import database_settings  # noqa: E402

DEFAULT_DATABASE = "sakila_dev"
ALLOWED_DATABASES = {"sakila", "sakila_dev"}


def build_mysql_command(
    environment: Mapping[str, str],
    arguments: Sequence[str],
    *,
    mysql_binary: str,
) -> tuple[list[str], dict[str, str]]:
    settings = database_settings("refactor", environment)
    selected_environment = settings.environment(environment)
    login_path = settings.login_path
    database = selected_environment.setdefault("MYSQL_DATABASE", DEFAULT_DATABASE)
    if database not in ALLOWED_DATABASES:
        allowed = ", ".join(sorted(ALLOWED_DATABASES))
        raise ValueError(f"MYSQL_DATABASE deve ser um destes schemas: {allowed}")

    command = [
        mysql_binary,
        f"--login-path={login_path}",
        "--protocol=TCP",
        *settings.tls_flags(),
        f"--database={database}",
        *arguments,
    ]
    return command, selected_environment


def main() -> int:
    settings = database_settings("refactor")
    if not settings.login_file.is_file():
        print("approved_login_file_not_found", file=sys.stderr)
        return 2
    mysql_binary = shutil.which(settings.mysql_binary)
    if mysql_binary is None:
        print("cliente mysql não encontrado no PATH", file=sys.stderr)
        return 2
    try:
        command, environment = build_mysql_command(
            os.environ,
            sys.argv[1:],
            mysql_binary=mysql_binary,
        )
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2
    os.execvpe(mysql_binary, command, environment)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
