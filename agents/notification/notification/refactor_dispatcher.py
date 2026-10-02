"""Validated, single-attempt completion notice for Refactor results."""

from __future__ import annotations

import logging
import re
from typing import Any

from notification.advisor import NotificationAdvisor
from notification.channels.email import EmailChannel
from notification.config import NotificationSettings
from notification.domain import DeliveryResult
from notification.validation import validate_refactor_result

logger = logging.getLogger(__name__)
_SAFE_UUID = re.compile(r"^[0-9a-fA-F-]{36}$")
_SAFE_QUERY_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SAFE_STATUS = frozenset({"approved_lab", "requires_dba_validation"})


def _safe_uuid(value: Any) -> str:
    return (
        value if isinstance(value, str) and _SAFE_UUID.fullmatch(value) else "<invalid>"
    )


def _safe_query_id(value: Any) -> str:
    return (
        value
        if isinstance(value, str) and _SAFE_QUERY_ID.fullmatch(value)
        else "<invalid>"
    )


def _safe_status(value: Any) -> str:
    return value if isinstance(value, str) and value in _SAFE_STATUS else "<invalid>"


class RefactorCompletionDispatcher:
    """Notify the DBA only after the MCP has accepted a Refactor result."""

    def __init__(
        self,
        settings: NotificationSettings,
        email_channel: EmailChannel,
        advisor: NotificationAdvisor | None = None,
    ) -> None:
        self._settings = settings
        self._email_channel = email_channel
        self._advisor = advisor or NotificationAdvisor()

    def dispatch(self, result: Any) -> dict[str, Any]:
        errors = validate_refactor_result(result)
        result_id = _safe_uuid(
            result.get("result_id") if isinstance(result, dict) else None
        )
        if errors:
            delivery = DeliveryResult(
                False,
                result_id,
                "email",
                "failed",
                error_code="invalid_refactor_result",
            )
            self._log(result, delivery)
            return delivery.to_dict()

        decision = self._advisor.decide_refactor_result()
        recipients = self._settings.recipients_for_refactor()
        if not recipients:
            delivery = DeliveryResult(
                False,
                result_id,
                decision.channel,
                "failed",
                error_code="recipients_not_configured",
            )
        elif not self._settings.delivery_enabled:
            delivery = DeliveryResult(
                False,
                result_id,
                decision.channel,
                "dry_run",
                recipient_count=len(recipients),
            )
        else:
            delivery = self._email_channel.send_refactor_completion(
                result,
                recipients,
            )
        self._log(result, delivery)
        return delivery.to_dict()

    @staticmethod
    def _log(result: Any, delivery: DeliveryResult) -> None:
        value = result if isinstance(result, dict) else {}
        logger.info(
            "refactor_result_id=%s query_id=%s status=%s channel=%s delivery_status=%s",
            _safe_uuid(value.get("result_id")),
            _safe_query_id(value.get("query_id")),
            _safe_status(value.get("status")),
            delivery.channel,
            delivery.status,
        )
