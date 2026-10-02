#!/usr/bin/env python3
"""Executa o laboratório read-only do Audit Security."""

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

AUDIT_ROOT = REPOSITORY_ROOT / "agents" / "audit"
MCP_ROOT = REPOSITORY_ROOT / "mcp"
NOTIFICATION_ROOT = REPOSITORY_ROOT / "agents" / "notification"
NOTIFICATION_CONFIG = NOTIFICATION_ROOT / ".notification.local.env"
AUDIT_COLLECTOR = AUDIT_ROOT / ".venv" / "bin" / "mysql-audit-security"
AUDIT_ADVISOR = AUDIT_ROOT / ".venv" / "bin" / "mysql-audit-advisor"


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
    print_status("🔎", "Validando os pré-requisitos do Audit")
    require_file(AUDIT_COLLECTOR, "Coletor Audit", executable=True)
    require_file(AUDIT_ADVISOR, "Agente Luna", executable=True)
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


def audit_environment() -> dict[str, str]:
    environment = dict(os.environ)
    environment.pop("SMTP_PASSWORD", None)
    environment.update(load_notification_environment())
    environment.update(
        {
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
            "AUDIT_SECURITY_ALERTING_ENABLED": "true",
            "AUDIT_SECURITY_ENVIRONMENT": "production",
            "MCP_NOTIFICATION_ENABLED": os.environ.get(
                "AGENT_MONITORING_NOTIFY", "false"
            ),
            "MCP_DBA_ENABLED": "true",
            "NOTIFICATION_DELIVERY_ENABLED": os.environ.get(
                "AGENT_MONITORING_NOTIFY", "false"
            ),
        }
    )
    return environment


def print_process_line(name: str, line: str) -> None:
    timestamp = datetime.now().astimezone().strftime("%H:%M:%S")
    print(f"[{timestamp}] [{name}] {line}", flush=True)


def terminate_process_group(process: subprocess.Popen[str]) -> None:
    for selected_signal, pause_seconds in (
        (signal.SIGINT, 2.0),
        (signal.SIGTERM, 1.0),
        (signal.SIGKILL, 0.0),
    ):
        if process.poll() is not None:
            break
        try:
            os.killpg(process.pid, selected_signal)
        except (ProcessLookupError, PermissionError):
            break
        if pause_seconds:
            try:
                process.wait(timeout=pause_seconds)
            except subprocess.TimeoutExpired:
                continue
            break
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass


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
    environment: dict[str, str],
    ready_markers: tuple[str, ...],
) -> ManagedProcess:
    print_status("▶", f"Iniciando {name}")
    process = subprocess.Popen(
        command,
        cwd=AUDIT_ROOT,
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
        name=f"audit-lab-output-{name}",
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
            fail(f"{managed.name} encerrou inesperadamente: código {return_code}")
    fail(f"{managed.name} não ficou pronto em {timeout_seconds:g} segundos")


def stop_process(managed: ManagedProcess) -> None:
    if managed.process.poll() is None:
        print_status("■", f"Encerrando {managed.name}")
        terminate_process_group(managed.process)
    managed.reader.join(timeout=2)
    print_status("✓", f"{managed.name} encerrado")


def print_plan() -> None:
    print_status("ℹ", "Modo de conferência: nenhum processo foi iniciado")
    print("\nFluxo que será executado:")
    print("  1. iniciar o coletor contínuo e validar a primeira coleta")
    print("  2. iniciar o agente Luna usando a coleta atual como baseline")
    print("  3. analisar somente coletas novas a cada 15 segundos")
    print("  4. deixar o MCP ser iniciado somente pelo agente Luna")
    print("  5. permanecer ativo até Ctrl-C e encerrar tudo corretamente")
    print("\nO script não gera DDL, DML ou eventos sintéticos.")
    print("E-mail exige configuração local e AGENT_MONITORING_NOTIFY=true.")
    print("Para executar: python3 apps/lab-console/scripts/run-audit-lab.py --execute")


def execute_lab() -> int:
    environment = audit_environment()
    background: list[ManagedProcess] = []
    print_status("🧪", "Iniciando laboratório do Audit Security")
    print_status(
        "🔒", "Coleta somente leitura; entrega conforme configuração explícita"
    )
    try:
        monitor = start_process(
            "audit-collector",
            [str(AUDIT_COLLECTOR), "monitor", "--interval-seconds", "15"],
            environment=environment,
            ready_markers=('"status": "collected"',),
        )
        background.append(monitor)
        wait_until_ready(monitor, timeout_seconds=60)

        advisor = start_process(
            "audit-advisor",
            [
                str(AUDIT_ADVISOR),
                "--interval-seconds",
                "15",
                "--start-from-current",
            ],
            environment=environment,
            ready_markers=("waiting_for_new_collection", '"status": "analyzed"'),
        )
        background.append(advisor)
        wait_until_ready(advisor, timeout_seconds=60)

        print_status("ℹ", "MCP será acionado quando houver evento Audit elegível")
        print_status("ℹ", "Pressione Ctrl-C para encerrar")
        while True:
            for managed in background:
                return_code = managed.process.poll()
                if return_code is not None:
                    fail(
                        f"{managed.name} encerrou inesperadamente: código {return_code}"
                    )
            time.sleep(1)
    except KeyboardInterrupt:
        print_status("⚠", "Interrupção recebida; encerrando o Audit")
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
        help="confirma a execução read-only com entrega real habilitada",
    )
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    try:
        preflight()
        if not arguments.execute:
            print_plan()
            return 0
        return execute_lab()
    except LabError as error:
        print_status("✗", str(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
