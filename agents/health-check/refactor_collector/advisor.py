"""Luna-owned 30-second triage of Slow Query Log refactor candidates."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from agent_monitoring.llm import LlmError
from agent_monitoring.llm import run_analysis as run_llm_analysis
from jsonschema import Draft202012Validator, FormatChecker

from src.mysql_cli import MysqlCliConnection

from .collect_query_tuning_snapshot import (
    BASE_DIR,
    CATALOG_PATH,
    RESULTS_DIR,
    collect_snapshot,
    load_query_catalog,
)
from .mcp_publisher import McpRefactorRequestPublisher

MODEL = "gpt-5.6-luna"
REASONING_EFFORT = "low"
INTERVAL_SECONDS = 30.0
THRESHOLD_SECONDS = 80.0
RULES_PATH = BASE_DIR / "rules.md"
ANALYSIS_SCHEMA_PATH = BASE_DIR / "analysis.schema.json"
REQUEST_SCHEMA_PATH = (
    BASE_DIR.parent / "contracts" / "query_refactor_request.v1.schema.json"
)
DECISION_PATH = RESULTS_DIR / "analysis.json"
STATE_PATH = RESULTS_DIR / "runtime" / "refactor-state.json"


class RefactorAdvisorError(RuntimeError):
    pass


class Analyzer(Protocol):
    def analyze(self, payload: dict[str, Any]) -> dict[str, Any]: ...


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _read_text(path: Path) -> str:
    value = path.read_text(encoding="utf-8")
    if not value.strip():
        raise RefactorAdvisorError(f"empty_file:{path.name}")
    return value


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RefactorAdvisorError(f"object_required:{path.name}")
    return value


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


class CodexLunaAnalyzer:
    def __init__(self, timeout_seconds: int = 120) -> None:
        self.timeout_seconds = timeout_seconds

    def analyze(self, payload: dict[str, Any]) -> dict[str, Any]:
        prompt = _read_text(RULES_PATH)
        prompt += "\n\n<DATA>\n"
        prompt += json.dumps(payload, ensure_ascii=False, sort_keys=True)
        prompt += "\n</DATA>\n"
        try:
            return json.loads(
                run_llm_analysis(
                    prompt,
                    schema=ANALYSIS_SCHEMA_PATH,
                    timeout_seconds=self.timeout_seconds,
                    runner=subprocess.run,
                )
            )
        except LlmError as error:
            raise RefactorAdvisorError(str(error)) from error


def _validate_analysis(analysis: dict[str, Any], snapshot: dict[str, Any]) -> None:
    schema = _read_object(ANALYSIS_SCHEMA_PATH)
    errors = list(Draft202012Validator(schema).iter_errors(analysis))
    if errors:
        raise RefactorAdvisorError("luna_output_schema_invalid")
    if analysis["decision"] != "candidate":
        return
    matches = [
        query
        for query in snapshot["queries"]
        if query.get("query_fingerprint") == analysis["query_fingerprint"]
        and query.get("query_id") == analysis["query_id"]
        and query.get("known_query") is True
    ]
    if not matches:
        raise RefactorAdvisorError("candidate_not_present_in_snapshot")
    maximum = max(query["query_time_seconds"] for query in matches)
    if maximum <= THRESHOLD_SECONDS:
        raise RefactorAdvisorError("candidate_below_threshold")
    if analysis["observed_latency_seconds"] != maximum:
        raise RefactorAdvisorError("candidate_latency_mismatch")


def _validate_request(request: dict[str, Any]) -> None:
    schema = _read_object(REQUEST_SCHEMA_PATH)
    errors = list(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(
            request
        )
    )
    if errors:
        raise RefactorAdvisorError("request_schema_invalid")
    expected = hashlib.sha256(request["original_sql"].encode("utf-8")).hexdigest()
    if request["original_sql_sha256"] != expected:
        raise RefactorAdvisorError("request_original_hash_mismatch")
    report = request["report"]
    if report["json"].get("snapshot_id") != report["snapshot_id"]:
        raise RefactorAdvisorError("request_snapshot_id_mismatch")
    if report["snapshot_id"] not in report["html"]:
        raise RefactorAdvisorError("request_html_snapshot_mismatch")


def build_request(
    analysis: dict[str, Any], snapshot: dict[str, Any], html_document: str
) -> dict[str, Any]:
    fingerprint = analysis["query_fingerprint"]
    query_id = analysis["query_id"]
    occurrences = [
        query
        for query in snapshot["queries"]
        if query["query_fingerprint"] == fingerprint
    ]
    worst = max(occurrences, key=lambda item: item["query_time_seconds"])
    original_sql = load_query_catalog()[query_id].strip()
    request = {
        "contract_version": "query_refactor_request.v1",
        "request_id": str(uuid.uuid4()),
        "created_at": _utc_now(),
        "source": "health-check",
        "query_id": query_id,
        "query_fingerprint": fingerprint,
        "dedupe_key": f"sakila:{query_id}:{fingerprint}",
        "schema": "sakila",
        "catalog_source": str(CATALOG_PATH.relative_to(BASE_DIR.parents[2])),
        "original_sql": original_sql,
        "original_sql_sha256": hashlib.sha256(original_sql.encode("utf-8")).hexdigest(),
        "evidence": {
            "threshold_seconds": THRESHOLD_SECONDS,
            "max_query_time_seconds": worst["query_time_seconds"],
            "occurrences": len(occurrences),
            "qualifying_occurrences": sum(
                query["query_time_seconds"] > THRESHOLD_SECONDS for query in occurrences
            ),
            "rows_examined": worst["rows_examined"],
            "rows_sent": worst["rows_sent"],
        },
        "report": {
            "snapshot_id": snapshot["snapshot_id"],
            "collected_at": snapshot["collected_at"],
            "json": snapshot,
            "html": html_document,
        },
    }
    _validate_request(request)
    return request


def _state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {"sent_fingerprints": {}}
    value = _read_object(STATE_PATH)
    if not isinstance(value.get("sent_fingerprints"), dict):
        raise RefactorAdvisorError("state_invalid")
    return value


def run_analysis(
    snapshot: dict[str, Any],
    html_document: str,
    *,
    analyzer: Analyzer,
    publisher: McpRefactorRequestPublisher,
) -> dict[str, Any]:
    state = _state()
    sent = state["sent_fingerprints"]
    eligible = [
        query
        for query in snapshot["queries"]
        if query.get("known_query") is True
        and query["query_time_seconds"] > THRESHOLD_SECONDS
        and query["query_fingerprint"] not in sent
    ]
    if not eligible:
        result = {
            "evaluated_at": _utc_now(),
            "snapshot_id": snapshot["snapshot_id"],
            "decision": "no_candidate",
            "reason": "no_new_known_query_above_80_seconds",
        }
        _atomic_json(DECISION_PATH, result)
        return result

    payload = {
        "report_json": snapshot,
        "report_html": html_document,
        "already_sent_fingerprints": sorted(sent),
    }
    analysis = analyzer.analyze(payload)
    _validate_analysis(analysis, snapshot)
    result: dict[str, Any] = {
        "evaluated_at": _utc_now(),
        "snapshot_id": snapshot["snapshot_id"],
        "analysis": analysis,
    }
    if analysis["decision"] == "candidate":
        request = build_request(analysis, snapshot, html_document)
        publication = publisher.publish(request)
        result["request_id"] = request["request_id"]
        result["publication"] = publication
        refactor = publication.get("refactor")
        if (
            publication.get("accepted") is True
            and isinstance(refactor, dict)
            and refactor.get("status") in {"recorded", "duplicate"}
        ):
            sent[analysis["query_fingerprint"]] = {
                "query_id": analysis["query_id"],
                "request_id": request["request_id"],
                "sent_at": _utc_now(),
            }
            _atomic_json(STATE_PATH, state)
    _atomic_json(DECISION_PATH, result)
    return result


def collect_and_analyze(
    *,
    analyzer: Analyzer,
    publisher: McpRefactorRequestPublisher,
) -> dict[str, Any]:
    runtime_policy = _read_object(BASE_DIR.parent / "policy.json")
    connection = MysqlCliConnection.from_environment(
        int(runtime_policy["collection"]["query_timeout_seconds"])
    )
    snapshot = collect_snapshot(connection)
    html_document = _read_text(RESULTS_DIR / "latest.html")
    return run_analysis(snapshot, html_document, analyzer=analyzer, publisher=publisher)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("run-once", "monitor"), nargs="?", default="monitor"
    )
    parser.add_argument("--interval-seconds", type=float, default=INTERVAL_SECONDS)
    args = parser.parse_args()
    if args.interval_seconds < 1:
        parser.error("--interval-seconds must be at least 1")
    analyzer = CodexLunaAnalyzer()
    publisher = McpRefactorRequestPublisher()
    while True:
        try:
            result = collect_and_analyze(analyzer=analyzer, publisher=publisher)
            print(
                json.dumps({"status": "analyzed", **result}, ensure_ascii=False),
                flush=True,
            )
        except (OSError, RuntimeError, ValueError) as error:
            print(
                json.dumps({"status": "failed", "error_code": type(error).__name__}),
                file=sys.stderr,
                flush=True,
            )
            if args.command == "run-once":
                return 1
        if args.command == "run-once":
            return 0
        time.sleep(args.interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
