"""Deterministic, read-only MySQL health-check collector."""

from typing import Any

__all__ = ["build_health_snapshot", "read_latest_snapshot"]


def __getattr__(name: str) -> Any:
    """Expose the general report API without importing it eagerly."""
    if name in __all__:
        from general_report.main import build_health_snapshot, read_latest_snapshot

        return {
            "build_health_snapshot": build_health_snapshot,
            "read_latest_snapshot": read_latest_snapshot,
        }[name]
    raise AttributeError(name)
