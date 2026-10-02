"""Small immutable domain values used by notification services."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    delivered: bool
    alert_id: str
    channel: str
    status: str
    recipient_count: int | None = None
    error_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value is not None}


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    should_send: bool
    channel: str
    status: str
    high_priority: bool
