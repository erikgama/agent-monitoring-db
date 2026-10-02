"""Setup and verification commands that do not touch the monitored database."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .config import (
    EXAMPLE_CONFIG,
    ROOT,
    ConfigurationError,
    config_path,
    database_settings,
)
from .llm import LlmError, llm_settings, run_analysis


def initialize() -> None:
    path = config_path()
    if path.exists():
        print("Configuração central existente preservada.")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        stream.write(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
    print(f"Configuração criada: {path}")


def configure_clients(clients: list[str]) -> None:
    uv = shutil.which("uv")
    if uv is None:
        raise ConfigurationError("uv_not_found")
    command = {
        "command": uv,
        "args": [
            "run",
            "--locked",
            "--directory",
            str(ROOT / "mcp"),
            "agent-monitoring-mcp",
        ],
    }
    for client in clients:
        if client == "codex":
            path = ROOT / ".codex" / "config.toml"
            # JSON strings are also valid TOML basic strings for these paths.
            value = "[mcp_servers.agent_monitoring]\n"
            value += (
                f"command = {json.dumps(uv)}\nargs = {json.dumps(command['args'])}\n"
            )
            value += "startup_timeout_sec = 60\ntool_timeout_sec = 120\n"
        else:
            path = ROOT / (".mcp.json" if client == "claude" else ".kimi-code/mcp.json")
            value = (
                json.dumps(
                    {"mcpServers": {"agent-monitoring": command}},
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n"
            )
        if path.exists():
            print(f"Configuração de {client} existente preservada: {path}")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as stream:
            stream.write(value)
        print(f"MCP de {client} configurado: {path}")


def doctor() -> int:
    settings = database_settings("health-check")
    llm = llm_settings()
    checks = {
        "central_config": config_path().is_file(),
        "uv": shutil.which("uv") is not None,
        "node": shutil.which("node") is not None,
        "npm": shutil.which("npm") is not None,
        "mysql": shutil.which(settings.mysql_binary) is not None,
        "mysql_config_editor": shutil.which("mysql_config_editor") is not None,
        "llm_cli": shutil.which(llm.provider) is not None,
        "mysql_login_file_exists": settings.login_file.is_file(),
    }
    print(
        json.dumps(
            {
                "provider": llm.provider,
                "checks": checks,
                "database_accessed": False,
                "credentials_read": False,
            },
            indent=2,
        )
    )
    print(
        "Login do LLM e disponibilidade do modelo: use llm-check. "
        "O doctor não os confirma."
    )
    return 0 if all(checks.values()) else 1


def llm_check() -> int:
    schema = {
        "type": "object",
        "properties": {"status": {"type": "string", "const": "ok"}},
        "required": ["status"],
        "additionalProperties": False,
    }
    with tempfile.TemporaryDirectory(prefix="monitoring-llm-check-") as name:
        path = Path(name) / "schema.json"
        path.write_text(json.dumps(schema), encoding="utf-8")
        result = json.loads(
            run_analysis(
                'Return {"status":"ok"}. Do not use tools.',
                schema=path,
                timeout_seconds=90,
            )
        )
    print(
        json.dumps(
            {
                "provider": llm_settings().provider,
                "status": result["status"],
                "database_accessed": False,
            }
        )
    )
    return 0


def database_check(role: str) -> int:
    """Check the selected profile using one bounded read-only transaction."""
    settings = database_settings(role)
    if not settings.login_file.is_file():
        raise ConfigurationError("approved_login_file_not_found")
    executable = shutil.which(settings.mysql_binary)
    if executable is None:
        raise ConfigurationError("mysql_not_found")
    sql = (
        "START TRANSACTION READ ONLY; "
        "SELECT @@port, DATABASE(); "
        "SELECT 1 FROM information_schema.schemata WHERE schema_name=DATABASE(); "
        "SHOW SESSION STATUS LIKE 'Ssl_cipher'; ROLLBACK;"
    )
    try:
        completed = subprocess.run(
            [
                executable,
                f"--login-path={settings.login_path}",
                "--protocol=TCP",
                *settings.tls_flags(),
                f"--database={settings.database}",
                "--connect-timeout=5",
                "--batch",
                "--skip-column-names",
                "--execute",
                sql,
            ],
            env=settings.environment(),
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise ConfigurationError("database_check_timeout") from error
    if completed.returncode:
        raise ConfigurationError("database_check_failed")
    rows = completed.stdout.strip().splitlines()
    if (
        len(rows) != 3
        or rows[0] != f"{settings.expected_port}\t{settings.database}"
        or rows[1] != "1"
    ):
        raise ConfigurationError("database_target_mismatch")
    if not rows[2].startswith("Ssl_cipher\t") or not rows[2].split("\t", 1)[1]:
        raise ConfigurationError("database_tls_not_active")
    print(
        json.dumps(
            {
                "status": "ok",
                "role": role,
                "database": settings.database,
                "read_only": True,
                "tls": True,
                "port_matches": True,
            }
        )
    )
    return 0


def configure_database(args: argparse.Namespace) -> int:
    settings = database_settings(args.role)
    executable = shutil.which("mysql_config_editor")
    if executable is None:
        raise ConfigurationError("mysql_config_editor_not_found")
    # The password is entered only in the MySQL client's own terminal prompt.
    settings.login_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    completed = subprocess.run(
        [
            executable,
            "set",
            f"--login-path={settings.login_path}",
            f"--host={args.host}",
            f"--port={args.port}",
            f"--user={args.user}",
            "--password",
        ],
        env=settings.environment(),
        check=False,
    )
    return completed.returncode


def main() -> int:
    parser = argparse.ArgumentParser(
        description="agent-monitoring: configuração central e verificações locais"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "init", help="Cria a configuração local sem sobrescrever arquivos"
    )
    commands.add_parser(
        "doctor",
        help="Verifica dependências e referências, sem acessar banco ou credenciais",
    )
    commands.add_parser(
        "llm-check",
        help="Faz uma pequena chamada real ao LLM configurado, sem acessar o banco",
    )
    connection = commands.add_parser(
        "db-check", help="Verifica conexão TLS e schema somente leitura"
    )
    connection.add_argument(
        "--role",
        choices=["health-check", "audit", "dba", "refactor", "workload", "audit-lab"],
        default="health-check",
    )
    clients = commands.add_parser(
        "configure-clients", help="Gera configurações locais MCP para este clone"
    )
    clients.add_argument(
        "--client", action="append", choices=["codex", "claude", "kimi"]
    )
    database = commands.add_parser(
        "configure-db",
        help="Abre o mysql_config_editor; a senha é digitada diretamente no cliente",
    )
    database.add_argument(
        "--role",
        choices=["health-check", "refactor", "workload", "audit-lab"],
        default="health-check",
    )
    database.add_argument("--host", required=True)
    database.add_argument("--user", required=True)
    database.add_argument("--port", type=int, default=3306)
    args = parser.parse_args()
    try:
        if args.command == "init":
            initialize()
        elif args.command == "configure-clients":
            configure_clients(args.client or ["codex", "claude", "kimi"])
        elif args.command == "doctor":
            return doctor()
        elif args.command == "llm-check":
            return llm_check()
        elif args.command == "db-check":
            return database_check(args.role)
        else:
            return configure_database(args)
        return 0
    except (ConfigurationError, LlmError, OSError) as error:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error_code": str(error)
                    if isinstance(error, (ConfigurationError, LlmError))
                    else "local_io_error",
                }
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
