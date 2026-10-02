"""Base interface for current and future delivery channels."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from typing import Any

from notification.domain import DeliveryResult


class NotificationChannel(ABC):
    """One bounded delivery attempt for a validated alert."""

    name: str

    @abstractmethod
    def send(
        self,
        alert: Mapping[str, Any],
        recipients: Sequence[str],
        *,
        high_priority: bool,
    ) -> DeliveryResult:
        """Attempt delivery once and return a non-sensitive result."""
