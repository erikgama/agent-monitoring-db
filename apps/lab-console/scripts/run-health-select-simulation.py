#!/usr/bin/env python3
"""Executa a carga de latência e a geração contínua do Slow Query Log."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
HEALTH_PYTHON = REPOSITORY_ROOT / "agents" / "health-check" / ".venv/bin/python"
LATENCY_LOAD = (
    REPOSITORY_ROOT
    / "agents"
    / "dba"
    / "load-tests"
    / "sakila-read-only"
    / "sakila_read_demo_35.py"
)
LOW_LOAD = (
    REPOSITORY_ROOT
    / "agents"
    / "dba"
    / "load-tests"
    / "sakila-read-only"
    / "sakila_read_steady.py"
)
SLOW_QUERY_LOAD = Path(__file__).with_name("run-slow-query-log-demo.py")
WARMUP_SECONDS = 60
WARMUP_TPS = 1.0


class SelectSimulationError(RuntimeError):
    """Falha operacional da simulação composta."""


@dataclass
class Child:
    name: str
    process: subprocess.Popen[str]
    reader: threading.Thread


def _latency_command() -> list[str]:
    return [
        sys.executable,
        str(LATENCY_LOAD),
        "--execute",
        "--confirm-target",
        "sakila",
        "--confirm-demo",
        "LATENCY_35_SAKILA",
        "--warmup-queries",
        "10",
        "--measurement-seconds",
        "300",
        "--calibration-seconds",
        "20",
        "--baseline-tps",
        "10",
        "--initial-loaded-tps",
        "15",
        "--minimum-loaded-tps",
        "10",
        "--maximum-loaded-tps",
        "15",
        "--target-increase-percent",
        "35",
        "--minimum-accepted-increase-percent",
        "35",
        "--maximum-accepted-increase-percent",
        "50",
        "--calibration-attempts",
        "4",
        "--official-attempts",
        "6",
        "--minimum-completed",
        "300",
        "--progress-interval-seconds",
        "3",
    ]


def _warmup_command() -> list[str]:
    """Build the one-minute, intentionally low read-only warm-up."""
    return [
        sys.executable,
        str(LOW_LOAD),
        "--execute",
        "--confirm-target",
        "sakila",
        "--confirm-read-only",
        "READ_STEADY_SAKILA",
        "--target-tps",
        str(WARMUP_TPS),
        "--duration-seconds",
        str(WARMUP_SECONDS),
        "--workers",
        "1",
        "--queue-size",
        "16",
        "--query-timeout-seconds",
        "30",
        "--max-retries",
        "2",
        "--progress-interval-seconds",
        "3",
        "--drain-queue",
    ]


def _slow_query_command() -> list[str]:
    return [
        str(HEALTH_PYTHON),
        str(SLOW_QUERY_LOAD),
        "--execute",
        "--confirm-target",
        "sakila",
        "--confirm-demo",
        "SLOW_LOG_REFACTOR_SAKILA",
    ]


def _read_output(name: str, process: subprocess.Popen[str]) -> None:
    assert process.stdout is not None
    for line in process.stdout:
        if line.strip():
            print(f"[{name}] {line.rstrip()}", flush=True)


def _start(name: str, command: list[str], environment: dict[str, str]) -> Child:
    process = subprocess.Popen(
        command,
        cwd=REPOSITORY_ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    reader = threading.Thread(
        target=_read_output,
        args=(name, process),
        name=f"select-simulation-{name}",
        daemon=True,
    )
    reader.start()
    return Child(name=name, process=process, reader=reader)


def _stop(child: Child) -> None:
    if child.process.poll() is None:
        child.process.terminate()
        try:
            child.process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            child.process.kill()
            child.process.wait(timeout=5)
    child.reader.join(timeout=2)


def _preflight() -> None:
    for path in (HEALTH_PYTHON, LOW_LOAD, LATENCY_LOAD, SLOW_QUERY_LOAD):
        if not path.is_file():
            raise SelectSimulationError(f"required_file_missing:{path.name}")


def execute() -> int:
    _preflight()
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONUNBUFFERED"] = "1"
    stop = threading.Event()

    def request_stop(_signum: int, _frame: object) -> None:
        stop.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    children: list[Child] = []
    latency_completed = False
    try:
        warmup = _start("warmup", _warmup_command(), environment)
        children.append(warmup)
        print(
            json.dumps(
                {
                    "duration_seconds": WARMUP_SECONDS,
                    "status": "warming_up_low_load",
                    "tps": WARMUP_TPS,
                    "workers": 1,
                },
                sort_keys=True,
            ),
            flush=True,
        )
        while not stop.wait(0.5):
            warmup_code = warmup.process.poll()
            if warmup_code is None:
                continue
            warmup.reader.join(timeout=2)
            if warmup_code != 0:
                raise SelectSimulationError(f"warmup_failed:{warmup_code}")
            break
        if stop.is_set():
            return 0

        slow_query = _start("slow-query-log", _slow_query_command(), environment)
        latency = _start("latency-load", _latency_command(), environment)
        children.extend((slow_query, latency))
        print(
            json.dumps(
                {
                    "components": ["latency_load", "slow_query_log"],
                    "status": "parallel_load_running_until_cancelled",
                },
                sort_keys=True,
            ),
            flush=True,
        )
        while not stop.wait(0.5):
            slow_code = slow_query.process.poll()
            if slow_code is not None:
                raise SelectSimulationError(
                    f"slow_query_log_stopped_unexpectedly:{slow_code}"
                )
            if not latency_completed:
                latency_code = latency.process.poll()
                if latency_code is not None:
                    latency.reader.join(timeout=2)
                    if latency_code != 0:
                        raise SelectSimulationError(
                            f"latency_load_failed:{latency_code}"
                        )
                    latency_completed = True
                    print(
                        json.dumps(
                            {
                                "status": "latency_load_completed",
                                "slow_query_log": "running",
                            },
                            sort_keys=True,
                        ),
                        flush=True,
                    )
    finally:
        for child in reversed(children):
            _stop(child)
    print(json.dumps({"status": "simulation_stopped"}), flush=True)
    return 0


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm-target", choices=["sakila"])
    parser.add_argument("--confirm-demo", choices=["HEALTH_SELECT_SIMULATION"])
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    if not arguments.execute:
        print("dry-run: nenhuma simulação foi iniciada")
        return 0
    if (
        arguments.confirm_target != "sakila"
        or arguments.confirm_demo != "HEALTH_SELECT_SIMULATION"
    ):
        print("erro: confirmação explícita da simulação é obrigatória")
        return 2
    try:
        return execute()
    except (OSError, SelectSimulationError, subprocess.SubprocessError) as error:
        print(f"erro: {error}", flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
