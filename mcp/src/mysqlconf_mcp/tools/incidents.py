"""Implementation of the incident_raise MCP tool."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Mapping
from typing import Any

from mysqlconf_mcp.config import (
    McpConfigurationError,
    dba_enabled,
    notification_enabled,
)
from mysqlconf_mcp.delivery import (
    DbaAdapterError,
    DbaDelivery,
    DbaGateway,
    DbaInboxAdapter,
    NotificationAdapterError,
    NotificationDelivery,
    NotificationDispatcherAdapter,
    NotificationGateway,
)
from mysqlconf_mcp.validation.alert_validator import (
    contract_allows_value,
    validate_alert,
)

logger = logging.getLogger(__name__)

_SAFE_ID = re.compile(r"^[0-9a-fA-F-]{36}$")
_SAFE_CODE = re.compile(r"^(?:-|[a-z][a-z0-9_,]{0,1023})$")

DeliveryFactory = Callable[[Mapping[str, str] | None], NotificationGateway]
DbaFactory = Callable[[Mapping[str, str] | None], DbaGateway]


def _safe_log_value(value: Any, contract_field: str | None = None) -> str:
    if not isinstance(value, str):
        return "<invalid>"
    if contract_field is not None:
        return value if contract_allows_value(contract_field, value) else "<invalid>"
    return value if _SAFE_ID.fullmatch(value) else "<invalid>"


def _safe_log_code(value: Any) -> str:
    if isinstance(value, str) and _SAFE_CODE.fullmatch(value):
        return value
    return "<invalid>"


def _log_result(
    alert: dict[str, Any],
    *,
    validation: str,
    delivery_status: str,
    delivery_error_code: str,
    dba_status: str,
    dba_error_code: str,
    warning: bool,
) -> None:
    alert_id = _safe_log_value(alert.get("alert_id"))
    audit_id = _safe_log_value(alert.get("audit_id"))
    severity = _safe_log_value(alert.get("severity"), "severity")
    category = _safe_log_value(alert.get("category"), "category")
    log = logger.warning if warning else logger.info
    log(
        "incident_raise alert_id=%s audit_id=%s severity=%s category=%s "
        "validation=%s delivery_status=%s delivery_error_code=%s "
        "dba_status=%s dba_error_code=%s",
        alert_id,
        audit_id,
        severity,
        category,
        _safe_log_code(validation),
        _safe_log_code(delivery_status),
        _safe_log_code(delivery_error_code),
        _safe_log_code(dba_status),
        _safe_log_code(dba_error_code),
    )


def _validated_response(
    alert: dict[str, Any],
    delivery: NotificationDelivery,
    dba: DbaDelivery,
) -> dict[str, Any]:
    persisted = dba.status in {"recorded", "duplicate"}
    return {
        "accepted": persisted,
        "status": "validated" if persisted else "persistence_required",
        "alert_id": alert["alert_id"],
        "audit_id": alert["audit_id"],
        "delivery": delivery.to_dict(),
        "dba": dba.to_dict(),
    }


def _persist_for_dba(
    alert: dict[str, Any],
    *,
    environ: Mapping[str, str] | None,
    dba_factory: DbaFactory | None,
) -> DbaDelivery:
    try:
        route_enabled = dba_enabled(environ)
    except McpConfigurationError as error:
        return DbaDelivery.failed(error.code)

    if not route_enabled:
        return DbaDelivery(target="dba", status="not_configured")

    factory = dba_factory or DbaInboxAdapter.from_environment
    try:
        gateway = factory(environ)
        delivery = gateway.deliver(alert)
        if not isinstance(delivery, DbaDelivery):
            return DbaDelivery.failed("dba_invalid_result")
        return delivery
    except DbaAdapterError as error:
        return DbaDelivery.failed(error.code)
    except Exception:
        return DbaDelivery.failed("dba_adapter_failed")


def _deliver_notification(
    alert: dict[str, Any],
    *,
    environ: Mapping[str, str] | None,
    delivery_factory: DeliveryFactory | None,
) -> NotificationDelivery:
    try:
        route_enabled = notification_enabled(environ)
    except McpConfigurationError as error:
        return NotificationDelivery.failed(error.code)

    if not route_enabled:
        return NotificationDelivery(target="notification", status="not_configured")

    factory = delivery_factory or NotificationDispatcherAdapter.from_environment
    try:
        gateway = factory(environ)
        delivery = gateway.deliver(alert)
        if not isinstance(delivery, NotificationDelivery):
            return NotificationDelivery.failed("notification_invalid_result")
        return delivery
    except NotificationAdapterError as error:
        return NotificationDelivery.failed(error.code)
    except Exception:
        return NotificationDelivery.failed("notification_adapter_failed")


def process_incident_raise(
    alert: dict[str, Any],
    *,
    environ: Mapping[str, str] | None = None,
    delivery_factory: DeliveryFactory | None = None,
    dba_factory: DbaFactory | None = None,
) -> dict[str, Any]:
    """Validate, persist for the DBA, then optionally notify."""
    errors = validate_alert(alert)

    if errors:
        error_codes = ",".join(sorted({error["code"] for error in errors})[:16])
        _log_result(
            alert,
            validation="rejected",
            delivery_status="not_attempted",
            delivery_error_code=error_codes,
            dba_status="not_attempted",
            dba_error_code=error_codes,
            warning=True,
        )
        return {
            "accepted": False,
            "status": "rejected",
            "errors": errors,
        }

    dba = _persist_for_dba(
        alert,
        environ=environ,
        dba_factory=dba_factory,
    )
    if dba.status not in {"recorded", "duplicate"}:
        delivery = NotificationDelivery(
            target="notification",
            status="not_attempted",
            delivered=False,
            error_code="dba_persistence_required",
        )
    else:
        delivery = _deliver_notification(
            alert,
            environ=environ,
            delivery_factory=delivery_factory,
        )

    _log_result(
        alert,
        validation="validated",
        delivery_status=delivery.status,
        delivery_error_code=delivery.error_code or "-",
        dba_status=dba.status,
        dba_error_code=dba.error_code or "-",
        warning=(
            delivery.status in {"failed", "not_attempted"}
            or dba.status not in {"recorded", "duplicate"}
        ),
    )
    return _validated_response(alert, delivery, dba)


def incident_raise(alert: dict[str, Any]) -> dict[str, Any]:
    """Validate one supported incident and optionally dispatch both routes."""
    return process_incident_raise(alert)
