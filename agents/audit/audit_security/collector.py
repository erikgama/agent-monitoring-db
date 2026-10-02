"""Read-only collection and normalization for Audit Security."""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

AUDIT_REPORT_DIR = Path(__file__).resolve().parent
BASE_DIR = AUDIT_REPORT_DIR.parent
SQL_DIR = AUDIT_REPORT_DIR / "sql" / "security_snapshot"

STATIC_QUERIES = {
    "instance_security": "00_instance_security.sql",
    "active_connections": "10_active_connections.sql",
    "accounts": "20_accounts.sql",
    "global_privileges": "30_global_privileges.sql",
    "roles": "40_roles.sql",
    "audit_configuration": "50_audit_configuration.sql",
}

AUDIT_DOMAINS = (
    "audit_connections",
    "audit_ddl",
    "audit_errors",
    "audit_sakila_data_access",
)

_DROP_DATABASE_SAKILA = re.compile(
    r"^\s*DROP\s+(?:DATABASE|SCHEMA)\s+(?:IF\s+EXISTS\s+)?`?sakila`?\s*;?\s*$",
    re.IGNORECASE,
)
_DROP_TABLE_SAKILA = re.compile(
    r"^\s*DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?`?sakila`?\s*\.",
    re.IGNORECASE,
)
_TRUNCATE_SAKILA = re.compile(
    r"^\s*TRUNCATE(?:\s+TABLE)?\s+`?sakila`?\s*\.",
    re.IGNORECASE,
)
_ALTER_TABLE_SAKILA = re.compile(
    r"^\s*ALTER\s+TABLE\s+`?sakila`?\s*\.",
    re.IGNORECASE,
)


class QueryClient(Protocol):
    def query(self, sql: str) -> list[dict[str, Any]]: ...


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso8601(value: datetime) -> str:
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def fingerprint(value: Any) -> str | None:
    if value in (None, ""):
        return None
    digest = hashlib.sha256(str(value).encode()).hexdigest()[:16]
    return f"sha256:{digest}"


