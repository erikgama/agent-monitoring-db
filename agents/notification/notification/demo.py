"""Manual alert e-mail test with dry-run and explicit send gates."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any, NoReturn

from notification.channels import EmailChannel
from notification.config import ConfigurationError, NotificationSettings
from notification.dispatcher import NotificationDispatcher


class ManualTestError(ValueError):
    """A stable, non-sensitive manual-test failure code."""


class SafeArgumentParser(argparse.ArgumentParser):
    """Reject unsupported arguments without echoing their values."""

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        self.exit(2, "notification-email-test: error: invalid_arguments\n")


def _smtp_must_not_run(*args: object, **kwargs: object) -> NoReturn:
    raise AssertionError("SMTP cannot run without the --send gate")


def _parser() -> argparse.ArgumentParser:
    parser = SafeArgumentParser(
        description="Validate an alert fixture and optionally send one real e-mail"
    )
    parser.add_argument("fixture", type=Path)
    parser.add_argument(
        "--send",
        action="store_true",
        help=("Allow one SMTP attempt only when NOTIFICATION_DELIVERY_ENABLED=true"),
    )
    return parser


def run(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    smtp_factory: Any | None = None,
) -> dict[str, Any]:
    """Run the manual test; SMTP remains impossible unless both gates are open."""
    args = _parser().parse_args(argv)
    alert = json.loads(args.fixture.read_text(encoding="utf-8"))
    if alert.get("severity") not in {"warning", "critical"}:
        raise ManualTestError("manual_test_requires_alertable_severity")

    settings = NotificationSettings.from_env(environ)
    if args.send:
        if not settings.delivery_enabled:
            raise ManualTestError("delivery_not_enabled")
        channel = (
            EmailChannel(settings)
            if smtp_factory is None
            else EmailChannel(settings, smtp_factory=smtp_factory)
        )
    else:
        settings = replace(settings, delivery_enabled=False)
        channel = EmailChannel(settings, smtp_factory=_smtp_must_not_run)

    return NotificationDispatcher(settings, channel).dispatch(alert)


def main() -> None:
    exit_code = 0
    try:
        result = run()
    except (ConfigurationError, ManualTestError) as error:
        result = {
            "delivered": False,
            "channel": "email",
            "status": "failed",
            "error_code": str(error),
        }
        exit_code = 2
    except OSError:
        result = {
            "delivered": False,
            "channel": "email",
            "status": "failed",
            "error_code": "manual_fixture_unavailable",
        }
        exit_code = 2
    except json.JSONDecodeError:
        result = {
            "delivered": False,
            "channel": "email",
            "status": "failed",
            "error_code": "manual_fixture_invalid_json",
        }
        exit_code = 2
    print(
        json.dumps(
            result,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    if exit_code:
        raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
