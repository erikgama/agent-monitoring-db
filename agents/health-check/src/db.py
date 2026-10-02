"""Allowlisted DB-API execution for versioned, read-only SQL files."""

from __future__ import annotations

import re
import time
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .models import QueryResult, SourceStatus

ALLOWED_SQL: dict[str, frozenset[str]] = {
    "00_capabilities.sql": frozenset(
        {"instance_identity", "sources", "consumers", "digest_columns"}
    ),
    "10_instance.sql": frozenset({"variables", "status"}),
    "20_connections.sql": frozenset({"top_users", "top_hosts"}),
    "30_workload.sql": frozenset(
        {"top_digests_modern", "top_digests_legacy", "aggregate"}
    ),
    "40_active_sessions.sql": frozenset({"sys_processlist", "threads_fallback"}),
    "50_locks_deadlocks.sql": frozenset(
        {"deadlocks", "lock_waits", "table_lock_waits"}
    ),
    "60_innodb.sql": frozenset({"status", "metrics"}),
    "70_schema_tables.sql": frozenset(
        {"schema_summary", "largest_tables", "missing_primary_keys", "auto_increment"}
    ),
    "80_indexes.sql": frozenset(
        {
            "unused_indexes",
            "redundant_indexes",
            "selectivity",
            "large_without_secondary",
        }
    ),
    "90_errors.sql": frozenset({"errors"}),
    "100_replication.sql": frozenset(
        {"replica_status", "connection_status", "applier_status", "group_members"}
    ),
    "110_select_latency.sql": frozenset(
        {
            "select_digest_counters",
            "select_digest_histogram",
            "percentile_capabilities",
            "history_consumer",
        }
    ),
    "120_slow_query_refactor.sql": frozenset({"slow_log_settings", "slow_queries"}),
}

_BLOCK_MARKER = re.compile(r"^\s*--\s*name:\s*([a-z0-9_]+)\s*$", re.I)
_FORBIDDEN = re.compile(
    r"\b(?:INSERT|UPDATE|DELETE|REPLACE|CREATE|ALTER|DROP|TRUNCATE|"
    r"RENAME|GRANT|REVOKE|KILL|FLUSH|RESET|PURGE|LOAD|CALL|DO|HANDLER|"
    r"LOCK\s+TABLES|UNLOCK\s+TABLES|SET\s+(?:GLOBAL|PERSIST|SESSION))\b",
    re.I,
)
_SELECT_STAR = re.compile(r"\bSELECT\s+(?:/\*.*?\*/\s*)?\*", re.I | re.S)
_PLACEHOLDER = re.compile(r"\{\{([a-z_]+)\}\}")
_PERMISSION_CODES = {1044, 1045, 1142, 1143, 1227, 1370}
_OBJECT_CODES = {1054, 1109, 1146, 1305}
_TIMEOUT_CODES = {1205, 3024}


class UnsafeSqlError(ValueError):
    """Raised before execution when a versioned query violates the policy."""


def parse_sql_file(path: Path) -> dict[str, str]:
    blocks: dict[str, list[str]] = {}
    current: str | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        marker = _BLOCK_MARKER.match(line)
        if marker:
            current = marker.group(1).lower()
            if current in blocks:
                raise UnsafeSqlError(f"duplicate_block:{path.name}:{current}")
            blocks[current] = []
        elif current is not None:
            blocks[current].append(line)
        elif line.strip() and not line.lstrip().startswith("--"):
            raise UnsafeSqlError(f"sql_without_named_block:{path.name}")

    parsed = {name: "\n".join(lines).strip() for name, lines in blocks.items()}
    if not parsed or any(not sql for sql in parsed.values()):
        raise UnsafeSqlError(f"empty_sql_block:{path.name}")
    return parsed


def validate_read_only_sql(sql: str) -> None:
    clean = re.sub(r"--[^\n]*", " ", sql)
    clean = re.sub(r"/\*.*?\*/", " ", clean, flags=re.S)
    first = re.match(r"^\s*([A-Z]+(?:\s+[A-Z]+\s+[A-Z]+)?)", clean, re.I)
    if not first:
        raise UnsafeSqlError("empty_statement")
    normalized = " ".join(first.group(1).upper().split())
    if not (normalized.startswith("SELECT") or normalized == "SHOW REPLICA STATUS"):
        raise UnsafeSqlError("statement_not_allowlisted_read_only")
    if _FORBIDDEN.search(clean):
        raise UnsafeSqlError("forbidden_sql_token")
    if _SELECT_STAR.search(clean):
        raise UnsafeSqlError("select_star_forbidden")
    semicolons = [index for index, char in enumerate(clean) if char == ";"]
    if len(semicolons) > 1 or (semicolons and clean[semicolons[0] + 1 :].strip()):
        raise UnsafeSqlError("multiple_statements_forbidden")


def render_numeric_parameters(sql: str, parameters: dict[str, int]) -> str:
    allowed_ranges = {
        "connections_limit": (1, 100),
        "workload_limit": (1, 100),
        "sessions_limit": (1, 100),
        "locks_limit": (1, 100),
        "tables_limit": (1, 100),
        "indexes_limit": (1, 100),
        "errors_limit": (1, 100),
        "large_table_bytes": (1, 2**63 - 1),
        "slow_log_lookback_minutes": (1, 1440),
        "slow_log_limit": (1, 100),
    }
    names = set(_PLACEHOLDER.findall(sql))
    if not names.issubset(allowed_ranges):
        raise UnsafeSqlError("unknown_numeric_parameter")
    rendered = sql
    for name in names:
        if name not in parameters or isinstance(parameters[name], bool):
            raise UnsafeSqlError(f"missing_numeric_parameter:{name}")
        value = parameters[name]
        low, high = allowed_ranges[name]
        if not isinstance(value, int) or not low <= value <= high:
            raise UnsafeSqlError(f"numeric_parameter_out_of_range:{name}")
        rendered = rendered.replace("{{" + name + "}}", str(value))
    if _PLACEHOLDER.search(rendered):
        raise UnsafeSqlError("unrendered_parameter")
    return rendered


