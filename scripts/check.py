#!/usr/bin/env python3
"""Offline checks using fake databases/SMTP and an isolated demo runtime."""

from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UNIT_PROJECTS = [
    "agents/health-check",
    "agents/audit",
    "agents/refactor",
    "agents/notification",
    "agents/dba/health-check-alerts",
    "agents/dba/audit-security-alerts",
    "mcp",
]
PYTHON_PATHS = [
    "agent_monitoring",
    "scripts",
    "tests",
    "agents/health-check",
    "agents/audit",
    "agents/refactor",
    "agents/notification",
    "agents/dba",
    "apps/lab-console/scripts",
    "apps/lab-console/api/labconsole",
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-web", action="store_true")
    args = parser.parse_args()
    environment = dict(os.environ)
    environment.update(
        LAB_MODE="demo",
        MCP_NOTIFICATION_ENABLED="false",
        MCP_DBA_ENABLED="false",
        NOTIFICATION_DELIVERY_ENABLED="false",
        AGENT_MONITORING_CONFIG=str(ROOT / "config/agent-monitoring.example.toml"),
        AGENT_MONITORING_LLM_PROVIDER="codex",
    )
    for key in tuple(environment):
        if key.startswith(
            ("LAB_DATABASE_", "MYSQL_", "SMTP_", "HEALTHCHECK_", "AUDIT_SECURITY_")
        ):
            environment.pop(key, None)
    # These optional tests exercise the real stdio transport with synthetic
    # alerts, a temporary DBA inbox and delivery disabled, without SMTP/MySQL.
    environment["HEALTHCHECK_RUN_MCP_INTEGRATION"] = "1"
    commands = [
        (ROOT, ["uv", "run", "--locked", "ruff", "check", *PYTHON_PATHS]),
        (ROOT, ["uv", "run", "--locked", "ruff", "format", "--check", *PYTHON_PATHS]),
        (
            ROOT / "apps/lab-console/api",
            ["uv", "run", "--locked", "mypy", "labconsole"],
        ),
        (ROOT, ["uv", "run", "--locked", "pytest", "-q"]),
        *[
            (
                ROOT / project,
                [
                    "uv",
                    "run",
                    "--locked",
                    "python",
                    "-m",
                    "unittest",
                    "discover",
                    "-s",
                    "tests",
                    "-p",
                    "test*.py",
                ],
            )
            for project in UNIT_PROJECTS
            if (ROOT / project / "tests").is_dir()
        ],
        (
            ROOT / "agents/health-check",
            [
                "uv",
                "run",
                "--locked",
                "python",
                "-m",
                "unittest",
                "discover",
                "-s",
                "../dba/tests",
                "-p",
                "test*.py",
            ],
        ),
        (ROOT / "apps/lab-console/api", ["uv", "run", "--locked", "pytest", "-q"]),
    ]
    if not args.skip_web:
        commands += [
            (ROOT / "apps/lab-console/web", ["npm", "run", task])
            for task in ("lint", "typecheck", "build")
        ]
    for cwd, command in commands:
        print(f"Verificando {cwd.relative_to(ROOT)}…", flush=True)
        if subprocess.run(command, cwd=cwd, env=environment, check=False).returncode:
            return 1
    print("Verificações concluídas sem banco, SMTP ou chamada real ao LLM.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
