"""Deterministic threshold evaluation; no LLM or automatic remediation."""

from __future__ import annotations

from typing import Any

from src.models import DomainStatus, QueryResult, SourceStatus
from src.normalizer import (
    as_float,
    as_int,
    auto_increment_rows,
    normalize_digest_rows,
    normalize_sys_rows,
    numeric_rows,
    rows,
    safe_replication_rows,
    variable_map,
)

DOMAIN_SCOPES: dict[str, dict[str, Any]] = {
    "workload": {
        "kind": "schema",
        "schema": "sakila",
        "description": "Digests e agregados filtrados pelo schema sakila.",
    },
    "active_sessions": {
        "kind": "schema",
        "schema": "sakila",
        "description": "Sessões ativas filtradas pelo schema sakila.",
    },
    "schema_tables": {
        "kind": "schema",
        "schema": "sakila",
        "description": "Metadados de tabelas filtrados pelo schema sakila.",
    },
    "indexes": {
        "kind": "schema",
        "schema": "sakila",
        "description": "Metadados e uso de índices filtrados pelo schema sakila.",
    },
    "locks": {
        "kind": "mixed",
        "schema": "sakila",
        "description": (
            "Esperas atuais e esperas de tabela são filtradas por sakila; "
            "o contador acumulado de deadlocks é global da instância."
        ),
    },
    "connections": {
        "kind": "instance",
        "schema": None,
        "description": (
            "Conexões, usuários, hosts e abortos representam toda a instância."
        ),
    },
    "innodb": {
        "kind": "instance",
        "schema": None,
        "description": "Métricas globais do mecanismo InnoDB da instância.",
    },
    "errors": {
        "kind": "instance",
        "schema": None,
        "description": (
            "Contadores globais de erros; não são atribuíveis apenas ao sakila."
        ),
    },
    "replication": {
        "kind": "instance",
        "schema": None,
        "description": "Estado global de replicação da instância.",
    },
}


def finding(
    finding_id: str,
    severity: str,
    domain: str,
    title: str,
    evidence: dict[str, Any],
    source: str,
) -> dict[str, Any]:
    return {
        "id": finding_id,
        "severity": severity,
        "domain": domain,
        "title": title,
        "evidence": evidence,
        "collected_from": source,
        "action": None,
    }


def _source_metadata(results: dict[str, QueryResult]) -> dict[str, Any]:
    return {name: result.metadata() for name, result in results.items()}


def _duration(results: dict[str, QueryResult]) -> int:
    return sum(result.duration_ms for result in results.values())


def _available(results: dict[str, QueryResult], name: str) -> list[dict[str, Any]]:
    result = results.get(name)
    if result and result.status == SourceStatus.AVAILABLE:
        return result.rows
    return []


def _base_status(results: dict[str, QueryResult]) -> DomainStatus:
    statuses = {result.status for result in results.values()}
    if SourceStatus.AVAILABLE in statuses:
        return DomainStatus.HEALTHY
    if statuses == {SourceStatus.NOT_AVAILABLE}:
        return DomainStatus.NOT_AVAILABLE
    return DomainStatus.UNKNOWN


def _domain(
    results: dict[str, QueryResult],
    status: DomainStatus,
    metrics: dict[str, Any],
    findings: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "status": status.value,
        "metrics": metrics,
        "findings": findings,
        "sources": _source_metadata(results),
        "duration_ms": _duration(results),
    }


def evaluate_instance(
    results: dict[str, QueryResult],
) -> tuple[dict[str, Any], dict[str, Any]]:
    variables = variable_map(_available(results, "variables"))
    status_values = variable_map(_available(results, "status"))
    instance = {
        "scope": {
            "kind": "instance",
            "schema": None,
            "description": "Configuração e status globais da instância MySQL.",
        },
        "uptime_seconds": as_int(status_values.get("uptime"), 0),
        "performance_schema_enabled": str(
            variables.get("performance_schema", "")
        ).upper()
        in {"1", "ON", "YES", "TRUE"},
        "config": {
            "max_connections": as_int(variables.get("max_connections"), 0),
            "innodb_buffer_pool_size_bytes": as_int(
                variables.get("innodb_buffer_pool_size"), 0
            ),
            "innodb_redo_log_capacity_bytes": as_int(
                variables.get("innodb_redo_log_capacity"), 0
            ),
            "tmp_table_size_bytes": as_int(variables.get("tmp_table_size"), 0),
            "max_heap_table_size_bytes": as_int(
                variables.get("max_heap_table_size"), 0
            ),
        },
        "collection": {
            "status": _base_status(results).value,
            "sources": _source_metadata(results),
            "duration_ms": _duration(results),
        },
    }
    return instance, status_values