def classify_database_error(error: Exception) -> tuple[SourceStatus, str]:
    code = getattr(error, "errno", None)
    if code is None and getattr(error, "args", None):
        code = error.args[0] if isinstance(error.args[0], int) else None
    class_name = type(error).__name__.lower()
    if code in _PERMISSION_CODES:
        return SourceStatus.NOT_AVAILABLE, "permission_denied"
    if code in _OBJECT_CODES:
        return SourceStatus.NOT_AVAILABLE, "source_or_column_not_available"
    if code in _TIMEOUT_CODES or "timeout" in class_name:
        return SourceStatus.DEGRADED, "query_timeout"
    if code is not None:
        return SourceStatus.ERROR, f"database_error_{code}"
    return SourceStatus.ERROR, "unexpected_database_error"


class DatabaseRunner:
    def __init__(self, connection: Any, sql_dir: Path, timeout_seconds: int) -> None:
        if connection is None:
            raise ValueError("existing_connection_required")
        if not 1 <= timeout_seconds <= 120:
            raise ValueError("query_timeout_seconds_out_of_range")
        self.connection = connection
        self.sql_dir = sql_dir.resolve()
        self.timeout_seconds = timeout_seconds

    @property
    def parallel_safe(self) -> bool:
        """Return whether independent cursors may execute concurrently."""
        return bool(getattr(self.connection, "parallel_safe", False))

    def _load(self, filename: str) -> dict[str, str]:
        if filename not in ALLOWED_SQL:
            raise UnsafeSqlError(f"sql_file_not_allowlisted:{filename}")
        path = (self.sql_dir / filename).resolve()
        if path.parent != self.sql_dir:
            raise UnsafeSqlError("sql_path_escape")
        blocks = parse_sql_file(path)
        if set(blocks) != set(ALLOWED_SQL[filename]):
            raise UnsafeSqlError(f"sql_block_allowlist_mismatch:{filename}")
        return blocks

    def validate_all(self, filenames: Iterable[str] | None = None) -> None:
        placeholder_values = {
            "connections_limit": 5,
            "workload_limit": 5,
            "sessions_limit": 5,
            "locks_limit": 5,
            "tables_limit": 5,
            "indexes_limit": 5,
            "errors_limit": 5,
            "large_table_bytes": 104857600,
            "slow_log_lookback_minutes": 60,
            "slow_log_limit": 25,
        }
        for filename in filenames or ALLOWED_SQL:
            for sql in self._load(filename).values():
                validate_read_only_sql(
                    render_numeric_parameters(sql, placeholder_values)
                )

    def execute_file(
        self,
        filename: str,
        parameters: dict[str, int],
        selected_blocks: Iterable[str] | None = None,
        *,
        parallel: bool = False,
    ) -> dict[str, QueryResult]:
        blocks = self._load(filename)
        selected = (
            list(selected_blocks) if selected_blocks is not None else list(blocks)
        )
        if not set(selected).issubset(ALLOWED_SQL[filename]):
            raise UnsafeSqlError(f"sql_block_not_allowlisted:{filename}")

        def execute_block(name: str) -> tuple[str, QueryResult]:
            result = self.execute_statement(
                render_numeric_parameters(blocks[name], parameters)
            )
            return name, result

        if parallel and self.parallel_safe and len(selected) > 1:
            with ThreadPoolExecutor(
                max_workers=len(selected),
                thread_name_prefix="health-query",
            ) as executor:
                return dict(executor.map(execute_block, selected))
        return dict(map(execute_block, selected))

    def execute_statement(self, sql: str) -> QueryResult:
        validate_read_only_sql(sql)
        executable = self._with_timeout_hint(sql)
        cursor = None
        started = time.monotonic()
        try:
            cursor = self.connection.cursor()
            cursor.execute(executable)
            rows: list[dict[str, Any]] = []
            if cursor.description:
                columns = [str(column[0]) for column in cursor.description]
                rows = [
                    dict(zip(columns, row, strict=True)) for row in cursor.fetchall()
                ]
            return QueryResult(
                status=SourceStatus.AVAILABLE,
                rows=rows,
                duration_ms=round((time.monotonic() - started) * 1000),
            )
        except Exception as error:  # Connector exceptions vary by implementation.
            status, reason = classify_database_error(error)
            return QueryResult(
                status=status,
                duration_ms=round((time.monotonic() - started) * 1000),
                reason=reason,
            )
        finally:
            if cursor is not None:
                try:
                    cursor.close()
                except Exception:
                    pass

    def _with_timeout_hint(self, sql: str) -> str:
        match = re.match(r"(\s*SELECT)\b", sql, re.I)
        if not match:
            return sql
        timeout_ms = self.timeout_seconds * 1000
        return (
            sql[: match.end()]
            + f" /*+ MAX_EXECUTION_TIME({timeout_ms}) */"
            + sql[match.end() :]
        )


def connection_tls_state(connection: Any) -> str:
    for attribute in ("ssl_active", "_ssl_active", "is_secure"):
        value = getattr(connection, attribute, None)
        if isinstance(value, bool):
            return "enabled" if value else "disabled"
    return "unknown"
