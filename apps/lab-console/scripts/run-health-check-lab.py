#!/usr/bin/env python3
"""Executa o laboratório completo e read-only do Health Check."""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import NoReturn

from notification_config import NotificationConfigError
from notification_config import load_notification_environment as load_local_notification

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPOSITORY_ROOT))

from agent_monitoring.config import database_settings  # noqa: E402
from agent_monitoring.llm import llm_settings  # noqa: E402

HEALTH_CHECK_ROOT = REPOSITORY_ROOT / "agents" / "health-check"
MCP_ROOT = REPOSITORY_ROOT / "mcp"
NOTIFICATION_ROOT = REPOSITORY_ROOT / "agents" / "notification"
NOTIFICATION_CONFIG = NOTIFICATION_ROOT / ".notification.local.env"
LOAD_SCRIPT = (
    REPOSITORY_ROOT
    / "agents"
    / "dba"
    / "load-tests"
    / "sakila-read-only"
    / "sakila_read_demo_35.py"
)
HEALTH_LATENCY = HEALTH_CHECK_ROOT / ".venv" / "bin" / "mysql-health-latency"
HEALTH_PYTHON = HEALTH_CHECK_ROOT / ".venv" / "bin" / "python"


class LabError(RuntimeError):
    """Falha operacional segura do orquestrador."""


@dataclass
class ManagedProcess:
    name: str
    process: subprocess.Popen[str]
    reader: threading.Thread
    ready: threading.Event


def print_status(symbol: str, message: str) -> None:
    timestamp = datetime.now().astimezone().strftime("%H:%M:%S")
    print(f"[{timestamp}] {symbol} {message}", flush=True)


def fail(message: str) -> NoReturn:
    raise LabError(message)


def require_file(path: Path, label: str, *, executable: bool = False) -> None:
    if not path.is_file():
        fail(f"{label} não encontrado: {path}")
    if executable and not os.access(path, os.X_OK):
        fail(f"{label} não é executável: {path}")
    print_status("✓", f"{label}: {path}")


def preflight() -> None:
    print_status("🔎", "Validando os pré-requisitos do laboratório")
    require_file(HEALTH_LATENCY, "Monitor do Health Check", executable=True)
    require_file(HEALTH_PYTHON, "Python do Health Check", executable=True)
    require_file(LOAD_SCRIPT, "Carga read-only do DBA")
    require_file(MCP_ROOT / "pyproject.toml", "MCP central")
    require_file(NOTIFICATION_ROOT / "pyproject.toml", "Notification")
    if os.environ.get("AGENT_MONITORING_NOTIFY") == "true":
        require_file(NOTIFICATION_CONFIG, "Configuração local do Notification")

    for command in (
        "uv",
        database_settings("health-check").mysql_binary,
        llm_settings().provider,
    ):
        resolved = shutil.which(command)
        if resolved is None:
            fail(f"comando obrigatório não encontrado no PATH: {command}")
        print_status("✓", f"Comando {command}: {resolved}")


def load_notification_environment() -> dict[str, str]:
    if not NOTIFICATION_CONFIG.is_file():
        return {}
    try:
        selected = load_local_notification(NOTIFICATION_CONFIG)
    except NotificationConfigError as error:
        raise LabError(
            f"não foi possível carregar a configuração do Notification: {error}"
        ) from error
    print_status("✓", "Configuração não secreta do Notification carregada")
    return selected


def lab_environment() -> dict[str, str]:
    environment = dict(os.environ)
    for key in (
        "SMTP_HOST",
        "SMTP_PASSWORD",
        "SMTP_PORT",
        "SMTP_USERNAME",
        "SMTP_USE_STARTTLS",
    ):
        environment.pop(key, None)
    environment.update(load_notification_environment())
    environment.update(
        {
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
            "HEALTHCHECK_ALERTING_ENABLED": "true",
            "HEALTHCHECK_ENVIRONMENT": "production",
            "MCP_NOTIFICATION_ENABLED": os.environ.get(
                "AGENT_MONITORING_NOTIFY", "false"
            ),
            "MCP_DBA_ENABLED": "true",
            "NOTIFICATION_DELIVERY_ENABLED": os.environ.get(
                "AGENT_MONITORING_NOTIFY", "false"
            ),
            "NOTIFICATION_SMTP_KEYCHAIN_SERVICE": "mysqlconf-notification-smtp",
        }
    )
    return environment


