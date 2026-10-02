"""Small typed models shared by collector components."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class SourceStatus(StrEnum):
    AVAILABLE = "available"
    NOT_AVAILABLE = "not_available"
    DEGRADED = "degraded"
    ERROR = "error"


class DomainStatus(StrEnum):
    HEALTHY = "healthy"
    ATTENTION = "attention"
    CRITICAL = "critical"
    UNKNOWN = "unknown"
    NOT_AVAILABLE = "not_available"


@dataclass(slots=True)
class QueryResult:
    status: SourceStatus
    rows: list[dict[str, Any]] = field(default_factory=list)
    duration_ms: int = 0
    reason: str | None = None

    def metadata(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "status": self.status.value,
            "duration_ms": self.duration_ms,
            "row_count": len(self.rows),
        }
        if self.reason:
            value["reason"] = self.reason
        return value


@dataclass(slots=True)
class CapabilityMatrix:
    mysql_version: str | None
    version_comment: str | None
    performance_schema_enabled: bool | None
    sources: set[str] = field(default_factory=set)
    consumers: dict[str, str] = field(default_factory=dict)
    digest_columns: set[str] = field(default_factory=set)
    probe_results: dict[str, QueryResult] = field(default_factory=dict)

    def has(self, schema: str, name: str) -> bool:
        return f"{schema.lower()}.{name.lower()}" in self.sources

    def supports(self, domain: str) -> bool:
        requirements = {
            "instance": self.has("performance_schema", "global_status"),
            "connections": self.has("performance_schema", "global_status"),
            "workload": self.has(
                "performance_schema", "events_statements_summary_by_digest"
            ),
            "active_sessions": self.has("sys", "processlist")
            or self.has("performance_schema", "threads"),
            "locks": self.has("performance_schema", "data_locks")
            and self.has("performance_schema", "data_lock_waits"),
            "innodb": self.has("performance_schema", "global_status"),
            "schema_tables": True,
            "indexes": True,
            "errors": self.has(
                "performance_schema", "events_errors_summary_global_by_error"
            ),
            # SHOW REPLICA STATUS may be available even when P_S tables are not.
            "replication": True,
        }
        return requirements.get(domain, False)
