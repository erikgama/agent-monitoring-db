"""Validate, apply policy, and dispatch exactly one channel delivery."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Any

from notification.advisor import NotificationAdvisor
from notification.channels.base import NotificationChannel
from notification.config import NotificationSettings
from notification.domain import DeliveryResult
from notification.validation import validate_alert

logger = logging.getLogger(__name__)

_SAFE_ID = re.compile(r"^[0-9a-fA-F-]{36}$")
_SAFE_SEVERITIES = frozenset({"info", "warning", "critical"})
_SAFE_CATEGORIES = frozenset(
    {
        "deadlock",
        "lock_wait",
        "query_latency",
        "connections",
        "innodb",
        "replication",
        "database_error",
        "destructive_ddl",
        "schema_change",
    }
)
_SAFE_CHANNELS = frozenset({"email"})
_SAFE_STATUSES = frozenset({"dry_run", "failed", "sent", "suppressed_by_policy"})
_SAFE_ERROR_CODES = frozenset(
    {
        "email_configuration_invalid",
        "email_from_not_configured",
        "gmail_smtp_port_invalid",
        "gmail_starttls_required",
        "invalid_alert",
        "recipients_not_configured",
        "smtp_credentials_incomplete",
        "smtp_credentials_required",
        "smtp_delivery_failed",
        "smtp_not_configured",
        "smtp_sender_mismatch",
        "tls_certificate_validation_required",
        "tls_configuration_failed",
    }
)


def _safe_id(value: Any) -> str:
    return (
        value if isinstance(value, str) and _SAFE_ID.fullmatch(value) else "<invalid>"
    )


def _safe_enum(value: Any, allowed: frozenset[str]) -> str:
    return value if isinstance(value, str) and value in allowed else "<invalid>"


class NotificationDispatcher:
    """Revalidate an alert and execute the route selected by the advisor."""

    def __init__(
        self,
        settings: NotificationSettings,
        email_channel: NotificationChannel,
        advisor: NotificationAdvisor | None = None,
    ) -> None:
        self._settings = settings
        self._email_channel = email_channel
        self._advisor = advisor or NotificationAdvisor()

    def dispatch(self, alert: Any) -> dict[str, Any]:
        errors = validate_alert(alert)
        if errors:
            result = DeliveryResult(
                False,
                _safe_id(alert.get("alert_id") if isinstance(alert, Mapping) else None),
                "email",
                "failed",
                error_code="invalid_alert",
            )
            self._log(alert, result)
            return result.to_dict()

        decision = self._advisor.decide_alert(alert["severity"])
        if not decision.should_send:
            result = DeliveryResult(
                False,
                alert["alert_id"],
                decision.channel,
                decision.status,
                recipient_count=0,
            )
            self._log(alert, result)
            return result.to_dict()

        recipients = self._settings.recipients_for(alert["severity"])
        if not recipients:
            result = DeliveryResult(
                False,
                alert["alert_id"],
                "email",
                "failed",
                error_code="recipients_not_configured",
            )
            self._log(alert, result)
            return result.to_dict()

        if not self._settings.delivery_enabled:
            result = DeliveryResult(
                False,
                alert["alert_id"],
                "email",
                "dry_run",
                recipient_count=len(recipients),
            )
            self._log(alert, result)
            return result.to_dict()

        smtp_error = self._settings.smtp_error_code()
        if smtp_error:
            result = DeliveryResult(
                False,
                alert["alert_id"],
                "email",
                "failed",
                error_code=smtp_error,
            )
            self._log(alert, result)
            return result.to_dict()

        result = self._email_channel.send(
            alert,
            recipients,
            high_priority=decision.high_priority,
        )
        self._log(alert, result)
        return result.to_dict()

    def _log(self, alert: Any, result: DeliveryResult) -> None:
        value = alert if isinstance(alert, Mapping) else {}
        error_code = (
            _safe_enum(result.error_code, _SAFE_ERROR_CODES)
            if result.error_code
            else "-"
        )
        logger.info(
            "alert_id=%s audit_id=%s severity=%s category=%s channel=%s "
            "status=%s error_code=%s",
            _safe_id(value.get("alert_id")),
            _safe_id(value.get("audit_id")),
            _safe_enum(value.get("severity"), _SAFE_SEVERITIES),
            _safe_enum(value.get("category"), _SAFE_CATEGORIES),
            _safe_enum(result.channel, _SAFE_CHANNELS),
            _safe_enum(result.status, _SAFE_STATUSES),
            error_code,
        )
