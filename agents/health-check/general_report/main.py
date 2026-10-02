"""Build the latest read-only health snapshot."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from src.db import DatabaseRunner, connection_tls_state
from src.models import QueryResult, SourceStatus
from src.mysql_cli import MysqlCliConnection

from .cache import read_latest_snapshot as read_snapshot_file
from .cache import replace_latest_snapshot
from .capability_probe import capability_report, probe_capabilities
from .collectors import DOMAIN_ORDER, GENERAL_REPORT_SQL_FILES, read_domain
from .evaluator import DOMAIN_SCOPES, assess_domains, overall_status
from .renderer import render_snapshot_html

GENERAL_REPORT_DIR = Path(__file__).resolve().parent
ROOT_DIR = GENERAL_REPORT_DIR.parent
MAX_PARALLEL_DOMAINS = 8


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"config_must_be_object:{path.name}")
    return value


def _bounded_env_int(name: str, default: int, low: int, high: int) -> int:
    raw = os.environ.get(name)
    value = default if raw is None else int(raw)
    if not low <= value <= high:
        raise ValueError(f"{name.lower()}_out_of_range")
    return value


def _cache_dir() -> Path:
    configured = os.environ.get("HEALTHCHECK_CACHE_DIR")
    if not configured:
        return GENERAL_REPORT_DIR / "results"
    path = Path(configured)
    return path if path.is_absolute() else ROOT_DIR / path


def _logger() -> logging.Logger:
    logger = logging.getLogger("heatwave_healthcheck")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    log_dir = ROOT_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        log_dir / "collector.log",
        maxBytes=1_000_000,
        backupCount=2,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.propagate = False
    return logger


def _collection_settings() -> tuple[
    dict[str, Any], dict[str, int], dict[str, Any], int, dict[str, int]
]:
    runtime_policy = _load_json(ROOT_DIR / "policy.json")
    policy = _load_json(GENERAL_REPORT_DIR / "rules" / "report-rules.json")
    if runtime_policy.get("scope", {}).get("allowed_schemas") != ["sakila"]:
        raise ValueError("healthcheck_schema_policy_must_be_sakila_only")
    timeout = _bounded_env_int(
        "HEALTHCHECK_QUERY_TIMEOUT_SECONDS",
        int(runtime_policy["collection"]["query_timeout_seconds"]),
        1,
        120,
    )
    limit_names = (
        "connections_limit",
        "workload_limit",
        "sessions_limit",
        "locks_limit",
        "tables_limit",
        "indexes_limit",
        "errors_limit",
    )
    reporting = policy["report_limits"]
    report_limits = {
        name: _bounded_env_int(
            f"HEALTHCHECK_{name.upper()}", int(reporting[name]), 1, 100
        )
        for name in limit_names
    }
    parameters: dict[str, int] = {
        **report_limits,
        "large_table_bytes": int(policy["collection"]["large_table_bytes"]),
    }
    return (
        policy["assessment_thresholds"],
        report_limits,
        policy["data_retention"],
        timeout,
        parameters,
    )


def _read_domain_signals(
    connection: Any,
    *,
    timeout: int,
    parameters: dict[str, int],
    logger: logging.Logger,
    audit_id: str,
) -> tuple[dict[str, dict[str, QueryResult]], Any]:
    runner = DatabaseRunner(connection, GENERAL_REPORT_DIR / "sql", timeout)
    runner.validate_all(GENERAL_REPORT_SQL_FILES)
    capabilities = probe_capabilities(runner, parameters)

    def collect_domain(domain: str) -> tuple[str, dict[str, QueryResult]]:
        try:
            result = read_domain(domain, runner, capabilities, parameters)
        except Exception:
            result = {
                "collector": QueryResult(
                    status=SourceStatus.ERROR,
                    reason="collector_internal_error",
                )
            }
            logger.error("domain_failed audit_id=%s domain=%s", audit_id, domain)
        return domain, result

    if runner.parallel_safe:
        with ThreadPoolExecutor(
            max_workers=min(MAX_PARALLEL_DOMAINS, len(DOMAIN_ORDER)),
            thread_name_prefix="health-domain",
        ) as executor:
            signals = dict(executor.map(collect_domain, DOMAIN_ORDER))
    else:
        signals = dict(map(collect_domain, DOMAIN_ORDER))
    return signals, capabilities


def _close_connection(connection: Any, logger: logging.Logger, audit_id: str) -> None:
    try:
        connection.close()
    except Exception:
        logger.warning("connection_close_failed audit_id=%s", audit_id)


def _snapshot_payload(
    *,
    audit_id: str,
    started: datetime,
    report_limits: dict[str, int],
    tls_state: str,
    capabilities: Any,
    signals: dict[str, dict[str, QueryResult]],
    thresholds: dict[str, Any],
    data_retention: dict[str, Any],
    duration_ms: int,
) -> dict[str, Any]:
    instance, domains, findings = assess_domains(signals, thresholds)
    instance["mysql_version"] = capabilities.mysql_version
    finished = _utc_now()
    return {
        "schema_version": "1.0",
        "audit_id": audit_id,
        "started_at": _iso(started),
        "finished_at": _iso(finished),
        "collected_at": _iso(finished),
        "duration_ms": duration_ms,
        "target": {
            "engine": "MySQL",
            "service": "MySQL HeatWave",
            "mysql_version": capabilities.mysql_version,
        },
        "scope": {
            "schemas": ["sakila"],
            "notice": (
                "O relatório combina dados filtrados pelo schema sakila com "
                "métricas globais da instância. Somente domínios com kind=schema "
                "são exclusivos do sakila."
            ),
            "domain_scopes": {
                kind: [
                    name
                    for name, scope in DOMAIN_SCOPES.items()
                    if scope["kind"] == kind
                ]
                for kind in ("schema", "mixed", "instance")
            },
            "report_limits": report_limits,
        },
        "overall_status": overall_status(domains),
        "capabilities": capability_report(capabilities, tls_state),
        "instance": instance,
        "domains": domains,
        "findings": findings,
        "data_retention": data_retention,
    }


def build_health_snapshot(existing_connection: Any) -> dict[str, Any]:
    """Build one health snapshot from an injected authenticated connection."""
    started = _utc_now()
    monotonic_started = time.monotonic()
    audit_id = str(uuid.uuid4())
    logger = _logger()
    logger.info("audit_started audit_id=%s", audit_id)

    try:
        thresholds, report_limits, data_retention, timeout, parameters = (
            _collection_settings()
        )
        tls_state = connection_tls_state(existing_connection)
        signals, capabilities = _read_domain_signals(
            existing_connection,
            timeout=timeout,
            parameters=parameters,
            logger=logger,
            audit_id=audit_id,
        )
    finally:
        _close_connection(existing_connection, logger, audit_id)

    snapshot = _snapshot_payload(
        audit_id=audit_id,
        started=started,
        report_limits=report_limits,
        tls_state=tls_state,
        capabilities=capabilities,
        signals=signals,
        thresholds=thresholds,
        data_retention=data_retention,
        duration_ms=round((time.monotonic() - monotonic_started) * 1000),
    )
    html_document = render_snapshot_html(snapshot)
    replace_latest_snapshot(_cache_dir(), snapshot, html_document)
    logger.info(
        "audit_finished audit_id=%s overall_status=%s duration_ms=%s",
        audit_id,
        snapshot["overall_status"],
        snapshot["duration_ms"],
    )
    return snapshot


def read_latest_snapshot() -> dict[str, Any]:
    """Read and validate latest.json without any DB connection path."""
    return read_snapshot_file(_cache_dir())


def main() -> int:
    parser = argparse.ArgumentParser(description="Run or read the MySQL health-check")
    parser.add_argument(
        "command",
        choices=["collect", "read-latest", "read_latest_healthcheck"],
    )
    args = parser.parse_args()
    if args.command == "collect":
        policy = _load_json(ROOT_DIR / "policy.json")
        try:
            connection = MysqlCliConnection.from_environment(
                int(policy["collection"]["query_timeout_seconds"])
            )
            report = build_health_snapshot(connection)
        except (OSError, RuntimeError, ValueError) as error:
            print(
                f"healthcheck_collection_failed:{type(error).__name__}", file=sys.stderr
            )
            return 1
        json.dump(
            {
                "audit_id": report["audit_id"],
                "collected_at": report["collected_at"],
                "overall_status": report["overall_status"],
            },
            sys.stdout,
            ensure_ascii=False,
        )
        sys.stdout.write("\n")
        return 0
    if args.command in {"read-latest", "read_latest_healthcheck"}:
        json.dump(read_latest_snapshot(), sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
