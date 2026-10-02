"""Operator-controlled notification settings."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass


class ConfigurationError(ValueError):
    """Raised with a stable code and without echoing configuration values."""


def _boolean(value: str | None, *, default: bool, code: str) -> bool:
    if value is None or not value.strip():
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(code)


def _recipients(value: str | None) -> tuple[str, ...]:
    if value is None:
        return ()
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


@dataclass(frozen=True, slots=True)
class NotificationSettings:
    """Configuration loaded only from the operator's process environment."""

    delivery_enabled: bool = False
    email_from: str | None = None
    warning_recipients: tuple[str, ...] = ()
    critical_recipients: tuple[str, ...] = ()
    refactor_recipients: tuple[str, ...] = ()
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_use_starttls: bool = True
    smtp_timeout_seconds: float = 10.0

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> NotificationSettings:
        source = os.environ if environ is None else environ
        raw_port = source.get("SMTP_PORT", "587")
        try:
            smtp_port = int(raw_port)
        except ValueError as error:
            raise ConfigurationError("smtp_port_invalid") from error
        if not 1 <= smtp_port <= 65535:
            raise ConfigurationError("smtp_port_invalid")

        return cls(
            delivery_enabled=_boolean(
                source.get("NOTIFICATION_DELIVERY_ENABLED"),
                default=False,
                code="delivery_enabled_invalid",
            ),
            email_from=_optional_text(source.get("NOTIFICATION_EMAIL_FROM")),
            warning_recipients=_recipients(
                source.get("NOTIFICATION_EMAIL_RECIPIENTS_WARNING")
            ),
            critical_recipients=_recipients(
                source.get("NOTIFICATION_EMAIL_RECIPIENTS_CRITICAL")
            ),
            refactor_recipients=_recipients(
                source.get("NOTIFICATION_EMAIL_RECIPIENTS_REFACTOR")
            ),
            smtp_host=_optional_text(source.get("SMTP_HOST")),
            smtp_port=smtp_port,
            smtp_username=_optional_text(source.get("SMTP_USERNAME")),
            smtp_password=source.get("SMTP_PASSWORD") or None,
            smtp_use_starttls=_boolean(
                source.get("SMTP_USE_STARTTLS"),
                default=True,
                code="smtp_use_starttls_invalid",
            ),
        )

    def recipients_for(self, severity: str) -> tuple[str, ...]:
        if severity == "warning":
            return self.warning_recipients
        if severity == "critical":
            return self.critical_recipients
        return ()

    def recipients_for_refactor(self) -> tuple[str, ...]:
        """Use a dedicated list when configured, otherwise the DBA warning list."""
        return self.refactor_recipients or self.warning_recipients

    def smtp_error_code(self) -> str | None:
        """Return a safe error code when enabled SMTP delivery is incomplete."""
        if not self.email_from:
            return "email_from_not_configured"
        if not self.smtp_host:
            return "smtp_not_configured"
        if bool(self.smtp_username) != bool(self.smtp_password):
            return "smtp_credentials_incomplete"
        if (
            self.smtp_username
            and self.email_from.casefold() != self.smtp_username.casefold()
        ):
            return "smtp_sender_mismatch"
        if self.smtp_host.casefold() == "smtp.gmail.com":
            if self.smtp_port != 587:
                return "gmail_smtp_port_invalid"
            if not self.smtp_use_starttls:
                return "gmail_starttls_required"
            if not self.smtp_username or not self.smtp_password:
                return "smtp_credentials_required"
        return None
