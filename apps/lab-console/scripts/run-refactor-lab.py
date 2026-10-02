#!/usr/bin/env python3
"""Executa o worker do Refactor com entrega MCP e aviso do Notification."""

from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path

from notification_config import NotificationConfigError, load_notification_environment

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
REFACTOR_ROOT = REPOSITORY_ROOT / "agents" / "refactor"
NOTIFICATION_CONFIG = (
    REPOSITORY_ROOT / "agents" / "notification" / ".notification.local.env"
)
REFACTOR_ADVISOR = REFACTOR_ROOT / ".venv" / "bin" / "mysql-refactor-advisor"


def environment() -> dict[str, str]:
    selected = dict(os.environ)
    for key in ("SMTP_HOST", "SMTP_PASSWORD", "SMTP_PORT", "SMTP_USERNAME"):
        selected.pop(key, None)
    if NOTIFICATION_CONFIG.is_file():
        selected.update(load_notification_environment(NOTIFICATION_CONFIG))
    selected.update(
        {
            "MCP_NOTIFICATION_ENABLED": os.environ.get(
                "AGENT_MONITORING_NOTIFY", "false"
            ),
            "NOTIFICATION_DELIVERY_ENABLED": os.environ.get(
                "AGENT_MONITORING_NOTIFY", "false"
            ),
            "NOTIFICATION_SMTP_KEYCHAIN_SERVICE": "mysqlconf-notification-smtp",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
        }
    )
    return selected


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    arguments = parser.parse_args()
    if not REFACTOR_ADVISOR.is_file() or not os.access(REFACTOR_ADVISOR, os.X_OK):
        print('{"status":"failed","error_code":"refactor_runtime_missing"}')
        return 2
    if (
        os.environ.get("AGENT_MONITORING_NOTIFY") == "true"
        and not NOTIFICATION_CONFIG.is_file()
    ):
        print('{"status":"failed","error_code":"refactor_preflight_failed"}')
        return 2
    if not arguments.execute:
        print("dry-run: o worker do Refactor e o envio de aviso não foram iniciados")
        return 0
    try:
        selected_environment = environment()
    except NotificationConfigError:
        print('{"status":"failed","error_code":"notification_config_invalid"}')
        return 2
    command = [
        str(REFACTOR_ADVISOR),
        "monitor",
    ]
    return subprocess.run(
        command,
        cwd=REFACTOR_ROOT,
        env=selected_environment,
        check=False,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