def evaluate_connections(
    results: dict[str, QueryResult],
    instance: dict[str, Any],
    global_status: dict[str, Any],
    thresholds: dict[str, Any],
) -> dict[str, Any]:
    max_connections = as_int(instance["config"].get("max_connections"), 0)
    connected = as_int(global_status.get("threads_connected"), 0)
    total_connections = as_int(global_status.get("connections"), 0)
    aborted = as_int(global_status.get("aborted_connects"), 0)
    base = (
        DomainStatus.HEALTHY
        if max_connections and global_status
        else _base_status(results)
    )
    usage = round(100 * connected / max_connections, 3) if max_connections else None
    aborted_pct = round(100 * aborted / max(total_connections, 1), 3)
    top_users = normalize_sys_rows(_available(results, "top_users"))
    top_hosts = normalize_sys_rows(_available(results, "top_hosts"))
    # A identificação de contas não é necessária para a análise de capacidade
    # e pode conter um principal no formato usuario@host. Persistimos somente
    # o marcador estável para manter a evidência técnica sem expor a conta.
    for user in top_users:
        if "user" in user:
            user["user"] = "masked"
    for host in top_hosts:
        if "host" in host:
            host["host"] = "masked"

    metrics = {
        "threads_connected": connected,
        "threads_running": as_int(global_status.get("threads_running"), 0),
        "threads_created": as_int(global_status.get("threads_created"), 0),
        "connections": total_connections,
        "aborted_connects": aborted,
        "aborted_clients": as_int(global_status.get("aborted_clients"), 0),
        "connection_usage_pct": usage,
        "aborted_connect_pct": aborted_pct,
        "top_users": top_users,
        "top_hosts": top_hosts,
    }
    items: list[dict[str, Any]] = []
    final = base
    if usage is not None and usage >= thresholds["critical_usage_pct"]:
        final = DomainStatus.CRITICAL
        items.append(
            finding(
                "connections.usage",
                "critical",
                "connections",
                "Uso crítico de conexões",
                {"usage_pct": usage},
                "performance_schema",
            )
        )
    elif usage is not None and usage >= thresholds["attention_usage_pct"]:
        final = DomainStatus.ATTENTION
        items.append(
            finding(
                "connections.usage",
                "attention",
                "connections",
                "Uso elevado de conexões",
                {"usage_pct": usage},
                "performance_schema",
            )
        )
    if aborted_pct >= thresholds["attention_aborted_connect_pct"]:
        if final == DomainStatus.HEALTHY:
            final = DomainStatus.ATTENTION
        items.append(
            finding(
                "connections.aborted",
                "attention",
                "connections",
                "Conexões abortadas acima do limite",
                {"aborted_connect_pct": aborted_pct},
                "performance_schema",
            )
        )
    return _domain(results, final, metrics, items)


