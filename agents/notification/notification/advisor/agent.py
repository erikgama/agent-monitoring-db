"""Route validated events without recalculating their severity."""

from __future__ import annotations

from pathlib import Path

from notification.domain import RoutingDecision

RULES_PATH = Path(__file__).with_name("rules.md")
_ALERTABLE_SEVERITIES = frozenset({"warning", "critical"})


class NotificationAdvisor:
    """Apply the local channel policy to a severity received from the MCP."""

    def decide_alert(self, severity: str) -> RoutingDecision:
        if severity == "info":
            return RoutingDecision(
                should_send=False,
                channel="email",
                status="suppressed_by_policy",
                high_priority=False,
            )
        if severity not in _ALERTABLE_SEVERITIES:
            raise ValueError("invalid_severity")
        return RoutingDecision(
            should_send=True,
            channel="email",
            status="eligible",
            high_priority=severity == "critical",
        )

    def decide_refactor_result(self) -> RoutingDecision:
        """Route the MCP completion notice; it is not a severity classification."""
        return RoutingDecision(
            should_send=True,
            channel="email",
            status="eligible",
            high_priority=False,
        )


def read_role() -> str:
    """Expose the Portuguese role used by the console and operator review."""
    return RULES_PATH.read_text(encoding="utf-8")
