#!/usr/bin/env python3
"""Install a clone with locked dependencies; never access MySQL or send email."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECTS = [
    ".",
    "agents/health-check",
    "agents/audit",
    "agents/refactor",
    "agents/notification",
    "agents/dba/health-check-alerts",
    "agents/dba/audit-security-alerts",
    "mcp",
    "apps/lab-console/api",
]


def run(command: list[str], cwd: Path) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--python", default="3.11", help="uv Python version, default 3.11"
    )
    parser.add_argument("--skip-web", action="store_true")
    args = parser.parse_args()
    uv = shutil.which("uv")
    if not uv or (not args.skip_web and not shutil.which("npm")):
        print(
            "Instale uv e Node.js 22 LTS (com npm) antes do bootstrap.", file=sys.stderr
        )
        return 2
    try:
        for project in PROJECTS:
            print(f"Instalando {project}…", flush=True)
            run(
                [uv, "sync", "--locked", "--extra", "dev", "--python", args.python],
                ROOT / project,
            )
        if not args.skip_web:
            run(["npm", "ci"], ROOT / "apps/lab-console/web")
        run([uv, "run", "--locked", "agent-monitoring", "init"], ROOT)
        print("Instalação concluída. Próximos passos: README.md e docs/SETUP.md.")
        return 0
    except subprocess.CalledProcessError:
        print(
            "Instalação interrompida; corrija a dependência indicada "
            "e execute novamente.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