def evaluate_workload(
    results: dict[str, QueryResult], thresholds: dict[str, Any]
) -> dict[str, Any]:
    digest_name = next(
        (name for name in results if name.startswith("top_digests_")), ""
    )
    digests = normalize_digest_rows(_available(results, digest_name))
    aggregate_rows = numeric_rows(
        _available(results, "aggregate"),
        {
            "statements",
            "tmp_disk_tables",
            "no_index_used",
            "no_good_index_used",
            "statement_errors",
            "statement_warnings",
        },
        {"total_latency_seconds"},
    )
    aggregate = aggregate_rows[0] if aggregate_rows else {}
    metrics = {
        "window": "since_last_reset_or_restart",
        "aggregate": aggregate,
        "top_digests": digests,
    }
    final = _base_status(results)
    items: list[dict[str, Any]] = []
    tmp_disk = as_int(aggregate.get("tmp_disk_tables"), 0)
    no_index = as_int(aggregate.get("no_index_used"), 0)
    if tmp_disk >= thresholds["attention_tmp_disk_tables"]:
        final = DomainStatus.ATTENTION if final == DomainStatus.HEALTHY else final
        items.append(
            finding(
                "workload.tmp_disk_tables",
                "attention",
                "workload",
                "Uso de tabelas temporárias em disco detectado",
                {"count": tmp_disk},
                "performance_schema",
            )
        )
    if no_index >= thresholds["attention_no_index_used"]:
        final = DomainStatus.ATTENTION if final == DomainStatus.HEALTHY else final
        items.append(
            finding(
                "workload.no_index",
                "attention",
                "workload",
                "Statements sem índice detectados",
                {"count": no_index},
                "performance_schema",
            )
        )
    recurring = thresholds["recurring_digest_min_executions"]
    for digest in digests:
        executions = as_int(digest.get("executions"), 0)
        p95 = digest.get("p95_latency_seconds")
        if p95 is None or executions < recurring:
            continue
        p95_value = as_float(p95)
        severity = None
        if p95_value >= thresholds["critical_p95_latency_seconds"]:
            severity = "critical"
            final = DomainStatus.CRITICAL
        elif p95_value >= thresholds["attention_p95_latency_seconds"]:
            severity = "attention"
            if final == DomainStatus.HEALTHY:
                final = DomainStatus.ATTENTION
        if severity:
            items.append(
                finding(
                    "workload.recurring_p95",
                    severity,
                    "workload",
                    "Latência p95 elevada em digest recorrente",
                    {
                        "digest": digest.get("digest"),
                        "executions": executions,
                        "p95_latency_seconds": p95_value,
                    },
                    "performance_schema",
                )
            )
    return _domain(results, final, metrics, items)


def evaluate_active_sessions(
    results: dict[str, QueryResult], thresholds: dict[str, Any]
) -> dict[str, Any]:
    active = normalize_sys_rows(
        next(
            (
                result.rows
                for result in results.values()
                if result.status == SourceStatus.AVAILABLE
            ),
            [],
        )
    )
    for session in active:
        for field in ("user", "host"):
            if field in session:
                session[field] = "masked"
    longest = max((as_int(row.get("running_seconds"), 0) for row in active), default=0)
    lock_waits = sum(
        1 for row in active if "lock" in str(row.get("state") or "").lower()
    )
    final = _base_status(results)
    items: list[dict[str, Any]] = []
    if longest >= thresholds["critical_long_running_seconds"] or (
        lock_waits and longest >= thresholds["attention_long_running_seconds"]
    ):
        final = DomainStatus.CRITICAL
        items.append(
            finding(
                "active_sessions.long_running",
                "critical",
                "active_sessions",
                "Sessão longa ou bloqueada em execução",
                {"longest_seconds": longest, "lock_wait_sessions": lock_waits},
                "sys_or_performance_schema",
            )
        )
    elif longest >= thresholds["attention_long_running_seconds"]:
        final = DomainStatus.ATTENTION
        items.append(
            finding(
                "active_sessions.long_running",
                "attention",
                "active_sessions",
                "Sessão longa em execução",
                {"longest_seconds": longest},
                "sys_or_performance_schema",
            )
        )
    return _domain(
        results,
        final,
        {"active_count": len(active), "longest_seconds": longest, "sessions": active},
        items,
    )


