"""Capability discovery without treating managed-service restrictions as failure."""

from __future__ import annotations

from typing import Any

from src.db import DatabaseRunner
from src.models import CapabilityMatrix, QueryResult, SourceStatus

CAPABILITY_BLOCKS = (
    "instance_identity",
    "sources",
    "consumers",
    "digest_columns",
)


def _read_capabilities(
    runner: DatabaseRunner, parameters: dict[str, int]
) -> dict[str, QueryResult]:
    return runner.execute_file(
        "00_capabilities.sql",
        parameters,
        CAPABILITY_BLOCKS,
        parallel=True,
    )


def _lower_row(row: dict[str, Any]) -> dict[str, Any]:
    return {str(key).lower(): value for key, value in row.items()}


def probe_capabilities(
    runner: DatabaseRunner, parameters: dict[str, int]
) -> CapabilityMatrix:
    results = _read_capabilities(runner, parameters)
    identity = results["instance_identity"]
    mysql_version = None
    version_comment = None
    performance_schema_enabled = None
    if identity.status == SourceStatus.AVAILABLE and identity.rows:
        row = _lower_row(identity.rows[0])
        mysql_version = str(row.get("mysql_version") or "") or None
        version_comment = str(row.get("version_comment") or "") or None
        raw_enabled = row.get("performance_schema_enabled")
        if raw_enabled is not None:
            performance_schema_enabled = str(raw_enabled).upper() in {
                "1",
                "ON",
                "YES",
                "TRUE",
            }

    sources: set[str] = set()
    source_result = results["sources"]
    if source_result.status == SourceStatus.AVAILABLE:
        for raw_row in source_result.rows:
            row = _lower_row(raw_row)
            schema = str(row.get("table_schema") or "").lower()
            table = str(row.get("table_name") or "").lower()
            if schema and table:
                sources.add(f"{schema}.{table}")

    consumers: dict[str, str] = {}
    consumer_result = results["consumers"]
    if consumer_result.status == SourceStatus.AVAILABLE:
        for raw_row in consumer_result.rows:
            row = _lower_row(raw_row)
            name = str(row.get("name") or "")
            enabled = str(row.get("enabled") or "UNKNOWN").upper()
            if name:
                consumers[name] = enabled

    digest_columns: set[str] = set()
    column_result = results["digest_columns"]
    if column_result.status == SourceStatus.AVAILABLE:
        digest_columns = {
            str(_lower_row(row).get("column_name") or "").upper()
            for row in column_result.rows
        }
        digest_columns.discard("")

    return CapabilityMatrix(
        mysql_version=mysql_version,
        version_comment=version_comment,
        performance_schema_enabled=performance_schema_enabled,
        sources=sources,
        consumers=consumers,
        digest_columns=digest_columns,
        probe_results=results,
    )


def _status_for_result(result: QueryResult) -> str:
    return result.status.value


def capability_report(matrix: CapabilityMatrix, tls_state: str) -> dict[str, Any]:
    if matrix.performance_schema_enabled is True:
        performance_schema = "available"
    elif matrix.performance_schema_enabled is False:
        performance_schema = "not_available"
    else:
        performance_schema = _status_for_result(
            matrix.probe_results["instance_identity"]
        )

    digest_consumer = matrix.consumers.get("statements_digest")
    digest_table = matrix.has(
        "performance_schema", "events_statements_summary_by_digest"
    )
    if not digest_table or digest_consumer == "NO":
        statement_digests = "not_available"
    elif digest_consumer == "YES":
        statement_digests = "available"
    else:
        statement_digests = "unknown"

    replication_sources = any(
        matrix.has("performance_schema", name)
        for name in (
            "replication_connection_status",
            "replication_applier_status",
            "replication_group_members",
        )
    )

    return {
        "performance_schema": performance_schema,
        "statement_digests": statement_digests,
        "sys_schema": "available"
        if any(source.startswith("sys.") for source in matrix.sources)
        else "not_available",
        "active_sessions": "available"
        if matrix.supports("active_sessions")
        else "not_available",
        "lock_waits": "available" if matrix.supports("locks") else "not_available",
        "innodb_metrics": "available"
        if matrix.has("information_schema", "innodb_metrics")
        else "not_available",
        "replication": "available" if replication_sources else "unknown",
        "connection_tls": tls_state,
        "probe": {
            name: result.metadata() for name, result in matrix.probe_results.items()
        },
    }