def _event_key(event: dict[str, Any]) -> str:
    canonical = json.dumps(
        event,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    return f"sha256:{digest}"


def _integer(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _audit_timestamp_utc(value: Any) -> Any:
    """Normalize MySQL Audit timestamps, which are UTC but may omit the suffix."""
    if not isinstance(value, str) or not value.strip():
        return value
    normalized = value.strip()
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError:
        return normalized
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _scope(database: Any) -> str:
    if database == "sakila":
        return "sakila"
    if database in (None, ""):
        return "[none]"
    return "[out_of_scope]"


def _sakila_ddl_scope(general: dict[str, Any]) -> str | None:
    if general.get("db") == "sakila":
        return "structured_database"
    query = general.get("query")
    if not isinstance(query, str):
        return None
    if _DROP_DATABASE_SAKILA.match(query):
        return "exact_drop_database_target"
    if _DROP_TABLE_SAKILA.match(query):
        return "exact_qualified_table_target"
    if _TRUNCATE_SAKILA.match(query):
        return "exact_qualified_table_target"
    if _ALTER_TABLE_SAKILA.match(query):
        return "exact_qualified_table_target"
    return None


def _domain(
    rows: list[dict[str, Any]],
    *,
    duration_ms: int,
    source: str,
    truncated: bool = False,
) -> dict[str, Any]:
    return {
        "status": "available",
        "duration_ms": duration_ms,
        "row_count_total": len(rows),
        "row_count_returned": len(rows),
        "truncated": truncated,
        "rows": rows,
        "source": source,
    }


def _unavailable(source: str, error: Exception) -> dict[str, Any]:
    return {
        "status": "unavailable",
        "duration_ms": 0,
        "row_count_total": 0,
        "row_count_returned": 0,
        "truncated": False,
        "rows": [],
        "source": source,
        "error": {
            "type": type(error).__name__,
            "mysql_errno": getattr(error, "errno", None),
            "message": "collection_source_unavailable",
        },
    }


def parse_audit_payload(raw: Any) -> tuple[list[dict[str, Any]], bool]:
    if not isinstance(raw, str):
        raise ValueError("audit_payload_missing")
    payload = json.loads(raw)
    if not isinstance(payload, list):
        raise ValueError("audit_payload_not_array")
    complete = bool(payload) and payload[-1] is None
    events = [item for item in payload if isinstance(item, dict)]
    return events, not complete


def parse_audit_pages(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    if not rows:
        raise ValueError("audit_payload_result_invalid")
    events: list[dict[str, Any]] = []
    complete = False
    for row in sorted(rows, key=lambda item: int(item.get("page_index", 0))):
        page_events, page_truncated = parse_audit_payload(row.get("audit_payload"))
        events.extend(page_events)
        if not page_truncated:
            complete = True
            break
    return events, not complete


def build_audit_query(window_minutes: int, max_events: int) -> str:
    template = _read_query("60_audit_events.sql")
    page_sizes: list[int] = []
    remaining = max_events
    while remaining > 0:
        page_size = min(500, remaining)
        page_sizes.append(page_size)
        remaining -= page_size

    statements = [
        "SET @audit_page_0 := audit_log_read(JSON_OBJECT("
        "'start', JSON_OBJECT('timestamp', "
        "DATE_FORMAT(@audit_start_utc, '%Y-%m-%d %H:%i:%s')), "
        f"'max_array_length', {page_sizes[0]}));",
        "SET @audit_done := JSON_TYPE(JSON_EXTRACT("
        "@audit_page_0, '$[last]')) = 'NULL';",
    ]
    for index, page_size in enumerate(page_sizes[1:], start=1):
        statements.extend(
            [
                f"SET @audit_page_{index} := IF(@audit_done, JSON_ARRAY(NULL), "
                "audit_log_read(JSON_OBJECT("
                f"'max_array_length', {page_size})));",
                "SET @audit_done := @audit_done OR JSON_TYPE(JSON_EXTRACT("
                f"@audit_page_{index}, '$[last]')) = 'NULL';",
            ]
        )
    selects = [
        "SELECT 0 AS page_index, "
        "DATE_FORMAT(@audit_start_utc, '%Y-%m-%dT%H:%i:%sZ') "
        "AS requested_start_utc, "
        "CONVERT(@audit_page_0 USING utf8mb4) AS audit_payload"
    ]
    for index in range(1, len(page_sizes)):
        selects.append(
            f"SELECT {index} AS page_index, "
            "DATE_FORMAT(@audit_start_utc, '%Y-%m-%dT%H:%i:%sZ') "
            "AS requested_start_utc, "
            f"CONVERT(@audit_page_{index} USING utf8mb4) AS audit_payload"
        )
    replacements = {
        "{{WINDOW_MINUTES}}": str(window_minutes),
        "{{PAGE_STATEMENTS}}": "\n".join(statements),
        "{{PAGE_SELECT}}": "\nUNION ALL\n".join(selects) + "\nORDER BY page_index;",
    }
    for marker, value in replacements.items():
        template = template.replace(marker, value)
    return template


def audit_rows(domain: str, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for event in events:
        event_class = event.get("class")
        event_name = event.get("event")
        account = event.get("account") or {}
        login = event.get("login") or {}
        connection = event.get("connection_data") or {}
        general = event.get("general_data") or {}
        table = event.get("table_access_data") or {}
        common = {
            "occurred_at_utc": _audit_timestamp_utc(event.get("timestamp")),
            "event_id": _integer(event.get("id")),
            "event_key": _event_key(event),
            "actor_id": fingerprint(account.get("user")),
            "source_id": fingerprint(login.get("ip")),
            "session_id": fingerprint(event.get("connection_id")),
        }
        if domain == "audit_connections" and event_class == "connection":
            status = _integer(connection.get("status"))
            rows.append(
                {
                    **common,
                    "connection_event": event_name,
                    "outcome": (
                        "success"
                        if status == 0
                        else "unknown"
                        if status is None
                        else "failure"
                    ),
                    "status_code": status,
                    "connection_type": connection.get("connection_type"),
                    "default_schema_scope": _scope(connection.get("db")),
                }
            )
        elif domain == "audit_errors" and event_class in {"connection", "general"}:
            status = _integer(
                connection.get("status")
                if event_class == "connection"
                else general.get("status")
            )
            if status in (None, 0):
                continue
            if event_class == "general" and general.get("db") != "sakila":
                continue
            query = general.get("query")
            rows.append(
                {
                    **common,
                    "event_class": event_class,
                    "event_name": event_name,
                    "mysql_error_code": status,
                    "sql_command": general.get("sql_command"),
                    "schema_scope": (
                        "instance_connection"
                        if event_class == "connection"
                        else "sakila"
                    ),
                    "sql_fingerprint": fingerprint(query),
                    "sql_length": len(query) if isinstance(query, str) else None,
                }
            )
        elif (
            domain == "audit_ddl"
            and event_class == "general"
            and event_name == "status"
            and (scope_evidence := _sakila_ddl_scope(general)) is not None
        ):
            command = str(general.get("sql_command") or "").lower()
            if not (
                command.startswith(("alter_", "create_", "drop_"))
                or command in {"truncate", "rename_table"}
            ):
                continue
            status = _integer(general.get("status"))
            query = general.get("query")
            rows.append(
                {
                    **common,
                    "sql_command": command,
                    "outcome": (
                        "success"
                        if status == 0
                        else "unknown"
                        if status is None
                        else "failure"
                    ),
                    "status_code": status,
                    "schema_scope": "sakila",
                    "scope_evidence": scope_evidence,
                    "sql_fingerprint": fingerprint(query),
                    "sql_length": len(query) if isinstance(query, str) else None,
                }
            )
        elif (
            domain == "audit_sakila_data_access"
            and event_class == "table_access"
            and table.get("db") == "sakila"
        ):
            table_event = table.get("event") or event_name
            command = str(table.get("sql_command") or "").lower()
            if table_event not in {"read", "insert", "update", "delete"}:
                continue
            query = table.get("query")
            rows.append(
                {
                    **common,
                    "operation": (
                        "SELECT"
                        if table_event == "read" and command == "select"
                        else str(table_event).upper()
                    ),
                    "sql_command": command,
                    "object_schema": "sakila",
                    "object_table": table.get("table"),
                    "outcome": "unknown_not_provided_by_table_access",
                    "sql_fingerprint": fingerprint(query),
                    "sql_length": len(query) if isinstance(query, str) else None,
                }
            )
    return sorted(
        rows,
        key=lambda row: (
            str(row.get("occurred_at_utc") or ""),
            row.get("event_id") or -1,
        ),
        reverse=True,
    )


def _read_query(filename: str) -> str:
    return (SQL_DIR / filename).read_text(encoding="utf-8")


def collect_snapshot(
    client: QueryClient,
    *,
    window_minutes: int,
    max_events: int,
    max_rows: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not 1 <= window_minutes <= 1440:
        raise ValueError("window_minutes_out_of_range")
    if not 1 <= max_events <= 50_000:
        raise ValueError("max_events_out_of_range")
    if not 1 <= max_rows <= 1000:
        raise ValueError("max_rows_out_of_range")

    started = utc_now()
    monotonic_started = time.monotonic()
    audit_id = str(uuid.uuid4())
    domains: dict[str, dict[str, Any]] = {}

    for name, filename in STATIC_QUERIES.items():
        query_started = time.monotonic()
        try:
            all_rows = client.query(_read_query(filename))
            rows = all_rows[:max_rows]
            domain = _domain(
                rows,
                duration_ms=round((time.monotonic() - query_started) * 1000),
                source=name,
                truncated=len(all_rows) > len(rows),
            )
            domain["row_count_total"] = len(all_rows)
            domain["query_file"] = filename
            domains[name] = domain
        except Exception as error:
            domains[name] = _unavailable(name, error)
            domains[name]["query_file"] = filename

    audit_query = build_audit_query(window_minutes, max_events)
    audit_started = time.monotonic()
    try:
        payload_rows = client.query(audit_query)
        events, audit_truncated = parse_audit_pages(payload_rows)
        audit_duration = round((time.monotonic() - audit_started) * 1000)
        for name in AUDIT_DOMAINS:
            all_rows = audit_rows(name, events)
            rows = all_rows[:max_rows]
            domain = _domain(
                rows,
                duration_ms=audit_duration,
                source=name,
                truncated=audit_truncated or len(all_rows) > len(rows),
            )
            domain["row_count_total"] = len(all_rows)
            domain["raw_events_examined"] = len(events)
            domain["requested_start_utc"] = payload_rows[0].get("requested_start_utc")
            domain["query_file"] = "60_audit_events.sql"
            domains[name] = domain
    except Exception as error:
        for name in AUDIT_DOMAINS:
            domains[name] = _unavailable(name, error)
            domains[name]["query_file"] = "60_audit_events.sql"

    data_quality = {
        "unavailable_domains": [
            name
            for name, domain in domains.items()
            if domain["status"] == "unavailable"
        ],
        "truncated_domains": [
            name for name, domain in domains.items() if domain.get("truncated")
        ],
    }
    finished = utc_now()
    overall = (
        "partial"
        if data_quality["unavailable_domains"] or data_quality["truncated_domains"]
        else "complete"
    )
    report = {
        "schema_version": "mysql_audit_security_snapshot.v1",
        "audit_id": audit_id,
        "started_at": iso8601(started),
        "finished_at": iso8601(finished),
        "collected_at": iso8601(finished),
        "duration_ms": round((time.monotonic() - monotonic_started) * 1000),
        "target": {"engine": "MySQL", "service": "MySQL HeatWave"},
        "scope": {
            "functional_schemas": ["sakila"],
            "audit_window_minutes": window_minutes,
            "max_audit_events": max_events,
            "max_rows_per_domain": max_rows,
        },
        "collector": {
            "name": "audit-security",
            "mode": "read_only",
            "identity_handling": "sha256_truncated_16",
            "raw_sql_persisted": False,
        },
        "overall_status": overall,
        "data_quality": data_quality,
        "policy_evaluation": {
            "status": "delegated_to_agent",
            "decision_owner": "audit-luna",
            "rules_source": "audit_security/advisor/rules.md",
        },
        "domains": domains,
    }
    return report