def evaluate_locks(
    results: dict[str, QueryResult], thresholds: dict[str, Any]
) -> dict[str, Any]:
    lock_waits = numeric_rows(
        _available(results, "lock_waits"),
        {
            "waiting_transaction_id",
            "waiting_thread_id",
            "blocking_transaction_id",
            "blocking_thread_id",
        },
    )
    deadlock_rows = numeric_rows(
        _available(results, "deadlocks"), {"error_number", "deadlocks"}
    )
    deadlocks = as_int(deadlock_rows[0].get("deadlocks"), 0) if deadlock_rows else None
    count = len(lock_waits)
    final = _base_status(results)
    items: list[dict[str, Any]] = []
    if count >= thresholds["critical_current_waits"]:
        final = DomainStatus.CRITICAL
        items.append(
            finding(
                "locks.current_waits",
                "critical",
                "locks",
                "Múltiplas esperas de lock atuais",
                {"count": count},
                "performance_schema",
            )
        )
    elif count >= thresholds["attention_current_waits"]:
        final = DomainStatus.ATTENTION
        items.append(
            finding(
                "locks.current_waits",
                "attention",
                "locks",
                "Espera de lock atual detectada",
                {"count": count},
                "performance_schema",
            )
        )
    if deadlocks and deadlocks > 0:
        if final == DomainStatus.HEALTHY:
            final = DomainStatus.ATTENTION
        items.append(
            finding(
                "locks.deadlocks",
                "attention",
                "locks",
                "Deadlocks acumulados detectados",
                {"count": deadlocks, "window": "since_last_reset_or_restart"},
                "performance_schema",
            )
        )
    metrics = {
        "current_wait_count": count,
        "current_waits": lock_waits,
        "deadlocks": deadlocks,
        "deadlock_window": "since_last_reset_or_restart",
        "table_lock_waits": numeric_rows(
            _available(results, "table_lock_waits"), {"wait_count", "max_wait_seconds"}
        ),
    }
    return _domain(results, final, metrics, items)


def evaluate_innodb(
    results: dict[str, QueryResult], thresholds: dict[str, Any]
) -> dict[str, Any]:
    status_values = variable_map(_available(results, "status"))
    requests = as_int(status_values.get("innodb_buffer_pool_read_requests"), 0)
    reads = as_int(status_values.get("innodb_buffer_pool_reads"), 0)
    hit_ratio = round(100 * (1 - reads / max(requests, 1)), 6) if requests else None
    log_waits = as_int(status_values.get("innodb_log_waits"), 0)
    metrics = {
        "buffer_pool_hit_ratio_pct": hit_ratio,
        "buffer_pool_read_requests": requests,
        "buffer_pool_reads": reads,
        "buffer_pool_free_pages": as_int(
            status_values.get("innodb_buffer_pool_pages_free"), 0
        ),
        "buffer_pool_wait_free": as_int(
            status_values.get("innodb_buffer_pool_wait_free"), 0
        ),
        "log_waits": log_waits,
        "row_lock_waits": as_int(status_values.get("innodb_row_lock_waits"), 0),
        "row_lock_wait_avg_ms": as_int(
            status_values.get("innodb_row_lock_time_avg"), 0
        ),
        "metrics": numeric_rows(
            _available(results, "metrics"), {"count", "count_reset"}
        ),
        "window": "since_last_reset_or_restart",
    }
    final = _base_status(results)
    items: list[dict[str, Any]] = []
    if (
        hit_ratio is not None
        and hit_ratio < thresholds["critical_buffer_pool_hit_ratio_pct_below"]
    ):
        final = DomainStatus.CRITICAL
        items.append(
            finding(
                "innodb.buffer_pool_hit_ratio",
                "critical",
                "innodb",
                "Buffer pool hit ratio crítico",
                {"hit_ratio_pct": hit_ratio},
                "performance_schema",
            )
        )
    elif (
        hit_ratio is not None
        and hit_ratio < thresholds["attention_buffer_pool_hit_ratio_pct_below"]
    ):
        final = DomainStatus.ATTENTION
        items.append(
            finding(
                "innodb.buffer_pool_hit_ratio",
                "attention",
                "innodb",
                "Buffer pool hit ratio abaixo do limite",
                {"hit_ratio_pct": hit_ratio},
                "performance_schema",
            )
        )
    if log_waits >= thresholds["attention_log_waits"]:
        if final == DomainStatus.HEALTHY:
            final = DomainStatus.ATTENTION
        items.append(
            finding(
                "innodb.log_waits",
                "attention",
                "innodb",
                "Esperas de redo log detectadas",
                {"count": log_waits},
                "performance_schema",
            )
        )
    return _domain(results, final, metrics, items)


