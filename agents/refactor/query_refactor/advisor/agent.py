"""Validate versioned SQL refactors in sakila_dev and return them to the DBA."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from agent_monitoring.llm import LlmError, llm_settings, run_analysis
from jsonschema import Draft202012Validator, FormatChecker

from ..mcp_publisher import McpRefactorResultPublisher

REFACTOR_ROOT = Path(__file__).resolve().parents[2]
FLOW_ROOT = REFACTOR_ROOT / "query_refactor"
REPOSITORY_ROOT = REFACTOR_ROOT.parents[1]
INBOX = FLOW_ROOT / "advisor" / "results"
MYSQL_WRAPPER = FLOW_ROOT / "mysql_client.py"
RESULT_SCHEMA = REFACTOR_ROOT / "contracts" / "query_refactor_result.v1.schema.json"
PROPOSAL_SCHEMA = (
    REFACTOR_ROOT / "contracts" / "refactor_advisor_proposal.v1.schema.json"
)
RULES_PATH = FLOW_ROOT / "advisor" / "rules.md"
INTERVAL_SECONDS = 30.0
MODEL = "gpt-5.6-sol"
REASONING_EFFORT = "medium"
MODEL_TIMEOUT_SECONDS = 180
CATALOG_RELATIVE = Path(
    "agents/health-check/refactor_collector/bad_queries_with_llm_refactor.sql"
)

_QUERY_MARKER = re.compile(r"/\*\s*query:\s*([a-z0-9_]+)\s*\*/", re.IGNORECASE)
_MAX_EXECUTION_HINT = re.compile(
    r"/\*\+\s*MAX_EXECUTION_TIME\([^)]*\)\s*\*/", re.IGNORECASE
)

_BLOCKED_SQL = re.compile(
    r"\b(?:alter|analyze|benchmark|call|create|delete|do|drop|grant|insert|kill|"
    r"load|lock|optimize|rename|repair|replace|revoke|set|sleep|truncate|unlock|"
    r"update)\b|\binto\s+(?:outfile|dumpfile)\b",
    re.IGNORECASE,
)
_BLOCKED_SCHEMA = re.compile(
    r"(?:`?(?:information_schema|mysql|performance_schema|sakila|sys)`?)\s*\.",
    re.IGNORECASE,
)
_SQL_LITERAL = re.compile(
    r"'(?:''|\\.|[^'\\])*'|\"(?:\"\"|\\.|[^\"\\])*\"|"
    r"\b0x[0-9a-f]+\b|(?<![\w$])[-+]?\d+(?:\.\d+)?(?:e[-+]?\d+)?",
    re.IGNORECASE,
)
_RUNTIME_CLAIM = re.compile(
    r"requires_dba_validation|approved_lab|"
    r"(?:ainda\s+)?(?:não\s+)?(?:foi\s+)?(?:executad[ao]\s+)?valida(?:ção|da)|"
    r"(?:ainda\s+)?requer\s+valida(?:ção|r)|status\s+(?:permanece|final)",
    re.IGNORECASE,
)


class RefactorAdvisorError(RuntimeError):
    pass


class Publisher(Protocol):
    def publish(self, result: dict[str, Any]) -> dict[str, Any]: ...


class Analyzer(Protocol):
    def propose(self, request: dict[str, Any]) -> dict[str, Any]: ...


@dataclass(frozen=True)
class QueryExecution:
    exit_code: int
    seconds: float
    header: str | None
    row_count: int
    result_sha256: str | None
    error_code: str | None = None


class Executor(Protocol):
    def execute(self, sql: str, timeout_seconds: float) -> QueryExecution: ...


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    _atomic_text(
        path,
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError("request_must_be_object")
    return value


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sql_without_literals_or_comments(sql: str) -> str:
    """Remove strings and comments before applying the local SQL deny-list."""
    output: list[str] = []
    index = 0
    quote: str | None = None
    while index < len(sql):
        character = sql[index]
        following = sql[index + 1] if index + 1 < len(sql) else ""
        if quote:
            if character == "\\" and index + 1 < len(sql):
                output.extend((" ", " "))
                index += 2
                continue
            if character == quote:
                if following == quote:
                    output.extend((" ", " "))
                    index += 2
                    continue
                quote = None
            output.append(" ")
            index += 1
            continue
        if character in {"'", '"'}:
            quote = character
            output.append(" ")
            index += 1
            continue
        if character == "#" or (character == "-" and following == "-"):
            newline = sql.find("\n", index)
            if newline == -1:
                output.extend(" " * (len(sql) - index))
                break
            output.extend(" " * (newline - index))
            index = newline
            continue
        if character == "/" and following == "*":
            end = sql.find("*/", index + 2)
            if end == -1:
                raise RefactorAdvisorError("proposal_unclosed_comment")
            output.extend(" " * (end + 2 - index))
            index = end + 2
            continue
        output.append(character)
        index += 1
    if quote:
        raise RefactorAdvisorError("proposal_unclosed_string")
    return "".join(output)


def validate_read_only_proposal(proposed_sql: str, original_sql: str) -> str:
    proposed = proposed_sql.strip()
    if not proposed:
        raise RefactorAdvisorError("proposal_empty")
    inspected = _sql_without_literals_or_comments(proposed)
    statements = [part.strip() for part in inspected.split(";") if part.strip()]
    if len(statements) != 1:
        raise RefactorAdvisorError("proposal_must_be_single_statement")
    if not re.match(r"^\s*(?:select|with)\b", inspected, re.IGNORECASE):
        raise RefactorAdvisorError("proposal_must_be_select")
    if _BLOCKED_SQL.search(inspected):
        raise RefactorAdvisorError("proposal_contains_blocked_sql")
    if _BLOCKED_SCHEMA.search(inspected):
        raise RefactorAdvisorError("proposal_schema_not_allowed")
    added_literals = set(_SQL_LITERAL.findall(proposed)) - set(
        _SQL_LITERAL.findall(original_sql)
    )
    if added_literals:
        raise RefactorAdvisorError("proposal_introduces_new_literal")
    if proposed.rstrip(";").strip() == original_sql.strip().rstrip(";").strip():
        raise RefactorAdvisorError("proposal_unchanged")
    return proposed


def _validate_proposal(proposal: dict[str, Any], original_sql: str) -> None:
    schema = _read_object(PROPOSAL_SCHEMA)
    errors = list(Draft202012Validator(schema).iter_errors(proposal))
    if errors:
        raise RefactorAdvisorError("proposal_contract_invalid")
    validate_read_only_proposal(proposal["proposed_sql"], original_sql)
    narrative = "\n".join([proposal["change_summary"], *proposal["limitations"]])
    if _RUNTIME_CLAIM.search(narrative):
        raise RefactorAdvisorError("proposal_contains_runtime_claim")


class CodexSolRefactorAdvisor:
    def __init__(self, timeout_seconds: int = MODEL_TIMEOUT_SECONDS) -> None:
        self.timeout_seconds = timeout_seconds

    def propose(self, request: dict[str, Any]) -> dict[str, Any]:
        original_sql = request["original_sql"]
        prompt = RULES_PATH.read_text(encoding="utf-8")
        payload = {
            "request_id": request["request_id"],
            "query_id": request["query_id"],
            "validation_schema": "sakila_dev",
            "original_sql": original_sql,
            "original_sql_sha256": request["original_sql_sha256"],
            "evidence": request["evidence"],
        }
        prompt += "\n\n<DADOS_NAO_CONFIAVEIS>\n"
        prompt += json.dumps(payload, ensure_ascii=False, sort_keys=True)
        prompt += "\n</DADOS_NAO_CONFIAVEIS>\n"
        try:
            proposal = json.loads(
                run_analysis(
                    prompt,
                    role="refactor",
                    schema=PROPOSAL_SCHEMA,
                    timeout_seconds=self.timeout_seconds,
                    runner=subprocess.run,
                )
            )
        except LlmError as error:
            raise RefactorAdvisorError(str(error)) from error
        _validate_proposal(proposal, original_sql)
        return proposal


def load_original_catalog(path: Path) -> dict[str, str]:
    content = path.read_text(encoding="utf-8")
    markers = list(_QUERY_MARKER.finditer(content))
    originals: dict[str, str] = {}
    for index, marker in enumerate(markers):
        end = markers[index + 1].start() if index + 1 < len(markers) else len(content)
        query_id = marker.group(1).lower()
        if query_id in originals:
            raise ValueError("catalog_original_duplicated")
        sql = content[marker.end() : end].strip()
        originals[query_id] = _MAX_EXECUTION_HINT.sub("", sql).strip()
    if not originals:
        raise ValueError("catalog_originals_missing")
    return originals


def validate_request(request: dict[str, Any]) -> None:
    required = {
        "contract_version",
        "request_id",
        "query_id",
        "query_fingerprint",
        "schema",
        "catalog_source",
        "original_sql",
        "original_sql_sha256",
        "evidence",
    }
    if not required.issubset(request):
        raise ValueError("request_fields_missing")
    if request["contract_version"] != "query_refactor_request.v1":
        raise ValueError("request_contract_invalid")
    if request["schema"] != "sakila":
        raise ValueError("request_schema_invalid")
    if request["catalog_source"] != str(CATALOG_RELATIVE):
        raise ValueError("request_catalog_invalid")
    if request["original_sql_sha256"] != _sha256(request["original_sql"]):
        raise ValueError("request_original_hash_mismatch")
    if float(request["evidence"]["max_query_time_seconds"]) <= 80.0:
        raise ValueError("request_below_threshold")


def add_timeout_hint(sql: str, timeout_seconds: float) -> str:
    milliseconds = max(1, int(timeout_seconds * 1000))
    return re.sub(
        r"\bSELECT\b",
        f"SELECT /*+ MAX_EXECUTION_TIME({milliseconds}) */",
        sql,
        count=1,
        flags=re.IGNORECASE,
    )


class MysqlReadOnlyExecutor:
    def execute(self, sql: str, timeout_seconds: float) -> QueryExecution:
        command = [
            sys.executable,
            str(MYSQL_WRAPPER),
            "--batch",
            "--raw",
            "--default-character-set=utf8mb4",
            "--execute",
            add_timeout_hint(sql, timeout_seconds),
        ]
        environment = dict(os.environ)
        environment["MYSQL_DATABASE"] = "sakila_dev"
        started = time.monotonic()
        try:
            completed = subprocess.run(
                command,
                cwd=REFACTOR_ROOT,
                env=environment,
                capture_output=True,
                text=True,
                timeout=timeout_seconds + 5,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return QueryExecution(
                exit_code=124,
                seconds=round(time.monotonic() - started, 6),
                header=None,
                row_count=0,
                result_sha256=None,
                error_code="client_timeout",
            )
        elapsed = round(time.monotonic() - started, 6)
        if completed.returncode != 0:
            return QueryExecution(
                exit_code=completed.returncode,
                seconds=elapsed,
                header=None,
                row_count=0,
                result_sha256=None,
                error_code="mysql_execution_failed",
            )
        lines = completed.stdout.replace("\r\n", "\n").splitlines()
        header = lines[0] if lines else ""
        rows = lines[1:]
        digest = hashlib.sha256("\n".join(sorted(rows)).encode("utf-8")).hexdigest()
        return QueryExecution(
            exit_code=0,
            seconds=elapsed,
            header=header,
            row_count=len(rows),
            result_sha256=digest,
        )


def _paths(record_dir: Path) -> tuple[Path, Path, Path]:
    return (
        record_dir / "original.sql",
        record_dir / "proposed.sql",
        record_dir / "report.md",
    )


def _relative(path: Path) -> str:
    return str(path.relative_to(REFACTOR_ROOT))


def _validate_result(result: dict[str, Any]) -> None:
    schema = _read_object(RESULT_SCHEMA)
    errors = list(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(result)
    )
    if errors:
        raise ValueError("result_contract_invalid")


def build_result(
    request: dict[str, Any],
    original_sql: str,
    proposed_sql: str,
    original: QueryExecution,
    proposed: QueryExecution,
    report_path: Path,
    original_path: Path,
    proposal_path: Path,
    change_summary: str,
    advisor_limitations: list[str],
) -> dict[str, Any]:
    equivalent = (
        original.exit_code == 0
        and proposed.exit_code == 0
        and original.header == proposed.header
        and original.row_count == proposed.row_count
        and original.result_sha256 == proposed.result_sha256
    )
    improved = proposed.seconds < original.seconds
    status = "approved_lab" if equivalent and improved else "requires_dba_validation"
    saved = round(original.seconds - proposed.seconds, 6)
    improvement = (
        round((saved / original.seconds * 100), 3) if original.seconds else 0.0
    )
    speedup = round(original.seconds / proposed.seconds, 3) if proposed.seconds else 0.0
    limitations = [
        *advisor_limitations,
        "Validação exclusivamente read-only no schema sakila_dev.",
        "Nenhuma alteração foi aplicada em sakila ou produção.",
        "As execuções foram seriais, mas ocorreram em sessões MySQL separadas.",
    ]
    if not equivalent:
        limitations.append("A equivalência do resultado não foi comprovada.")
    if not improved:
        limitations.append("A proposta não foi mais rápida nesta execução.")
    return {
        "contract_version": "query_refactor_result.v1",
        "result_id": str(uuid.uuid4()),
        "request_id": request["request_id"],
        "completed_at": _utc_now(),
        "source": "refactor",
        "destination": "dba",
        "query_id": request["query_id"],
        "query_fingerprint": request["query_fingerprint"],
        "status": status,
        "validation_schema": "sakila_dev",
        "original_sql": original_sql,
        "original_sql_sha256": _sha256(original_sql),
        "proposed_sql": proposed_sql,
        "proposed_sql_sha256": _sha256(proposed_sql),
        "change_summary": change_summary,
        "timings": {
            "before_seconds": original.seconds,
            "after_seconds": proposed.seconds,
            "saved_seconds": saved,
            "improvement_percent": improvement,
            "speedup": speedup,
        },
        "validation": {
            "equivalent": equivalent,
            "original_exit_code": original.exit_code,
            "proposed_exit_code": proposed.exit_code,
            "original_rows": original.row_count,
            "proposed_rows": proposed.row_count,
            "original_result_sha256": original.result_sha256,
            "proposed_result_sha256": proposed.result_sha256,
        },
        "artifacts": {
            "original_path": _relative(original_path),
            "proposal_path": _relative(proposal_path),
            "report_path": _relative(report_path),
        },
        "limitations": limitations,
    }


def render_report(result: dict[str, Any]) -> str:
    timings = result["timings"]
    validation = result["validation"]
    equivalent = str(validation["equivalent"]).lower()
    evidence = "\n".join(
        (
            "- Execução serial final: "
            f"original `{timings['before_seconds']} s`, "
            f"proposta `{timings['after_seconds']} s`.",
            "- Ganho de laboratório: "
            f"`{timings['saved_seconds']} s` "
            f"(`{timings['improvement_percent']}%`, `{timings['speedup']}x`).",
            "- Equivalência por cabeçalho, quantidade de linhas e SHA-256 "
            f"do conjunto sem ordem: `{equivalent}`.",
            "- Linhas comparadas: "
            f"original `{validation['original_rows']}`, "
            f"proposta `{validation['proposed_rows']}`.",
        )
    )
    status = (
        "APROVADA SOMENTE NO LABORATÓRIO sakila_dev"
        if result["status"] == "approved_lab"
        else "REQUER VALIDAÇÃO DO DBA"
    )
    return (
        f"""# Handoff: {result["query_id"]} — primeira passada

