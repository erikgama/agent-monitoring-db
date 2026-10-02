"""Publisher boundary for locally validated alerts."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class PublicationResult:
    published_to_mcp: bool
    accepted: bool | None
    mcp_status: str
    delivery_status: str | None = None
    delivery: dict[str, Any] | None = None
    dba_status: str | None = None
    dba: dict[str, Any] | None = None
    error_code: str | None = None

    def as_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "published_to_mcp": self.published_to_mcp,
            "accepted": self.accepted,
            "mcp_status": self.mcp_status,
            "delivery_status": self.delivery_status,
        }
        if self.delivery is not None:
            value["delivery"] = self.delivery
        if self.dba_status is not None:
            value["dba_status"] = self.dba_status
        if self.dba is not None:
            value["dba"] = self.dba
        if self.error_code is not None:
            value["error_code"] = self.error_code
        return value


class AlertPublisher(ABC):
    @abstractmethod
    def publish(self, alert: dict[str, Any]) -> PublicationResult:
        """Publish one already validated alert."""


@dataclass(slots=True)
class NoOpAlertPublisher(AlertPublisher):
    """In-memory publisher used by unit tests; it performs no I/O."""

    result: PublicationResult = field(
        default_factory=lambda: PublicationResult(
            published_to_mcp=True,
            accepted=True,
            mcp_status="validated",
            delivery_status="dry_run",
            delivery={
                "target": "notification",
                "channel": "email",
                "status": "dry_run",
                "delivered": False,
            },
        )
    )
    calls: list[dict[str, Any]] = field(default_factory=list)

    def publish(self, alert: dict[str, Any]) -> PublicationResult:
        self.calls.append(alert)
        return self.result
