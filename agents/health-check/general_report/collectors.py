"""Domain collectors built only from allowlisted SQL blocks."""

from __future__ import annotations

from collections.abc import Callable

from src.db import DatabaseRunner
from src.models import CapabilityMatrix, QueryResult, SourceStatus

GENERAL_REPORT_SQL_FILES = (
    "00_capabilities.sql",
    "10_instance.sql",
    "20_connections.sql",
    "30_workload.sql",
    "40_active_sessions.sql",
    "50_locks_deadlocks.sql",
    "60_innodb.sql",
    "70_schema_tables.sql",
    "80_indexes.sql",
    "90_errors.sql",
    "100_replication.sql",
)

DOMAIN_ORDER = (
    "instance",
    "connections",
    "workload",
    "active_sessions",
    "locks",
    "innodb",
    "schema_tables",
    "indexes",
    "errors",
    "replication",
)


def unavailable(reason: str) -> dict[str, QueryResult]:
    return {
        "source": QueryResult(
            status=SourceStatus.NOT_AVAILABLE,
            reason=reason,
        )
    }


def read_domain(
    domain: str,
    runner: DatabaseRunner,
    capabilities: CapabilityMatrix,
    parameters: dict[str, int],
) -> dict[str, QueryResult]:
    collectors: dict[str, Callable[[], dict[str, QueryResult]]] = {
        "instance": lambda: runner.execute_file("10_instance.sql", parameters),
        "connections": lambda: runner.execute_file("20_connections.sql", parameters),
        "workload": lambda: _collect_workload(runner, capabilities, parameters),
        "active_sessions": lambda: _collect_active_sessions(
            runner, capabilities, parameters
        ),
        "locks": lambda: _collect_locks(runner, capabilities, parameters),
        "innodb": lambda: _collect_innodb(runner, capabilities, parameters),
        "schema_tables": lambda: runner.execute_file(
            "70_schema_tables.sql", parameters
        ),
        "indexes": lambda: _collect_indexes(runner, capabilities, parameters),
        "errors": lambda: runner.execute_file("90_errors.sql", parameters),
        "replication": lambda: _collect_replication(runner, capabilities, parameters),
    }
    if domain not in collectors:
        raise ValueError(f"unknown_domain:{domain}")
    if not capabilities.supports(domain):
        return unavailable("required_source_not_available")
    return collectors[domain]()


def _collect_workload(
    runner: DatabaseRunner,
    capabilities: CapabilityMatrix,
    parameters: dict[str, int],
) -> dict[str, QueryResult]:
    modern = {"QUANTILE_95", "COUNT_SECONDARY"}.issubset(capabilities.digest_columns)
    digest_block = "top_digests_modern" if modern else "top_digests_legacy"
    return runner.execute_file(
        "30_workload.sql", parameters, [digest_block, "aggregate"]
    )


def _collect_active_sessions(
    runner: DatabaseRunner,
    capabilities: CapabilityMatrix,
    parameters: dict[str, int],
) -> dict[str, QueryResult]:
    if capabilities.has("sys", "processlist"):
        primary = runner.execute_file(
            "40_active_sessions.sql", parameters, ["sys_processlist"]
        )
        if primary["sys_processlist"].status == SourceStatus.AVAILABLE:
            return primary
        if capabilities.has("performance_schema", "threads"):
            fallback = runner.execute_file(
                "40_active_sessions.sql", parameters, ["threads_fallback"]
            )
            return {**primary, **fallback}
        return primary
    return runner.execute_file(
        "40_active_sessions.sql", parameters, ["threads_fallback"]
    )


def _collect_locks(
    runner: DatabaseRunner,
    capabilities: CapabilityMatrix,
    parameters: dict[str, int],
) -> dict[str, QueryResult]:
    blocks = ["lock_waits"]
    if capabilities.has("performance_schema", "events_errors_summary_global_by_error"):
        blocks.insert(0, "deadlocks")
    if capabilities.has("sys", "schema_table_lock_waits"):
        blocks.append("table_lock_waits")
    return runner.execute_file("50_locks_deadlocks.sql", parameters, blocks)


def _collect_innodb(
    runner: DatabaseRunner,
    capabilities: CapabilityMatrix,
    parameters: dict[str, int],
) -> dict[str, QueryResult]:
    blocks = ["status"]
    if capabilities.has("information_schema", "innodb_metrics"):
        blocks.append("metrics")
    return runner.execute_file("60_innodb.sql", parameters, blocks)


def _collect_indexes(
    runner: DatabaseRunner,
    capabilities: CapabilityMatrix,
    parameters: dict[str, int],
) -> dict[str, QueryResult]:
    blocks: list[str] = []
    if capabilities.has("sys", "schema_unused_indexes"):
        blocks.append("unused_indexes")
    if capabilities.has("sys", "schema_redundant_indexes"):
        blocks.append("redundant_indexes")
    blocks.extend(["selectivity", "large_without_secondary"])
    return runner.execute_file("80_indexes.sql", parameters, blocks)


def _collect_replication(
    runner: DatabaseRunner,
    capabilities: CapabilityMatrix,
    parameters: dict[str, int],
) -> dict[str, QueryResult]:
    show = runner.execute_file("100_replication.sql", parameters, ["replica_status"])
    replica_result = show["replica_status"]
    if replica_result.status == SourceStatus.AVAILABLE:
        return show

    fallback_blocks = [
        block
        for block, table in (
            ("connection_status", "replication_connection_status"),
            ("applier_status", "replication_applier_status"),
            ("group_members", "replication_group_members"),
        )
        if capabilities.has("performance_schema", table)
    ]
    if not fallback_blocks:
        return show
    fallback = runner.execute_file("100_replication.sql", parameters, fallback_blocks)
    return {**show, **fallback}
