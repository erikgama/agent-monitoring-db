#!/usr/bin/env python3
"""Configure and verify local SMTP without storing or printing a password."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NOTIFICATION = ROOT / "agents" / "notification"
EXAMPLE = NOTIFICATION / ".env.example"
CONFIG = NOTIFICATION / ".notification.local.env"
DISPATCH = ROOT / "apps/lab-console/api/labconsole/notification_dispatch.py"
sys.path.insert(0, str(ROOT / "apps/lab-console/scripts"))
from notification_config import (  # noqa: E402
    NotificationConfigError,
    load_notification_environment,
)

EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class SmtpSetupError(ValueError):
    """Safe failure code without configuration values."""


def initialize() -> dict[str, object]:
    if CONFIG.exists():
        return {"status": "existing_preserved", "path": str(CONFIG)}
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(CONFIG, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return {"status": "existing_preserved", "path": str(CONFIG)}
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(EXAMPLE.read_bytes())
    except Exception:
        CONFIG.unlink(missing_ok=True)
        raise
    return {"status": "created", "path": str(CONFIG)}


def checked_environment() -> dict[str, str]:
    if not CONFIG.is_file():
        raise SmtpSetupError("smtp_config_not_found")
    if CONFIG.stat().st_mode & 0o077:
        raise SmtpSetupError("smtp_config_permissions_insecure")
    try:
        values = load_notification_environment(CONFIG)
    except NotificationConfigError as error:
        raise SmtpSetupError("smtp_config_invalid") from error

    addresses = [
        values["NOTIFICATION_EMAIL_FROM"],
        values["SMTP_USERNAME"],
        *[
            address.strip()
            for key in (
                "NOTIFICATION_EMAIL_RECIPIENTS_WARNING",
                "NOTIFICATION_EMAIL_RECIPIENTS_CRITICAL",
                "NOTIFICATION_EMAIL_RECIPIENTS_REFACTOR",
            )
            for address in values.get(key, "").split(",")
            if address.strip()
        ],
    ]
    if any(
        not EMAIL.fullmatch(item) or item.endswith(".invalid") for item in addresses
    ):
        raise SmtpSetupError("smtp_addresses_invalid_or_placeholder")
    if (
        values["NOTIFICATION_EMAIL_FROM"].casefold()
        != values["SMTP_USERNAME"].casefold()
    ):
        raise SmtpSetupError("smtp_sender_mismatch")
    try:
        port = int(values["SMTP_PORT"])
    except ValueError as error:
        raise SmtpSetupError("smtp_port_invalid") from error
    if not 1 <= port <= 65535:
        raise SmtpSetupError("smtp_port_invalid")
    starttls = values["SMTP_USE_STARTTLS"].casefold()
    if starttls not in {"true", "false", "1", "0", "yes", "no", "on", "off"}:
        raise SmtpSetupError("smtp_starttls_invalid")
    if values["SMTP_HOST"].casefold() == "smtp.gmail.com" and (
        port != 587 or starttls not in {"true", "1", "yes", "on"}
    ):
        raise SmtpSetupError("gmail_requires_587_starttls")

    helper = values.get("NOTIFICATION_SMTP_CREDENTIAL_HELPER")
    if helper:
        path = Path(helper)
        if not path.is_absolute() or not path.is_file() or not os.access(path, os.X_OK):
            raise SmtpSetupError("smtp_credential_helper_unavailable")
    elif sys.platform != "darwin":
        raise SmtpSetupError("smtp_credential_helper_required")
    return values


def check() -> dict[str, object]:
    values = checked_environment()
    return {
        "status": "ready_for_send_test",
        "credential_source": (
            "helper"
            if values.get("NOTIFICATION_SMTP_CREDENTIAL_HELPER")
            else "keychain"
        ),
        "warning_recipient_count": len(
            values["NOTIFICATION_EMAIL_RECIPIENTS_WARNING"].split(",")
        ),
        "critical_recipient_count": len(
            values["NOTIFICATION_EMAIL_RECIPIENTS_CRITICAL"].split(",")
        ),
        "smtp_connected": False,
        "secret_read": False,
    }


def send_test() -> dict[str, object]:
    values = checked_environment()
    interpreter = NOTIFICATION / ".venv/bin/python"
    if not interpreter.is_file():
        raise SmtpSetupError("notification_not_installed")
    environment = dict(os.environ)
    environment.pop("SMTP_PASSWORD", None)
    environment.update(values)
    environment["NOTIFICATION_DELIVERY_ENABLED"] = "true"
    try:
        completed = subprocess.run(
            [str(interpreter), str(DISPATCH), "--send"],
            cwd=NOTIFICATION,
            env=environment,
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise SmtpSetupError("smtp_send_test_timeout") from error
    try:
        result = json.loads(completed.stdout)
    except (ValueError, TypeError) as error:
        raise SmtpSetupError("smtp_send_test_invalid_result") from error
    if (
        completed.returncode
        or result.get("status") != "sent"
        or result.get("delivered") is not True
    ):
        code = result.get("error_code")
        if not isinstance(code, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", code):
            code = "smtp_send_test_failed"
        raise SmtpSetupError(code)
    return {
        "status": "sent",
        "delivered": result.get("delivered") is True,
        "recipient_count": result.get("recipient_count"),
        "inbox_receipt_verified": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="Create the non-secret local template, mode 0600")
    commands.add_parser("check", help="Validate SMTP references without sending")
    real = commands.add_parser("send-test", help="Send one real warning test email")
    real.add_argument("--send", action="store_true", required=True)
    args = parser.parse_args(argv)
    try:
        result = (
            initialize()
            if args.command == "init"
            else check()
            if args.command == "check"
            else send_test()
        )
    except (OSError, SmtpSetupError) as error:
        code = (
            str(error) if isinstance(error, SmtpSetupError) else "smtp_setup_io_error"
        )
        print(json.dumps({"status": "failed", "error_code": code}), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