- ID: `{result["request_id"]}`
- Data/hora: `{result["completed_at"]}`
- Origem: Refactor
- Destino: DBA
- Advisor: `{llm_settings("refactor").provider}`
- Model: `{llm_settings("refactor").model or "client-default"}`
- Effort: `{llm_settings("refactor").effort}`
- Status: **{status}**
- Escopo: validação read-only e serial no schema `sakila_dev`; produção não alterada.

## SQL e mudança

- Original literal: `{result["artifacts"]["original_path"]}`
- Proposta canônica: `{result["artifacts"]["proposal_path"]}`
- Mudança única: {result["change_summary"]}

## Evidências

{evidence}

## Limitações

"""
        + "".join(f"- {item}\n" for item in result["limitations"])
        + f"""

## Cobertura e limitações

- Foram comparados cabeçalho, quantidade de linhas e SHA-256 do conjunto sem
  ordem.
- Evidência restrita ao `sakila_dev`; nenhuma afirmação de ganho em produção.

## Decisões e justificativas

- Status `{result["status"]}` baseado em equivalência `{equivalent}` e
  comparação serial de tempo.

## Riscos e limitações

- Diferenças de volume, estatísticas e concorrência entre laboratório e
  produção podem alterar o desempenho.

## Próximo passo

- O DBA deve revisar a proposta e decidir se ela será apenas encaminhada aos
  desenvolvedores. Este handoff não autoriza implantação.
