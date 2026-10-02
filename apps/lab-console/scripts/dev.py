#!/usr/bin/env python3
"""Start the local Lab Console in isolated demo or integrated operational mode."""

import argparse
import os
import secrets
import signal
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--production",
        action="store_true",
        help="Serve the existing npm production build",
    )
    parser.add_argument(
        "--integrated",
        action="store_true",
        help="Start the loopback-only API with the real local runner",
    )
    parser.add_argument(
        "--allow-execute",
        action="store_true",
        help="Allow the integrated runner to execute allowlisted actions",
    )
    parser.add_argument(
        "--runtime",
        type=Path,
        help="Runtime state directory, separated by selected mode",
    )
    args = parser.parse_args()
    if args.allow_execute and not args.integrated:
        parser.error("--allow-execute requires --integrated")
    processes = []
    mode = "integrated" if args.integrated else "demo"
    runtime = args.runtime or ROOT / "runtime" / mode
    environment = dict(
        os.environ,
        LAB_MODE=mode,
        LAB_ORIGIN="http://localhost:3000",
        LAB_RUNTIME=str(runtime.resolve()),
    )
    if args.integrated:
        environment["LAB_RUNNER_KEY"] = environment.get(
            "LAB_RUNNER_KEY", secrets.token_urlsafe(32)
        )
    else:
        # A local demo must never inherit integrated control-plane settings.
        for key in ("LAB_DATABASE_URL", "LAB_USERS_JSON", "LAB_RUNNER_KEY"):
            environment.pop(key, None)

    def stop(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    try:
        commands = [
            (
                [
                    str(ROOT / "api/.venv/bin/uvicorn"),
                    "labconsole.app:create_app",
                    "--factory",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "8000",
                ],
                ROOT / "api",
            ),
        ]
        if args.integrated:
            repository = ROOT.parents[1]
            runner = [
                str(ROOT / "api/.venv/bin/lab-runner"),
                "--repository",
                str(repository),
                "--runtime",
                str((runtime / "runner").resolve()),
                "--url",
                "ws://127.0.0.1:8000/runner",
                "--actions",
                "health.lab,health.load,health.collect,audit.lab,audit.drop,audit.alter,"
                "refactor.lab,lab.three,notification.test,dba.actor_count,dba.film_count",
            ]
            if args.allow_execute:
                runner.append("--allow-execute")
            commands.append((runner, ROOT / "api"))
        commands.append(
            (["npm", "run", "start" if args.production else "dev"], ROOT / "web")
        )
        for command, cwd in commands:
            processes.append(
                subprocess.Popen(
                    command, cwd=cwd, env=environment, start_new_session=True
                )
            )
        label = (
            "INTEGRADO: runner real com execução allowlisted habilitada"
            if args.integrated and args.allow_execute
            else "INTEGRADO: runner real somente para conferência"
            if args.integrated
            else "DEMO: eventos fictícios e nenhum efeito externo"
        )
        print(
            f"\n{label}\nhttp://localhost:3000 · Ctrl-C encerra os processos.\n",
            flush=True,
        )
        while all(p.poll() is None for p in processes):
            try:
                processes[0].wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
    except KeyboardInterrupt:
        print("Encerrando Lab Console…", flush=True)
    finally:
        for process in reversed(processes):
            for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGKILL):
                if process.poll() is not None:
                    break
                try:
                    os.killpg(process.pid, sig)
                    process.wait(timeout=4)
                except ProcessLookupError:
                    break
                except subprocess.TimeoutExpired:
                    continue


if __name__ == "__main__":
    main()