def evaluate_schema_tables(
    results: dict[str, QueryResult], thresholds: dict[str, Any]
) -> dict[str, Any]:
    largest = numeric_rows(
        _available(results, "largest_tables"),
        {"estimated_rows", "data_bytes", "index_bytes", "free_bytes"},
        {"fragmentation_pct"},
    )
    auto_values = auto_increment_rows(_available(results, "auto_increment"))
    fragmented = [
        row
        for row in largest
        if as_float(row.get("fragmentation_pct"), 0)
        >= thresholds["attention_fragmentation_pct"]
    ]
    auto_risk = [
        row
        for row in auto_values
        if row.get("usage_pct") is not None
        and row["usage_pct"] >= thresholds["critical_auto_increment_usage_pct"]
    ]
    final = _base_status(results)
    items: list[dict[str, Any]] = []
    if fragmented:
        final = DomainStatus.ATTENTION if final == DomainStatus.HEALTHY else final
        items.append(
            finding(
                "tables.fragmentation",
                "attention",
                "schema_tables",
                "Fragmentação estimada acima do limite",
                {"table_count": len(fragmented)},
                "information_schema",
            )
        )
    if auto_risk:
        final = DomainStatus.CRITICAL
        items.append(
            finding(
                "tables.auto_increment",
                "critical",
                "schema_tables",
                "AUTO_INCREMENT próximo do limite do tipo",
                {"table_count": len(auto_risk)},
                "information_schema",
            )
        )
    metrics = {
        "schemas": numeric_rows(
            _available(results, "schema_summary"),
            {
                "table_count",
                "estimated_rows",
                "data_bytes",
                "index_bytes",
                "total_bytes",
            },
        ),
        "largest_tables": largest,
        "missing_primary_keys": rows(_available(results, "missing_primary_keys")),
        "auto_increment": auto_values,
    }
    return _domain(results, final, metrics, items)


def evaluate_indexes(results: dict[str, QueryResult]) -> dict[str, Any]:
    unused = rows(_available(results, "unused_indexes"))
    redundant = rows(_available(results, "redundant_indexes"))
    large_without = numeric_rows(
        _available(results, "large_without_secondary"), {"total_bytes"}
    )
    final = _base_status(results)
    items: list[dict[str, Any]] = []
    if unused:
        final = DomainStatus.ATTENTION if final == DomainStatus.HEALTHY else final
        items.append(
            finding(
                "indexes.unused",
                "attention",
                "indexes",
                "Índices sem uso observado",
                {"count": len(unused), "window": "since_last_reset_or_restart"},
                "sys",
            )
        )
    if redundant:
        final = DomainStatus.ATTENTION if final == DomainStatus.HEALTHY else final
        items.append(
            finding(
                "indexes.redundant",
                "attention",
                "indexes",
                "Possíveis índices redundantes observados",
                {"count": len(redundant)},
                "sys",
            )
        )
    if large_without:
        final = DomainStatus.ATTENTION if final == DomainStatus.HEALTHY else final
        items.append(
            finding(
                "indexes.large_without_secondary",
                "attention",
                "indexes",
                "Tabela grande sem índice secundário observada",
                {"count": len(large_without)},
                "information_schema",
            )
        )
    metrics = {
        "unused_indexes": unused,
        "redundant_indexes": redundant,
        "selectivity": numeric_rows(
            _available(results, "selectivity"),
            {"cardinality", "estimated_rows"},
            {"selectivity_pct"},
        ),
        "large_without_secondary": large_without,
    }
    return _domain(results, final, metrics, items)


def evaluate_errors(
    results: dict[str, QueryResult], thresholds: dict[str, Any]
) -> dict[str, Any]:
    error_rows = numeric_rows(
        _available(results, "errors"),
        {"error_number", "error_count", "handled_count"},
    )
    total = sum(as_int(row.get("error_count"), 0) for row in error_rows)
    deadlocks = [
        row for row in error_rows if as_int(row.get("error_number"), 0) == 1213
    ]
    lock_timeouts = [
        row for row in error_rows if as_int(row.get("error_number"), 0) == 1205
    ]
    final = _base_status(results)
    items: list[dict[str, Any]] = []
    if total >= thresholds["critical_error_count"]:
        final = DomainStatus.CRITICAL
        items.append(
            finding(
                "errors.accumulated",
                "critical",
                "errors",
                "Volume acumulado de erros acima do limite crítico",
                {"count": total, "window": "since_last_reset_or_restart"},
                "performance_schema",
            )
        )
    elif total >= thresholds["attention_error_count"]:
        final = DomainStatus.ATTENTION
        items.append(
            finding(
                "errors.accumulated",
                "attention",
                "errors",
                "Erros acumulados observados",
                {"count": total, "window": "since_last_reset_or_restart"},
                "performance_schema",
            )
        )
    metrics = {
        "total_error_count": total,
        "deadlocks": deadlocks,
        "lock_wait_timeouts": lock_timeouts,
        "top_errors": error_rows,
        "window": "since_last_reset_or_restart",
    }
    return _domain(results, final, metrics, items)


