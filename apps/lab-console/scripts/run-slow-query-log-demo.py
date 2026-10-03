#!/usr/bin/env python3
"""Executa continuamente a SELECT versionada que alimenta o Slow Query Log."""

from __future__ import annotations

import argparse
import json
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
HEALTH_CHECK_ROOT = REPOSITORY_ROOT / "agents" / "health-check"
sys.path.insert(0, str(REPOSITORY_ROOT))

from agent_monitoring.config import database_settings  # noqa: E402

QUERY_ID = "correlated_running_total"

sys.path.insert(0, str(HEALTH_CHECK_ROOT))

from refactor_collector.collect_query_tuning_snapshot import (  # noqa: E402
    load_query_catalog,
)


class SlowQueryDemoError(RuntimeError):
    """Falha operacional sanitizada da simulação."""


def _sanitize_error(value: str) -> str:
    compact = " ".join(value.split())[-500:]
    return (
        re.sub(
            r"(?i)(password|passwd|pwd)\s*[=:]\s*\S+",
            r"\1=[REDACTED]",
            compact,
        )
        or "mysql_client_error"
    )


def _connection_settings() -> tuple[Path, str]:
    settings = database_settings("workload")
    login_file = settings.login_file
    login_path = settings.login_path
    if not login_file.is_file():
        raise SlowQueryDemoError("approved_login_file_not_found")
    if not login_path or not login_path.replace("-", "").replace("_", "").isalnum():
        raise SlowQueryDemoError("invalid_login_path")
    return login_file.resolve(), login_path


def _mysql_environment(login_file: Path) -> dict[str, str]:
    environment = database_settings("workload").environment()
    environment["MYSQL_TEST_LOGIN_FILE"] = str(login_file)
    return environment


def _mysql_command(login_path: str, sql: str) -> list[str]:
    settings = database_settings("workload")
    return [
        settings.mysql_binary,
        f"--login-path={login_path}",
        *settings.tls_flags(),
        "--database=sakila",
        "--batch",
        "--raw",
        "--skip-column-names",
        f"--execute={sql}",
    ]


def _load_original_query() -> str:
    query = load_query_catalog()[QUERY_ID].strip().rstrip(";")
    if not query.upper().startswith("SELECT "):
        raise SlowQueryDemoError("versioned_query_must_be_select")
    if "{{" in query or "}}" in query:
        raise SlowQueryDemoError("versioned_query_has_unresolved_placeholder")
    forbidden = re.search(
        r"\b(?:INSERT|UPDATE|DELETE|REPLACE|ALTER|CREATE|DROP|TRUNCATE|"
        r"GRANT|REVOKE|CALL|LOAD|LOCK|UNLOCK|SET)\b",
        query,
        re.IGNORECASE,
    )
    if forbidden:
        raise SlowQueryDemoError("versioned_query_is_not_read_only")
    return query


def _preflight(login_file: Path, login_path: str) -> None:
    completed = subprocess.run(
        _mysql_command(
            login_path,
            "SELECT @@GLOBAL.slow_query_log, @@GLOBAL.log_output, "
            "@@GLOBAL.long_query_time, DATABASE()",
        ),
        env=_mysql_environment(login_file),
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    if completed.returncode != 0:
        raise SlowQueryDemoError(_sanitize_error(completed.stderr))
    fields = completed.stdout.strip().split("\t")
    if len(fields) != 4:
        raise SlowQueryDemoError("slow_log_preflight_unexpected_result")
    enabled, output, long_query_time, schema = fields
    if enabled != "1":
        raise SlowQueryDemoError("slow_query_log_disabled")
    if "TABLE" not in {item.strip().upper() for item in output.split(",")}:
        raise SlowQueryDemoError("slow_query_log_table_output_required")
    if schema != "sakila":
        raise SlowQueryDemoError("sakila_schema_required")
    print(
        json.dumps(
            {
                "status": "slow_query_log_ready",
                "query_id": QUERY_ID,
                "long_query_time_seconds": float(long_query_time),
            },
            sort_keys=True,
        ),
        flush=True,
    )


def _stop_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def execute_loop(*, pause_seconds: float) -> int:
    login_file, login_path = _connection_settings()
    query = _load_original_query()
    _preflight(login_file, login_path)
    stop = threading.Event()
    current: subprocess.Popen[str] | None = None

    def request_stop(_signum: int, _frame: object) -> None:
        stop.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    iteration = 0
    try:
        while not stop.is_set():
            iteration += 1
            started = time.monotonic()
            print(
                json.dumps(
                    {
                        "status": "slow_query_started",
                        "query_id": QUERY_ID,
                        "iteration": iteration,
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
            current = subprocess.Popen(
                _mysql_command(login_path, query),
                env=_mysql_environment(login_file),
                text=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            while current.poll() is None and not stop.wait(0.5):
                pass
            if stop.is_set():
                _stop_process(current)
                break
            stderr = current.stderr.read() if current.stderr is not None else ""
            if current.returncode != 0:
                raise SlowQueryDemoError(_sanitize_error(stderr))
            print(
                json.dumps(
                    {
                        "status": "slow_query_completed",
                        "query_id": QUERY_ID,
                        "iteration": iteration,
                        "elapsed_seconds": round(time.monotonic() - started, 3),
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
            current = None
            stop.wait(pause_seconds)
    finally:
        if current is not None:
            _stop_process(current)
    print(
        json.dumps(
            {"status": "slow_query_stopped", "query_id": QUERY_ID},
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm-target", choices=["sakila"])
    parser.add_argument("--confirm-demo", choices=["SLOW_LOG_REFACTOR_SAKILA"])
    parser.add_argument("--pause-seconds", type=float, default=1.0)
    arguments = parser.parse_args()
    if arguments.pause_seconds < 0:
        parser.error("pause-seconds deve ser maior ou igual a zero")
    return arguments


def main() -> int:
    arguments = parse_arguments()
    if not arguments.execute:
        print("dry-run: nenhuma consulta foi executada")
        return 0
    if (
        arguments.confirm_target != "sakila"
        or arguments.confirm_demo != "SLOW_LOG_REFACTOR_SAKILA"
    ):
        print("erro: confirmação explícita da simulação é obrigatória")
        return 2
    try:
        return execute_loop(pause_seconds=arguments.pause_seconds)
    except (OSError, SlowQueryDemoError, subprocess.SubprocessError) as error:
        print(f"erro: {_sanitize_error(str(error))}", flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
