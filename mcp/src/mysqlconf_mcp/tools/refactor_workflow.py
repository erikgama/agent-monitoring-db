"""MCP tools for Health Check -> Refactor -> DBA."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from mysqlconf_mcp.config import McpConfigurationError, notification_enabled
from mysqlconf_mcp.delivery import (
    NotificationAdapterError,
    NotificationDelivery,
    RefactorNotificationDispatcherAdapter,
    RefactorNotificationGateway,
)
from mysqlconf_mcp.delivery.refactor_workflow import (
    record_dba_result,
    record_refactor_request,
)
from mysqlconf_mcp.validation.refactor_validator import (
    validate_refactor_request,
    validate_refactor_result,
)


def _configured_path(name: str) -> Path | None:
    value = os.environ.get(name)
    if not value:
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError("configured_directory_must_be_absolute")
    return path


def refactor_request_raise(request: dict[str, Any]) -> dict[str, Any]:
    errors = validate_refactor_request(request)
    if errors:
        return {"accepted": False, "status": "rejected", "errors": errors}
    try:
        delivery = record_refactor_request(
            request, _configured_path("MCP_REFACTOR_REQUESTS_DIR")
        )
    except (OSError, ValueError):
        delivery = {"target": "refactor", "status": "failed", "recorded": False}
    return {
        "accepted": delivery["status"] in {"recorded", "duplicate"},
        "status": "validated",
        "request_id": request["request_id"],
        "refactor": delivery,
    }


def refactor_result_raise(result: dict[str, Any]) -> dict[str, Any]:
    return process_refactor_result_raise(result)


RefactorNotificationFactory = Callable[
    [Mapping[str, str] | None], RefactorNotificationGateway
]


def process_refactor_result_raise(
    result: dict[str, Any],
    *,
    environ: Mapping[str, str] | None = None,
    notification_factory: RefactorNotificationFactory | None = None,
) -> dict[str, Any]:
    errors = validate_refactor_result(result)
    if errors:
        return {"accepted": False, "status": "rejected", "errors": errors}
    try:
        delivery = record_dba_result(
            result, _configured_path("MCP_DBA_REFACTOR_RESULTS_DIR")
        )
    except (OSError, ValueError):
        delivery = {"target": "dba", "status": "failed", "recorded": False}

    if delivery["status"] == "duplicate":
        notification = NotificationDelivery(
            target="notification",
            status="duplicate",
            delivered=False,
        )
    elif delivery["status"] != "recorded":
        notification = NotificationDelivery(
            target="notification",
            status="blocked_by_dba_delivery",
            delivered=False,
        )
    else:
        try:
            enabled = notification_enabled(environ)
        except McpConfigurationError as error:
            notification = NotificationDelivery.failed(error.code)
        else:
            if not enabled:
                notification = NotificationDelivery(
                    target="notification",
                    status="not_configured",
                )
            else:
                factory = (
                    notification_factory
                    or RefactorNotificationDispatcherAdapter.from_environment
                )
                try:
                    gateway = factory(environ)
                    notification = gateway.deliver(result)
                    if not isinstance(notification, NotificationDelivery):
                        notification = NotificationDelivery.failed(
                            "notification_invalid_result"
                        )
                except NotificationAdapterError as error:
                    notification = NotificationDelivery.failed(error.code)
                except Exception:
                    notification = NotificationDelivery.failed(
                        "notification_adapter_failed"
                    )
    return {
        "accepted": delivery["status"] in {"recorded", "duplicate"},
        "status": "validated",
        "result_id": result["result_id"],
        "dba": delivery,
        "notification": notification.to_dict(),
    }
