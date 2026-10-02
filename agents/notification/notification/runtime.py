"""Notification-owned runtime assembly and local credential resolution."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path

from notification.channels import EmailChannel
from notification.config import NotificationSettings
from notification.dispatcher import NotificationDispatcher
from notification.refactor_dispatcher import RefactorCompletionDispatcher

KEYCHAIN_SERVICE = "mysqlconf-notification-smtp"


class CredentialResolutionError(RuntimeError):
    """Safe failure raised without exposing keychain output or configuration."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


Runner = Callable[..., subprocess.CompletedProcess[str]]


def resolve_runtime_settings(
    environ: Mapping[str, str] | None = None,
    *,
    runner: Runner | None = None,
) -> NotificationSettings:
    """Only Notification resolves its secret through the operator's broker."""
    settings = NotificationSettings.from_env(environ)
    if (
        not settings.delivery_enabled
        or settings.smtp_password
        or not settings.smtp_username
    ):
        return settings

    source = os.environ if environ is None else environ
    helper = source.get("NOTIFICATION_SMTP_CREDENTIAL_HELPER", "")
    if helper:
        if not Path(helper).is_absolute():
            raise CredentialResolutionError("smtp_credential_helper_invalid")
        try:
            completed = (runner or subprocess.run)(
                [helper, "--account", settings.smtp_username],
                text=True,
                capture_output=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise CredentialResolutionError("smtp_credential_lookup_failed") from error
        password = completed.stdout.rstrip("\r\n") if completed.returncode == 0 else ""
        if not password or "\n" in password or "\r" in password:
            raise CredentialResolutionError("smtp_credential_lookup_failed")
        return replace(settings, smtp_password=password)
    service = source.get("NOTIFICATION_SMTP_KEYCHAIN_SERVICE", KEYCHAIN_SERVICE)
    if service != KEYCHAIN_SERVICE:
        raise CredentialResolutionError("smtp_keychain_service_invalid")
    try:
        selected_runner = runner or subprocess.run
        completed = selected_runner(
            [
                "security",
                "find-generic-password",
                "-w",
                "-a",
                settings.smtp_username,
                "-s",
                service,
            ],
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise CredentialResolutionError("smtp_keychain_lookup_failed") from error
    password = completed.stdout.rstrip("\r\n") if completed.returncode == 0 else ""
    if not password:
        raise CredentialResolutionError("smtp_keychain_lookup_failed")
    return replace(settings, smtp_password=password)


def build_dispatcher(
    environ: Mapping[str, str] | None = None,
    *,
    runner: Runner | None = None,
) -> NotificationDispatcher:
    """Build the public dispatcher without exposing credentials to its caller."""
    settings = resolve_runtime_settings(environ, runner=runner)
    return NotificationDispatcher(settings, EmailChannel(settings))


def build_refactor_dispatcher(
    environ: Mapping[str, str] | None = None,
    *,
    runner: Runner | None = None,
) -> RefactorCompletionDispatcher:
    """Build the completion dispatcher without exposing SMTP credentials."""
    settings = resolve_runtime_settings(environ, runner=runner)
    return RefactorCompletionDispatcher(settings, EmailChannel(settings))
