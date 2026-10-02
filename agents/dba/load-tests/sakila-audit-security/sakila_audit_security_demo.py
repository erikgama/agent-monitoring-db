#!/usr/bin/env python3
"""Generate a bounded Sakila Audit Security demo with a restricted account."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT))

from agent_monitoring.config import database_settings  # noqa: E402

DATABASE_SETTINGS = database_settings("audit-lab")
LOGIN_FILE = DATABASE_SETTINGS.login_file
LOGIN_PATH = DATABASE_SETTINGS.login_path
EXPECTED_ACCOUNT = "sakila_audit_demo"
SCHEMA = "sakila"
FIXTURE_TABLE = "audit_security_demo_events"
TEMPORARY_TABLE = "audit_security_demo_temp"
CONFIRMATION = "AUDIT_SECURITY_SAKILA"
ALLOWED_DENIED_ERRORS = {1044, 1142, 1227}
ERROR_CODE = re.compile(r"\bERROR\s+(\d+)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Operation:
    name: str
    sql: str
    expected: str


@dataclass(frozen=True)
class OperationResult:
    round_number: int
    name: str
    expected: str
    status: str
    mysql_errno: int | None
    duration_seconds: float


def timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def mysql_environment() -> dict[str, str]:
    environment = DATABASE_SETTINGS.environment()
    environment["MYSQL_TEST_LOGIN_FILE"] = str(LOGIN_FILE)
    return environment


def mysql_command(sql: str) -> list[str]:
    return [
        DATABASE_SETTINGS.mysql_binary,
        f"--login-path={LOGIN_PATH}",
        *DATABASE_SETTINGS.tls_flags(),
        f"--database={SCHEMA}",
        "--batch",
        "--raw",
        "--skip-column-names",
        f"--execute={sql}",
    ]


def run_sql(sql: str, timeout_seconds: int = 20) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        mysql_command(sql),
        env=mysql_environment(),
        text=True,
        capture_output=True,
        timeout=timeout_seconds,
        check=False,
    )


def mysql_errno(stderr: str) -> int | None:
    match = ERROR_CODE.search(stderr)
    return int(match.group(1)) if match else None


def validate_grants(grants: Sequence[str]) -> None:
    normalized = "\n".join(grants).upper()
    forbidden = (
        "ALL PRIVILEGES",
        "GRANT OPTION",
        " DROP",
        " ALTER",
        "CREATE USER",
        "SYSTEM_USER",
        "SUPER",
    )
    if any(token in normalized for token in forbidden):
        raise RuntimeError("demo_account_has_forbidden_privileges")
    required = (
        "SELECT",
        "INSERT",
        "UPDATE",
        "DELETE",
        "CREATE TEMPORARY TABLES",
        f"`{SCHEMA}`.`{FIXTURE_TABLE}`".upper(),
    )
    if any(token not in normalized for token in required):
        raise RuntimeError("demo_account_missing_required_privileges")


def preflight() -> dict[str, str]:
    if LOGIN_PATH != "sakila-audit-demo":
        raise RuntimeError("demo_login_path_invalid")
    if not LOGIN_FILE.is_file():
        raise RuntimeError("demo_login_file_not_found")
    identity = run_sql(
        "SELECT DATABASE(), SUBSTRING_INDEX(CURRENT_USER(), '@', 1), "
        "CURRENT_ROLE(), @@version_comment;"
    )
    if identity.returncode != 0:
        raise RuntimeError("demo_identity_preflight_failed")
    fields = identity.stdout.strip().split("\t")
    if len(fields) != 4:
        raise RuntimeError("demo_identity_preflight_shape_invalid")
    database, account, current_role, version_comment = fields
    if database != SCHEMA:
        raise RuntimeError("demo_schema_invalid")
    if account != EXPECTED_ACCOUNT or account.lower() in {"admin", "root"}:
        raise RuntimeError("demo_account_invalid")
    if current_role not in {"NONE", "NONE@NONE"}:
        raise RuntimeError("demo_account_active_role_not_allowed")
    if (
        "cloud" not in version_comment.lower()
        and "heatwave" not in version_comment.lower()
    ):
        raise RuntimeError("demo_target_not_heatwave")

    fixture = run_sql(
        "SELECT COUNT(*) FROM information_schema.columns "
        f"WHERE table_schema = '{SCHEMA}' "
        f"AND table_name = '{FIXTURE_TABLE}' "
        "AND column_name IN ('run_id', 'event_type', 'payload', 'created_at');"
    )
    if fixture.returncode != 0 or fixture.stdout.strip() != "4":
        raise RuntimeError("demo_fixture_table_invalid_or_missing")

    grants = run_sql("SHOW GRANTS FOR CURRENT_USER();")
    if grants.returncode != 0:
        raise RuntimeError("demo_grants_preflight_failed")
    validate_grants(grants.stdout.splitlines())
    return {
        "schema": database,
        "account": account,
        "login_path": LOGIN_PATH,
        "fixture_table": f"{SCHEMA}.{FIXTURE_TABLE}",
    }


def build_operations(run_id: str, scenario: str = "all") -> list[Operation]:
    safe_run_id = str(uuid.UUID(run_id))
    fixture = f"`{SCHEMA}`.`{FIXTURE_TABLE}`"
    temporary = f"`{SCHEMA}`.`{TEMPORARY_TABLE}`"
    setup_operations = [
        Operation(
            "insert_fixture",
            f"INSERT INTO {fixture} (run_id, event_type, payload) "
            f"VALUES ('{safe_run_id}', 'insert', 'audit-security-demo');",
            "success",
        ),
        Operation(
            "update_fixture",
            f"UPDATE {fixture} SET event_type = 'updated' "
            f"WHERE run_id = '{safe_run_id}';",
            "success",
        ),
        Operation(
            "select_fixture",
            f"SELECT run_id, event_type FROM {fixture} WHERE run_id = '{safe_run_id}';",
            "success",
        ),
        Operation(
            "temporary_table_lifecycle",
            f"CREATE TEMPORARY TABLE {temporary} (id INT PRIMARY KEY); "
            f"INSERT INTO {temporary} (id) VALUES (1); "
            f"UPDATE {temporary} SET id = 2 WHERE id = 1; "
            f"DELETE FROM {temporary} WHERE id = 2; "
            f"DROP TEMPORARY TABLE {temporary};",
            "success",
        ),
        Operation(
            "delete_fixture",
            f"DELETE FROM {fixture} WHERE run_id = '{safe_run_id}';",
            "success",
        ),
    ]
    protected_operations = {
        "alter": Operation(
            "blocked_alter_fixture",
            f"ALTER TABLE {fixture} DROP COLUMN audit_security_forbidden_probe;",
            "permission_denied",
        ),
        "drop": Operation(
            "blocked_drop_fixture",
            f"DROP TABLE {fixture};",
            "permission_denied",
        ),
    }
    if scenario == "all":
        return [
            *setup_operations,
            protected_operations["alter"],
            protected_operations["drop"],
        ]
    if scenario in protected_operations:
        return [protected_operations[scenario]]
    raise ValueError("scenario_invalid")


def execute_operation(
    operation: Operation,
    round_number: int,
    timeout_seconds: int,
) -> OperationResult:
    started = time.monotonic()
    try:
        completed = run_sql(operation.sql, timeout_seconds)
    except (OSError, subprocess.TimeoutExpired):
        return OperationResult(
            round_number=round_number,
            name=operation.name,
            expected=operation.expected,
            status="transport_failure",
            mysql_errno=None,
            duration_seconds=round(time.monotonic() - started, 6),
        )
    elapsed = round(time.monotonic() - started, 6)
    error_code = mysql_errno(completed.stderr)
    if operation.expected == "success":
        status = "passed" if completed.returncode == 0 else "unexpected_failure"
    else:
        status = (
            "expected_permission_denied"
            if completed.returncode != 0 and error_code in ALLOWED_DENIED_ERRORS
            else "unsafe_unexpected_success"
            if completed.returncode == 0
            else "unexpected_failure"
        )
    return OperationResult(
        round_number=round_number,
        name=operation.name,
        expected=operation.expected,
        status=status,
        mysql_errno=error_code,
        duration_seconds=elapsed,
    )


def cleanup(run_id: str, timeout_seconds: int) -> bool:
    safe_run_id = str(uuid.UUID(run_id))
    try:
        completed = run_sql(
            f"DELETE FROM `{SCHEMA}`.`{FIXTURE_TABLE}` WHERE run_id = '{safe_run_id}';",
            timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def write_report(
    *,
    started_at: str,
    identity: dict[str, str],
    rounds: int,
    scenario: str,
    results: list[OperationResult],
    cleanup_ok: bool,
) -> tuple[Path, Path]:
    output = Path(__file__).resolve().parent / "reports"
    output.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().strftime("%Y-%m-%dT%H%M%S%z")
    json_path = output / f"sakila-audit-security-{stamp}.json"
    markdown_path = output / f"sakila-audit-security-{stamp}.md"
    failed = [
        result
        for result in results
        if result.status not in {"passed", "expected_permission_denied"}
    ]
    report = {
        "schema_version": "sakila_audit_security_demo.v1",
        "started_at": started_at,
        "finished_at": timestamp(),
        "target": identity,
        "configuration": {"rounds": rounds, "retries": 0, "scenario": scenario},
        "operations": [asdict(result) for result in results],
        "cleanup_ok": cleanup_ok,
        "status": "completed" if not failed and cleanup_ok else "failed",
        "safety": {
            "dedicated_non_admin_account_required": True,
            "persistent_ddl_expected": "permission_denied",
            "raw_sql_persisted": False,
            "credentials_persisted": False,
        },
    }
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    lines = [
        "# Sakila Audit Security demo",
        "",
        f"- Status: `{report['status']}`",
        f"- Início: `{report['started_at']}`",
        f"- Fim: `{report['finished_at']}`",
        f"- Conta restrita: `{identity['account']}`",
        f"- Rodadas: `{rounds}`",
        f"- Cenário: `{scenario}`",
        f"- Cleanup: `{'ok' if cleanup_ok else 'failed'}`",
        "",
        "| Rodada | Operação | Esperado | Resultado | MySQL errno |",
        "| ---: | --- | --- | --- | ---: |",
    ]
    lines.extend(
        f"| {item.round_number} | `{item.name}` | `{item.expected}` | "
        f"`{item.status}` | {item.mysql_errno or '-'} |"
        for item in results
    )
    lines.extend(
        [
            "",
            "O relatório não contém SQL literal, senha, host ou conteúdo "
            "do login-path.",
        ]
    )
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, markdown_path


def parse_arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm-target", choices=[SCHEMA])
    parser.add_argument("--confirm-user", choices=[EXPECTED_ACCOUNT])
    parser.add_argument("--confirm-demo", choices=[CONFIRMATION])
    parser.add_argument("--rounds", type=int, default=1)
    parser.add_argument("--scenario", choices=["all", "alter", "drop"], default="all")
    parser.add_argument("--timeout-seconds", type=int, default=20)
    arguments = parser.parse_args(argv)
    if not 1 <= arguments.rounds <= 10:
        parser.error("--rounds deve ficar entre 1 e 10")
    if not 1 <= arguments.timeout_seconds <= 120:
        parser.error("--timeout-seconds deve ficar entre 1 e 120")
    return arguments


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parse_arguments(argv)
    if not arguments.execute:
        print("dry-run: nenhum acesso ao banco foi realizado")
        print(f"login_path={LOGIN_PATH} account={EXPECTED_ACCOUNT} schema={SCHEMA}")
        print(
            "operations="
            + ",".join(
                item.name
                for item in build_operations(str(uuid.uuid4()), arguments.scenario)
            )
        )
        print(
            "para executar: --execute --confirm-target sakila "
            "--confirm-user sakila_audit_demo "
            "--confirm-demo AUDIT_SECURITY_SAKILA --rounds 1"
        )
        return 0
    if (
        arguments.confirm_target != SCHEMA
        or arguments.confirm_user != EXPECTED_ACCOUNT
        or arguments.confirm_demo != CONFIRMATION
    ):
        print("erro: confirmações explícitas ausentes", file=sys.stderr)
        return 2

    started_at = timestamp()
    try:
        identity = preflight()
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(
            f"erro: preflight bloqueou a execução: {type(error).__name__}",
            file=sys.stderr,
        )
        return 2

    results: list[OperationResult] = []
    cleanup_ok = True
    for round_number in range(1, arguments.rounds + 1):
        run_id = str(uuid.uuid4())
        try:
            for operation in build_operations(run_id, arguments.scenario):
                result = execute_operation(
                    operation,
                    round_number,
                    arguments.timeout_seconds,
                )
                results.append(result)
                print(
                    f"round={round_number} operation={result.name} "
                    f"status={result.status} mysql_errno={result.mysql_errno}",
                    flush=True,
                )
                if result.status not in {"passed", "expected_permission_denied"}:
                    break
        finally:
            if arguments.scenario == "all":
                cleanup_ok = cleanup(run_id, arguments.timeout_seconds) and cleanup_ok
        if results[-1].status not in {"passed", "expected_permission_denied"}:
            break

    json_path, markdown_path = write_report(
        started_at=started_at,
        identity=identity,
        rounds=arguments.rounds,
        scenario=arguments.scenario,
        results=results,
        cleanup_ok=cleanup_ok,
    )
    print(f"json={json_path}")
    print(f"markdown={markdown_path}")
    return (
        0
        if cleanup_ok
        and all(
            item.status in {"passed", "expected_permission_denied"} for item in results
        )
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
