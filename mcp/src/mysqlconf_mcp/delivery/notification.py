"""Adapter from the MCP boundary to the notification agent's public API."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any, Protocol

from notification import (
    CredentialResolutionError,
    NotificationDispatcher,
    RefactorCompletionDispatcher,
    build_dispatcher,
    build_refactor_dispatcher,
)

_SAFE_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


@dataclass(frozen=True, slots=True)
class NotificationDelivery:
    """Sanitized delivery outcome exposed by the MCP."""

    target: str
    status: str
    delivered: bool | None = None
    channel: str | None = None
    error_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value is not None}

    @classmethod
    def failed(cls, error_code: str) -> NotificationDelivery:
        safe_error_code = _safe_code(error_code) or "notification_failure"
        return cls(
            target="notification",
            status="failed",
            delivered=False,
            error_code=safe_error_code,
        )


class NotificationGateway(Protocol):
    """Narrow boundary consumed by incident_raise."""

    def deliver(self, alert: dict[str, Any]) -> NotificationDelivery: ...


class RefactorNotificationGateway(Protocol):
    """Narrow boundary consumed after a Refactor result reaches the DBA."""

    def deliver(self, result: dict[str, Any]) -> NotificationDelivery: ...


class NotificationAdapterError(RuntimeError):
    """Safe adapter construction failure."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _safe_code(value: Any) -> str | None:
    return value if isinstance(value, str) and _SAFE_CODE.fullmatch(value) else None


class NotificationDispatcherAdapter:
    """Call NotificationDispatcher without owning notification decisions."""

    def __init__(self, dispatcher: NotificationDispatcher) -> None:
        self._dispatcher = dispatcher

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> NotificationDispatcherAdapter:
        try:
            return cls(build_dispatcher(environ))
        except CredentialResolutionError as error:
            raise NotificationAdapterError(error.code) from error
        except Exception as error:
            raise NotificationAdapterError(
                "notification_configuration_invalid"
            ) from error

    def deliver(self, alert: dict[str, Any]) -> NotificationDelivery:
        try:
            result = self._dispatcher.dispatch(alert)
        except Exception:
            return NotificationDelivery.failed("notification_dispatch_failed")
        return self._normalize(result, expected_alert_id=alert["alert_id"])

    @staticmethod
    def _normalize(
        result: Any,
        *,
        expected_alert_id: str,
    ) -> NotificationDelivery:
        if (
            not isinstance(result, Mapping)
            or result.get("alert_id") != expected_alert_id
        ):
            return NotificationDelivery.failed("notification_invalid_result")

        status = _safe_code(result.get("status"))
        channel = _safe_code(result.get("channel"))
        delivered = result.get("delivered")
        if status is None or channel is None or not isinstance(delivered, bool):
            return NotificationDelivery.failed("notification_invalid_result")

        error_code = result.get("error_code")
        safe_error_code = _safe_code(error_code) if error_code is not None else None
        if error_code is not None and safe_error_code is None:
            return NotificationDelivery.failed("notification_invalid_result")
        if status == "failed":
            return NotificationDelivery.failed(
                safe_error_code or "notification_delivery_failed"
            )

        return NotificationDelivery(
            target="notification",
            channel=channel,
            status=status,
            delivered=delivered,
            error_code=safe_error_code,
        )


class RefactorNotificationDispatcherAdapter:
    """Call the Notification-owned completion dispatcher."""

    def __init__(self, dispatcher: RefactorCompletionDispatcher) -> None:
        self._dispatcher = dispatcher

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> RefactorNotificationDispatcherAdapter:
        try:
            return cls(build_refactor_dispatcher(environ))
        except CredentialResolutionError as error:
            raise NotificationAdapterError(error.code) from error
        except Exception as error:
            raise NotificationAdapterError(
                "notification_configuration_invalid"
            ) from error

    def deliver(self, result: dict[str, Any]) -> NotificationDelivery:
        try:
            delivered = self._dispatcher.dispatch(result)
        except Exception:
            return NotificationDelivery.failed("notification_dispatch_failed")
        return NotificationDispatcherAdapter._normalize(
            delivered,
            expected_alert_id=result["result_id"],
        )
