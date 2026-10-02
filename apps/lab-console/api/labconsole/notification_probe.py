"""Adapter to the existing Notification manual test; no SMTP implementation here."""

import argparse
import importlib.util
import os
import subprocess
import sys
from pathlib import Path


def run(repository: Path, execute: bool) -> int:
    environment = dict(os.environ)
    if execute:
        # Reuse the official non-secret configuration loader. SMTP password is
        # still resolved exclusively by Notification in macOS Keychain.
        path = (
            repository / "apps" / "lab-console" / "scripts" / "notification_config.py"
        )
        spec = importlib.util.spec_from_file_location("notification_config", path)
        if spec is None or spec.loader is None:
            raise ValueError("official_loader_unavailable")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        config = repository / "agents" / "notification" / ".notification.local.env"
        environment.update(module.load_notification_environment(config))
    environment["NOTIFICATION_DELIVERY_ENABLED"] = "true" if execute else "false"
    environment["NOTIFICATION_SMTP_KEYCHAIN_SERVICE"] = "mysqlconf-notification-smtp"
    environment.pop("SMTP_PASSWORD", None)
    root = repository / "agents/notification"
    command = [
        str(root / ".venv/bin/mysql-notification-email-test"),
        "fixtures/connection-warning.json",
    ]
    if execute:
        # The manual dry-run CLI does not resolve Keychain. Real delivery must
        # use Notification's public runtime assembly, in its own interpreter.
        command = [
            str(root / ".venv/bin/python"),
            str(Path(__file__).with_name("notification_dispatch.py")),
            "--send",
        ]
    return subprocess.run(command, cwd=root, env=environment, check=False).returncode


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    try:
        code = run(args.repository, args.execute)
    except Exception:
        print(
            '{"status":"failed","error_code":"notification_preflight_failed"}',
            flush=True,
        )
        code = 2
    raise SystemExit(code)


if __name__ == "__main__":
    main()
