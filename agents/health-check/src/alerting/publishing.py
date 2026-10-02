"""Validate, deduplicate, and publish alerts created from an agent decision."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ..alert_contract import AlertContractError, validate_alert
from .cooldown import CooldownPolicy
from .publisher import AlertPublisher
from .state import AlertStateStore


def _iso(value: datetime) -> str:
    return (
        value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    )


def publish_validated_alerts(
    alerts: list[dict[str, Any]],
    publisher: AlertPublisher,
    state: AlertStateStore,
    *,
    cooldown: CooldownPolicy | None = None,
    evaluated_at: datetime | None = None,
    preserve_dedupe_keys: set[str] | None = None,
) -> dict[str, Any]:
    """Publish agent alerts only after contract validation and cooldown checks."""
    now = (evaluated_at or datetime.now(UTC)).astimezone(UTC)
    policy = cooldown or CooldownPolicy()
    outcomes: list[dict[str, Any]] = []
    active_keys: set[str] = set(preserve_dedupe_keys or ())
    all_valid = True

    for alert in alerts:
        identifiers = {
            "alert_id": alert.get("alert_id"),
            "audit_id": alert.get("audit_id"),
            "dedupe_key": alert.get("dedupe_key"),
            "severity": alert.get("severity"),
            "category": alert.get("category"),
        }
        try:
            validate_alert(alert)
        except AlertContractError as error:
            all_valid = False
            outcomes.append(
                {
                    **identifiers,
                    "decision": "invalid_alert",
                    "published_to_mcp": False,
                    "accepted": None,
                    "mcp_status": "not_called",
                    "delivery_status": None,
                    "error_code": str(error),
                }
            )
            continue

        active_keys.add(alert["dedupe_key"])
        decision = state.decide(alert, now, policy)
        if not decision.publish:
            state.mark_suppressed(alert)
            outcomes.append(
                {
                    **identifiers,
                    "decision": decision.reason,
                    "published_to_mcp": False,
                    "accepted": None,
                    "mcp_status": "suppressed",
                    "delivery_status": None,
                }
            )
            continue

        result = publisher.publish(alert)
        outcome = {**identifiers, "decision": decision.reason, **result.as_dict()}
        outcomes.append(outcome)
        if result.published_to_mcp and result.accepted is True:
            state.mark_sent(alert, _iso(now))

    resolved = state.resolve_missing(active_keys) if all_valid else []
    return {
        "status": "completed" if all_valid else "invalid_alert",
        "evaluated_at": _iso(now),
        "alert_count": len(alerts),
        "published_count": sum(1 for item in outcomes if item["published_to_mcp"]),
        "suppressed_count": sum(
            1 for item in outcomes if item["decision"] == "cooldown_active"
        ),
        "resolved_dedupe_keys": resolved,
        "outcomes": outcomes,
    }