"""
    )


def process_request(
    request_path: Path,
    *,
    analyzer: Analyzer,
    executor: Executor,
    publisher: Publisher,
) -> dict[str, Any]:
    request = _read_object(request_path)
    validate_request(request)
    record_dir = request_path.parent
    result_path = record_dir / "result.json"
    state_path = record_dir / "state.json"
    if result_path.is_file():
        result = _read_object(result_path)
        _validate_result(result)
    else:
        catalog_path = (REPOSITORY_ROOT / request["catalog_source"]).resolve()
        if catalog_path != (REPOSITORY_ROOT / CATALOG_RELATIVE).resolve():
            raise ValueError("catalog_path_not_allowed")
        original_sql = load_original_catalog(catalog_path).get(request["query_id"])
        if not original_sql:
            raise ValueError("query_not_in_catalog")
        if original_sql != request["original_sql"]:
            raise ValueError("catalog_original_mismatch")
        original_path, proposal_path, report_path = _paths(record_dir)
        _atomic_text(original_path, original_sql.rstrip() + "\n")
        proposal = analyzer.propose(request)
        _validate_proposal(proposal, original_sql)
        proposed_sql = validate_read_only_proposal(
            proposal["proposed_sql"], original_sql
        )
        _atomic_json(
            record_dir / "advisor.json",
            {
                "model": llm_settings("refactor").model or "client-default",
                "provider": llm_settings("refactor").provider,
                "reasoning_effort": llm_settings("refactor").effort,
                **proposal,
            },
        )
        _atomic_text(proposal_path, proposed_sql.rstrip() + "\n")
        historical = float(request["evidence"]["max_query_time_seconds"])
        original = executor.execute(original_sql, max(60.0, historical * 2.0))
        proposal_limit = max(
            30.0, original.seconds if original.exit_code == 0 else historical
        )
        proposed = executor.execute(proposed_sql, proposal_limit)
        result = build_result(
            request,
            original_sql,
            proposed_sql,
            original,
            proposed,
            report_path,
            original_path,
            proposal_path,
            proposal["change_summary"],
            proposal["limitations"],
        )
        _validate_result(result)
        _atomic_text(report_path, render_report(result))
        _atomic_json(result_path, result)

    publication = publisher.publish(result)
    dba = publication.get("dba")
    delivered = (
        publication.get("accepted") is True
        and isinstance(dba, dict)
        and dba.get("status") in {"recorded", "duplicate"}
    )
    state = {
        "updated_at": _utc_now(),
        "status": "completed" if delivered else "pending_delivery",
        "result_id": result["result_id"],
        "publication": publication,
    }
    _atomic_json(state_path, state)
    _atomic_json(
        record_dir.parent / "latest.json",
        {
            "request_id": result["request_id"],
            "job_id": record_dir.name,
            "result_path": _relative(result_path),
            "report_path": _relative(record_dir / "report.md"),
            "updated_at": state["updated_at"],
        },
    )
    return state


def pending_requests(inbox: Path = INBOX) -> list[Path]:
    if not inbox.is_dir():
        return []
    pending = []
    for request in sorted(inbox.glob("*/request.json")):
        state = request.parent / "state.json"
        if state.is_file() and _read_object(state).get("status") == "completed":
            continue
        pending.append(request)
    return pending


def run_cycle(
    *, analyzer: Analyzer, executor: Executor, publisher: Publisher
) -> dict[str, Any]:
    requests = pending_requests()
    processed = 0
    failed = 0
    for request in requests:
        try:
            state = process_request(
                request,
                analyzer=analyzer,
                executor=executor,
                publisher=publisher,
            )
            processed += state["status"] == "completed"
            failed += state["status"] != "completed"
        except (OSError, RuntimeError, ValueError, KeyError, TypeError):
            failed += 1
    return {
        "status": "waiting" if not requests else "processed",
        "pending_found": len(requests),
        "completed": processed,
        "failed": failed,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("run-once", "monitor"), nargs="?", default="monitor"
    )
    parser.add_argument("--interval-seconds", type=float, default=INTERVAL_SECONDS)
    args = parser.parse_args()
    if args.interval_seconds < 1:
        parser.error("--interval-seconds must be at least 1")
    analyzer = CodexSolRefactorAdvisor()
    executor = MysqlReadOnlyExecutor()
    publisher = McpRefactorResultPublisher()
    while True:
        result = run_cycle(analyzer=analyzer, executor=executor, publisher=publisher)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        if args.command == "run-once":
            return 0 if result["failed"] == 0 else 1
        time.sleep(args.interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
