"""Type conversion and privacy-preserving row normalization."""

from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal
from typing import Any


def scalar(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def rows(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {str(key).lower(): scalar(value) for key, value in row.items()}
        for row in values
    ]


_LATENCY_UNITS_SECONDS = {
    "ps": 1e-12,
    "ns": 1e-9,
    "us": 1e-6,
    "ms": 1e-3,
    "s": 1.0,
    "min": 60.0,
    "h": 3600.0,
    "d": 86400.0,
}


def _sys_latency_seconds(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float, Decimal)):
        return float(value) / 1_000_000_000_000
    match = re.fullmatch(
        r"\s*([0-9]+(?:\.[0-9]+)?)\s*(ps|ns|us|ms|s|min|h|d)\s*", str(value), re.I
    )
    if not match:
        return None
    return round(
        float(match.group(1)) * _LATENCY_UNITS_SECONDS[match.group(2).lower()], 9
    )


def normalize_sys_rows(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    integer_fields = {
        "connection_id",
        "total_connections",
        "current_connections",
        "statements",
        "running_seconds",
        "rows_examined",
        "rows_sent",
        "rows_affected",
        "tmp_tables",
        "tmp_disk_tables",
    }
    output: list[dict[str, Any]] = []
    for source in rows(values):
        item: dict[str, Any] = {}
        for key, value in source.items():
            if key.endswith("_latency"):
                item[key + "_seconds"] = _sys_latency_seconds(value)
            elif key in integer_fields and value is not None:
                item[key] = as_int(value)
            else:
                item[key] = value
        output.append(item)
    return output


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return default


def numeric_rows(
    values: list[dict[str, Any]],
    integer_fields: set[str] | frozenset[str] = frozenset(),
    float_fields: set[str] | frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    output = rows(values)
    for row in output:
        for key in integer_fields:
            if key in row and row[key] is not None:
                row[key] = as_int(row[key])
        for key in float_fields:
            if key in row and row[key] is not None:
                row[key] = as_float(row[key])
    return output


def variable_map(values: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for row in rows(values):
        name = str(row.get("variable_name") or "").lower()
        if name:
            result[name] = row.get("variable_value")
    return result


def normalize_digest_text(value: Any) -> str | None:
    if value is None:
        return None
    normalized = re.sub(r"\s+", " ", str(value)).strip()
    return normalized[:4096]


def normalize_digest_rows(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    integer_fields = {
        "executions",
        "rows_examined",
        "rows_sent",
        "tmp_tables",
        "tmp_disk_tables",
        "sort_rows",
        "no_index_used",
        "no_good_index_used",
        "errors",
        "warnings",
        "heatwave_secondary_executions",
    }
    float_fields = {
        "total_latency_seconds",
        "avg_latency_seconds",
        "p95_latency_seconds",
    }
    normalized = numeric_rows(values, integer_fields, float_fields)
    allowed = {
        "schema_name",
        "digest",
        "digest_text",
        "executions",
        "total_latency_seconds",
        "avg_latency_seconds",
        "p95_latency_seconds",
        "rows_examined",
        "rows_sent",
        "tmp_tables",
        "tmp_disk_tables",
        "sort_rows",
        "no_index_used",
        "no_good_index_used",
        "errors",
        "warnings",
        "first_seen",
        "last_seen",
        "heatwave_secondary_executions",
    }
    output: list[dict[str, Any]] = []
    for row in normalized:
        item = {key: row.get(key) for key in allowed}
        item["digest_text"] = normalize_digest_text(item.get("digest_text"))
        output.append(item)
    return output


_INTEGER_LIMITS = {
    "tinyint": (127, 255),
    "smallint": (32767, 65535),
    "mediumint": (8388607, 16777215),
    "int": (2147483647, 4294967295),
    "integer": (2147483647, 4294967295),
    "bigint": (9223372036854775807, 18446744073709551615),
}


def auto_increment_rows(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in rows(values):
        data_type = str(row.get("data_type") or "").lower()
        column_type = str(row.get("column_type") or "").lower()
        current = as_int(row.get("auto_increment"), 0)
        limits = _INTEGER_LIMITS.get(data_type)
        maximum = limits[1 if "unsigned" in column_type else 0] if limits else None
        usage = round(100 * current / maximum, 6) if maximum and current >= 0 else None
        output.append(
            {
                "table_schema": row.get("table_schema"),
                "table_name": row.get("table_name"),
                "column_name": row.get("column_name"),
                "data_type": data_type or None,
                "unsigned": "unsigned" in column_type,
                "next_value": current,
                "max_value": maximum,
                "usage_pct": usage,
            }
        )
    return output


def safe_replication_rows(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    allowed = {
        "channel_name",
        "service_state",
        "last_error_number",
        "count_received_heartbeats",
        "member_id",
        "member_host",
        "member_port",
        "member_state",
        "member_role",
        "member_version",
        "replica_io_running",
        "replica_sql_running",
        "seconds_behind_source",
        "last_io_errno",
        "last_sql_errno",
    }
    normalized = numeric_rows(
        values,
        {
            "last_error_number",
            "count_received_heartbeats",
            "member_port",
            "seconds_behind_source",
            "last_io_errno",
            "last_sql_errno",
        },
    )
    safe = [
        {key: value for key, value in row.items() if key in allowed}
        for row in normalized
    ]
    for row in safe:
        if "member_host" in row:
            row["member_host"] = "masked"
    return safe