def evaluate_replication(results: dict[str, QueryResult]) -> dict[str, Any]:
    safe: dict[str, list[dict[str, Any]]] = {
        name: safe_replication_rows(result.rows)
        for name, result in results.items()
        if result.status == SourceStatus.AVAILABLE
    }
    show_rows = safe.get("replica_status", [])
    group_rows = safe.get("group_members", [])
    channel_rows = safe.get("connection_status", []) + safe.get("applier_status", [])
    active_group_rows = [
        row
        for row in group_rows
        if str(row.get("member_state") or "").upper() in {"ONLINE", "RECOVERING"}
    ]
    if active_group_rows:
        mode = "group"
    elif show_rows or channel_rows:
        mode = "async"
    elif any(result.status == SourceStatus.AVAILABLE for result in results.values()):
        mode = "not_detected"
    else:
        mode = "not_available"
    final = _base_status(results)
    if mode == "not_available":
        final = DomainStatus.NOT_AVAILABLE
    errors: list[dict[str, Any]] = []
    for row in show_rows + channel_rows:
        for key in ("last_error_number", "last_io_errno", "last_sql_errno"):
            number = as_int(row.get(key), 0)
            if number:
                errors.append({"source_field": key, "error_number": number})
    items: list[dict[str, Any]] = []
    if errors:
        final = DomainStatus.ATTENTION
        items.append(
            finding(
                "replication.errors",
                "attention",
                "replication",
                "Erros de replicação observados",
                {"count": len(errors)},
                "performance_schema_or_show",
            )
        )
    lag_values = [
        row.get("seconds_behind_source")
        for row in show_rows
        if row.get("seconds_behind_source") is not None
    ]
    lag = max((as_int(value) for value in lag_values), default=None)
    metrics = {
        "available": mode != "not_available",
        "mode": mode,
        "channels": show_rows + channel_rows,
        "member_states": group_rows,
        "lag_seconds": lag,
        "errors": errors,
    }
    return _domain(results, final, metrics, items)


def assess_domains(
    raw: dict[str, dict[str, QueryResult]],
    thresholds: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], list[dict[str, Any]]]:
    instance, global_status = evaluate_instance(raw["instance"])
    domains = {
        "connections": evaluate_connections(
            raw["connections"], instance, global_status, thresholds["connections"]
        ),
        "workload": evaluate_workload(raw["workload"], thresholds["workload"]),
        "active_sessions": evaluate_active_sessions(
            raw["active_sessions"], thresholds["active_sessions"]
        ),
        "locks": evaluate_locks(raw["locks"], thresholds["locks"]),
        "innodb": evaluate_innodb(raw["innodb"], thresholds["innodb"]),
        "schema_tables": evaluate_schema_tables(
            raw["schema_tables"], thresholds["tables"]
        ),
        "indexes": evaluate_indexes(raw["indexes"]),
        "errors": evaluate_errors(raw["errors"], thresholds["errors"]),
        "replication": evaluate_replication(raw["replication"]),
    }
    for name, domain in domains.items():
        domain["scope"] = dict(DOMAIN_SCOPES[name])
        for item in domain["findings"]:
            item["scope"] = dict(DOMAIN_SCOPES[name])
    findings = [item for domain in domains.values() for item in domain["findings"]]
    rank = {"critical": 0, "attention": 1}
    findings.sort(key=lambda item: (rank.get(item["severity"], 2), item["id"]))
    return instance, domains, findings


def overall_status(domains: dict[str, dict[str, Any]]) -> str:
    statuses = {name: value["status"] for name, value in domains.items()}
    if "critical" in statuses.values():
        return "critical"
    if "attention" in statuses.values():
        return "attention"
    if statuses.get("connections") in {"unknown", "not_available"} and statuses.get(
        "workload"
    ) in {"unknown", "not_available"}:
        return "unknown"
    if "unknown" in statuses.values():
        return "unknown"
    return "healthy"
