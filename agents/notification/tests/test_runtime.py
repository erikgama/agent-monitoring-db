from __future__ import annotations

import subprocess
import unittest
from typing import Any

from notification.runtime import (
    CredentialResolutionError,
    resolve_runtime_settings,
)


def environment(**changes: str) -> dict[str, str]:
    value = {
        "NOTIFICATION_DELIVERY_ENABLED": "true",
        "NOTIFICATION_EMAIL_FROM": "notifier@example.invalid",
        "NOTIFICATION_EMAIL_RECIPIENTS_CRITICAL": "operator@example.invalid",
        "SMTP_HOST": "smtp.gmail.com",
        "SMTP_PORT": "587",
        "SMTP_USERNAME": "notifier@example.invalid",
        "SMTP_USE_STARTTLS": "true",
    }
    value.update(changes)
    return value


class SpyRunner:
    def __init__(self, *, password: str = "keychain-secret", returncode: int = 0):
        self.password = password
        self.returncode = returncode
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def __call__(self, command: list[str], **kwargs: Any):
        self.calls.append((command, kwargs))
        return subprocess.CompletedProcess(
            command,
            self.returncode,
            stdout=f"{self.password}\n" if self.returncode == 0 else "",
            stderr="never-exposed",
        )


class NotificationRuntimeTests(unittest.TestCase):
    def test_portable_helper_is_called_only_by_notification(self) -> None:
        runner = SpyRunner(password="broker-secret")
        settings = resolve_runtime_settings(
            environment(NOTIFICATION_SMTP_CREDENTIAL_HELPER="/opt/company/smtp-secret"),
            runner=runner,
        )
        self.assertEqual(settings.smtp_password, "broker-secret")
        self.assertEqual(
            runner.calls[0][0],
            [
                "/opt/company/smtp-secret",
                "--account",
                "notifier@example.invalid",
            ],
        )
        self.assertEqual(runner.calls[0][1]["timeout"], 5)

    def test_relative_helper_is_rejected(self) -> None:
        runner = SpyRunner()
        with self.assertRaisesRegex(
            CredentialResolutionError, "smtp_credential_helper_invalid"
        ):
            resolve_runtime_settings(
                environment(NOTIFICATION_SMTP_CREDENTIAL_HELPER="relative-helper"),
                runner=runner,
            )
        self.assertEqual(runner.calls, [])

    def test_helper_failure_never_exposes_output(self) -> None:
        with self.assertRaisesRegex(
            CredentialResolutionError, "smtp_credential_lookup_failed"
        ):
            resolve_runtime_settings(
                environment(
                    NOTIFICATION_SMTP_CREDENTIAL_HELPER="/opt/company/smtp-secret"
                ),
                runner=SpyRunner(returncode=1),
            )

    def test_disabled_delivery_never_calls_portable_helper(self) -> None:
        runner = SpyRunner()
        resolve_runtime_settings(
            environment(
                NOTIFICATION_DELIVERY_ENABLED="false",
                NOTIFICATION_SMTP_CREDENTIAL_HELPER="/opt/company/smtp-secret",
            ),
            runner=runner,
        )
        self.assertEqual(runner.calls, [])

    def test_notification_resolves_its_own_keychain_password(self) -> None:
        runner = SpyRunner()
        settings = resolve_runtime_settings(environment(), runner=runner)

        self.assertEqual(settings.smtp_password, "keychain-secret")
        self.assertEqual(len(runner.calls), 1)
        command, options = runner.calls[0]
        self.assertEqual(
            command,
            [
                "security",
                "find-generic-password",
                "-w",
                "-a",
                "notifier@example.invalid",
                "-s",
                "mysqlconf-notification-smtp",
            ],
        )
        self.assertTrue(options["capture_output"])
        self.assertEqual(options["timeout"], 5)

    def test_dry_run_never_reads_keychain(self) -> None:
        runner = SpyRunner()
        settings = resolve_runtime_settings(
            environment(NOTIFICATION_DELIVERY_ENABLED="false"),
            runner=runner,
        )

        self.assertFalse(settings.delivery_enabled)
        self.assertEqual(runner.calls, [])

    def test_injected_password_does_not_read_keychain(self) -> None:
        runner = SpyRunner()
        settings = resolve_runtime_settings(
            environment(SMTP_PASSWORD="injected-secret"), runner=runner
        )

        self.assertEqual(settings.smtp_password, "injected-secret")
        self.assertEqual(runner.calls, [])

    def test_keychain_failure_is_safe(self) -> None:
        runner = SpyRunner(returncode=44)
        with self.assertRaisesRegex(
            CredentialResolutionError, "smtp_keychain_lookup_failed"
        ):
            resolve_runtime_settings(environment(), runner=runner)

    def test_keychain_service_cannot_be_redirected(self) -> None:
        with self.assertRaisesRegex(
            CredentialResolutionError, "smtp_keychain_service_invalid"
        ):
            resolve_runtime_settings(
                environment(NOTIFICATION_SMTP_KEYCHAIN_SERVICE="other-service"),
                runner=SpyRunner(),
            )


if __name__ == "__main__":
    unittest.main()
