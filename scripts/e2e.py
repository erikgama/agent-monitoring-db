#!/usr/bin/env python3
"""Browser verification in a fresh demo, without operational processes."""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "apps/lab-console/web"


def free_port() -> int:
    with socket.socket() as connection:
        connection.bind(("127.0.0.1", 0))
        return connection.getsockname()[1]


def ready(url: str, processes: list[subprocess.Popen], timeout: int = 90) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if any(item.poll() is not None for item in processes):
            raise RuntimeError("demo_process_exited")
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            time.sleep(0.5)
    raise RuntimeError("demo_startup_timeout")


def main() -> int:
    api_port, web_port = free_port(), free_port()
    while api_port == web_port:
        web_port = free_port()
    build_directory = ".next-e2e-" + uuid.uuid4().hex
    environment = dict(os.environ)
    for key in tuple(environment):
        if key.startswith(("MYSQL_", "SMTP_", "LAB_", "NOTIFICATION_", "MCP_")):
            environment.pop(key, None)
    origin = f"http://127.0.0.1:{web_port}"
    environment.update(
        LAB_MODE="demo",
        LAB_ORIGIN=origin,
        LAB_API_URL=f"http://127.0.0.1:{api_port}",
        LAB_E2E_URL=origin,
        LAB_NEXT_DIST_DIR=build_directory,
        NEXT_TELEMETRY_DISABLED="1",
    )
    processes: list[subprocess.Popen] = []
    with tempfile.TemporaryDirectory(prefix="agent-monitoring-e2e-") as name:
        temporary = Path(name)
        environment["LAB_RUNTIME"] = str(temporary / "runtime")
        with (temporary / "demo.log").open("w") as log:
            try:
                commands = [
                    (
                        [
                            "uv",
                            "run",
                            "--locked",
                            "uvicorn",
                            "labconsole.app:create_app",
                            "--factory",
                            "--host",
                            "127.0.0.1",
                            "--port",
                            str(api_port),
                        ],
                        ROOT / "apps/lab-console/api",
                    ),
                    (["npm", "run", "dev", "--", "--port", str(web_port)], WEB),
                ]
                for command, cwd in commands:
                    processes.append(
                        subprocess.Popen(
                            command,
                            cwd=cwd,
                            env=environment,
                            stdout=log,
                            stderr=subprocess.STDOUT,
                            start_new_session=True,
                        )
                    )
                ready(origin, processes)
                return subprocess.run(
                    ["npm", "test"], cwd=WEB, env=environment, check=False
                ).returncode
            except RuntimeError as error:
                print(json.dumps({"status": "failed", "error_code": str(error)}))
                return 1
            finally:
                for process in reversed(processes):
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                        process.wait(timeout=10)
                    except ProcessLookupError:
                        pass
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=5)
                shutil.rmtree(WEB / build_directory, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