def print_process_line(name: str, line: str) -> None:
    timestamp = datetime.now().astimezone().strftime("%H:%M:%S")
    print(f"[{timestamp}] [{name}] {line}", flush=True)


def read_output(
    name: str,
    process: subprocess.Popen[str],
    ready: threading.Event,
    ready_markers: tuple[str, ...],
) -> None:
    assert process.stdout is not None
    for raw_line in process.stdout:
        line = raw_line.rstrip()
        if not line:
            continue
        print_process_line(name, line)
        if any(marker in line for marker in ready_markers):
            ready.set()


def start_process(
    name: str,
    command: list[str],
    *,
    cwd: Path,
    environment: dict[str, str],
    ready_markers: tuple[str, ...] = (),
) -> ManagedProcess:
    print_status("▶", f"Iniciando {name}")
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        start_new_session=True,
    )
    ready = threading.Event()
    reader = threading.Thread(
        target=read_output,
        args=(name, process, ready, ready_markers),
        name=f"lab-output-{name}",
        daemon=True,
    )
    reader.start()
    return ManagedProcess(name, process, reader, ready)


def wait_until_ready(managed: ManagedProcess, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if managed.ready.wait(timeout=0.25):
            print_status("✓", f"{managed.name} está pronto")
            return
        return_code = managed.process.poll()
        if return_code is not None:
            fail(f"{managed.name} encerrou antes de ficar pronto: código {return_code}")
    fail(f"{managed.name} não ficou pronto em {timeout_seconds:g} segundos")


def terminate_process_group(process: subprocess.Popen[str]) -> None:
    for sig, timeout in (
        (signal.SIGINT, 8),
        (signal.SIGTERM, 4),
        (signal.SIGKILL, 2),
    ):
        if process.poll() is not None:
            break
        try:
            os.killpg(process.pid, sig)
        except (ProcessLookupError, PermissionError):
            break
        try:
            process.wait(timeout=timeout)
            break
        except subprocess.TimeoutExpired:
            continue


def stop_process(managed: ManagedProcess) -> None:
    process = managed.process
    if process.poll() is not None:
        managed.reader.join(timeout=1)
        print_status("•", f"{managed.name} já estava encerrado")
        return

    print_status("■", f"Encerrando {managed.name}")
    terminate_process_group(process)
    managed.reader.join(timeout=2)
    print_status("✓", f"{managed.name} encerrado")


def run_command(
    name: str,
    command: list[str],
    *,
    cwd: Path,
    environment: dict[str, str],
) -> int:
    print_status("▶", f"Executando {name}")
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        start_new_session=True,
    )
    try:
        assert process.stdout is not None
        for raw_line in process.stdout:
            line = raw_line.rstrip()
            if line:
                print_process_line(name, line)
        return_code = process.wait()
    except KeyboardInterrupt:
        terminate_process_group(process)
        raise
    if return_code == 0:
        print_status("✓", f"{name} concluído")
    else:
        print_status("✗", f"{name} falhou com código {return_code}")
    return return_code


def workload_command() -> list[str]:
    return [
        sys.executable,
        str(LOAD_SCRIPT),
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
        "30",
    ]


def print_plan(*, monitor_only: bool = False) -> None:
    print_status("ℹ", "Modo de conferência: nenhum processo foi iniciado")
    print("\nFluxo que será executado:")
    print("  1. validar capacidades de percentis em modo read-only")
    print("  2. iniciar o coletor contínuo de latência e HTML")
    print("  3. iniciar o agente Luna para ler o HTML e decidir")
    print("  4. coletar o relatório geral quando Luna decidir alertar")
    print("  5. deixar o MCP ser iniciado pelo Luna quando houver alerta")
    print("  6. avaliar o Slow Query Log a cada 30 segundos")
    print("  7. registrar no MCP queries conhecidas acima de 80 s")
    print("  8. deixar o pedido pendente até o Refactor ser ativado")
    if monitor_only:
        print("  9. manter os processos ativos até interrupção")
    else:
        print("  9. executar a carga read-only controlada no schema sakila")
        print(" 10. encerrar os processos")
    print("\nO Notification fará uma tentativa real de e-mail quando houver alerta.")
    suffix = " --monitor-only" if monitor_only else ""
    print(
        "Para executar de verdade: "
        "python3 apps/lab-console/scripts/run-health-check-lab.py "
        f"--execute{suffix}"
    )


