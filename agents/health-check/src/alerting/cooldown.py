"""Repetition protection for the P99 latency alert."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CooldownPolicy:
    """Control how often the same validated alert may be published."""

    warning_seconds: int = 15 * 60
    critical_seconds: int = 2 * 60

    def seconds_for(self, severity: str) -> int:
        if severity == "critical":
            return self.critical_seconds
        if severity == "warning":
            return self.warning_seconds
        return 0