def execute_lab(*, monitor_only: bool = False) -> int:
    environment = lab_environment()
    background: list[ManagedProcess] = []
    print_status(
        "🧪",
        "Iniciando coletor e agente do Health Check"
        if monitor_only
        else "Iniciando laboratório completo do Health Check",
    )
    print_status(
        "🔒",
        "Aguardando carga manual em sakila; entrega de e-mail habilitada"
        if monitor_only
        else "Carga read-only em sakila; entrega de e-mail habilitada",
    )

    try:
        capabilities_code = run_command(
            "capabilities",
            [str(HEALTH_LATENCY), "capabilities"],
            cwd=HEALTH_CHECK_ROOT,
            environment=environment,
        )
        if capabilities_code != 0:
            fail("as capacidades necessárias não estão disponíveis")

        monitor = start_process(
            "health-monitor",
            [str(HEALTH_LATENCY), "monitor"],
            cwd=HEALTH_CHECK_ROOT,
            environment=environment,
            ready_markers=("waiting_for_activity", '"status": "idle"'),
        )
        background.append(monitor)
        wait_until_ready(monitor, timeout_seconds=45)

        advisor = start_process(
            "health-advisor",
            [
                str(HEALTH_PYTHON),
                "-m",
                "advisor.agent",
                "--interval-seconds",
                "15",
            ],
            cwd=HEALTH_CHECK_ROOT,
            environment=environment,
            ready_markers=("waiting_for_new_collection", '"status": "analyzed"'),
        )
        background.append(advisor)
        # A primeira coleta real pode consumir quase toda a janela do monitor;
        # preserve tempo suficiente para o Luna concluir a análise seguinte.
        wait_until_ready(advisor, timeout_seconds=120)

        refactor_advisor = start_process(
            "health-refactor-advisor",
            [
                str(HEALTH_PYTHON),
                "-m",
                "refactor_collector.advisor",
                "monitor",
                "--interval-seconds",
                "30",
            ],
            cwd=HEALTH_CHECK_ROOT,
            environment=environment,
            ready_markers=('"status": "analyzed"',),
        )
        background.append(refactor_advisor)
        wait_until_ready(refactor_advisor, timeout_seconds=180)

        print_status(
            "ℹ",
            "MCP será acionado somente por decisões validadas dos agentes Luna",
        )
        if monitor_only:
            print_status(
                "✓",
                "Health Check ativo; aguardando carga manual ou interrupção",
            )
            while True:
                for managed in background:
                    return_code = managed.process.poll()
                    if return_code is not None:
                        fail(
                            f"{managed.name} encerrou inesperadamente: "
                            f"código {return_code}"
                        )
                time.sleep(1)

        print_status("⚠", "A carga oficial pode levar vários minutos")
        workload_code = run_command(
            "sakila-load",
            workload_command(),
            cwd=REPOSITORY_ROOT,
            environment=environment,
        )
        if workload_code != 0:
            fail(f"a carga controlada terminou com código {workload_code}")

        if monitor.process.poll() is not None:
            fail("o monitor encerrou inesperadamente durante a carga")
        if advisor.process.poll() is not None:
            fail("o agente Luna encerrou inesperadamente durante a carga")
        if refactor_advisor.process.poll() is not None:
            fail("o agente de candidatos a Refactor encerrou durante a carga")
        print_status("✅", "Laboratório concluído com sucesso")
        return 0
    except KeyboardInterrupt:
        print_status("⚠", "Interrupção recebida; encerrando o laboratório")
        return 130
    except LabError as error:
        print_status("✗", str(error))
        return 1
    finally:
        for managed in reversed(background):
            stop_process(managed)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="confirma a execução da carga read-only controlada",
    )
    parser.add_argument(
        "--monitor-only",
        action="store_true",
        help="mantém monitor e advisor ativos sem iniciar a carga",
    )
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    try:
        preflight()
        if not arguments.execute:
            print_plan(monitor_only=arguments.monitor_only)
            return 0
        return execute_lab(monitor_only=arguments.monitor_only)
    except LabError as error:
        print_status("✗", str(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
