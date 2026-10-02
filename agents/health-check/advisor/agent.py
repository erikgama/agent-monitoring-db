"""Luna-owned decisions and MCP publication for SELECT-latency HTML reports."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from agent_monitoring.llm import LlmError, build_command, llm_settings, run_analysis
from jsonschema import Draft202012Validator

from src.alert_contract import (
    CONTRACT_VERSION,
    SOURCE,
    AlertContractError,
    validate_alert,
)
from src.alerting import (
    AlertPublisher,
    AlertStateStore,
    CooldownPolicy,
    McpIncidentPublisher,
)
from src.alerting.publishing import publish_validated_alerts

BASE_DIR = Path(__file__).resolve().parents[1]
MODEL = "gpt-5.6-luna"
REASONING_EFFORT = "low"
INTERVAL_SECONDS = 15.0
REPORT_HTML_PATH = BASE_DIR / "select_latency" / "results" / "latest.html"
REPORT_JSON_PATH = BASE_DIR / "select_latency" / "results" / "latest.json"
GENERAL_REPORT_HTML_PATH = BASE_DIR / "general_report" / "results" / "report.html"
GENERAL_REPORT_JSON_PATH = BASE_DIR / "general_report" / "results" / "report.json"
OUTPUT_DIRECTORY = BASE_DIR / "advisor" / "results"
RULES_PATH = BASE_DIR / "advisor" / "rules.md"
ANALYSIS_SCHEMA_PATH = BASE_DIR / "advisor" / "analysis.schema.json"
ALERT_STATE_PATH = OUTPUT_DIRECTORY / "runtime" / "alert-state.json"
P99_THRESHOLD_SECONDS = 2.0
DEDUPE_KEY = "sakila:actor_popularity:query_latency:p99_gt_2s"
TARGET_DIGEST = "97eb2e3ec6c2ecefc310ed0c449384c04c59dc149f6cb31ca75e95a9a7836fe5"


class LunaAnalysisError(RuntimeError):
    """Safe failure code for the Luna decision boundary."""


class Analyzer(Protocol):
    def analyze(self, payload: dict[str, Any]) -> dict[str, Any]: ...


def _iso(value: datetime) -> str:
    return (
        value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    )


def _parse_utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as error:
        raise LunaAnalysisError("report_collected_at_invalid") from error
    if parsed.tzinfo is None:
        raise LunaAnalysisError("report_collected_at_invalid")
    return parsed.astimezone(UTC)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def collect_latest_general_report() -> None:
    """Run the existing safe collection so the DBA receives a fresh report."""
    from general_report.main import build_health_snapshot
    from src.mysql_cli import MysqlCliConnection

    try:
        policy = _read_object(BASE_DIR / "policy.json", "health_check_policy")
        timeout = int(policy["collection"]["query_timeout_seconds"])
        connection = MysqlCliConnection.from_environment(timeout)
        build_health_snapshot(connection)
    except (KeyError, OSError, RuntimeError, TypeError, ValueError) as error:
        raise LunaAnalysisError("general_report_collection_failed") from error


def _read_text(path: Path, error_code: str) -> str:
    try:
        value = path.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise LunaAnalysisError(f"{error_code}_missing") from error
    except (OSError, UnicodeError) as error:
        raise LunaAnalysisError(f"{error_code}_unreadable") from error
    if not value.strip():
        raise LunaAnalysisError(f"{error_code}_empty")
    return value


def _read_object(path: Path, error_code: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise LunaAnalysisError(f"{error_code}_missing") from error
    except (OSError, json.JSONDecodeError) as error:
        raise LunaAnalysisError(f"{error_code}_unreadable_or_invalid") from error
    if not isinstance(value, dict):
        raise LunaAnalysisError(f"{error_code}_must_be_object")
    return value


def _validate_analysis(value: dict[str, Any]) -> None:
    schema = _read_object(ANALYSIS_SCHEMA_PATH, "analysis_schema")
    errors = sorted(
        Draft202012Validator(schema).iter_errors(value),
        key=lambda error: tuple(str(item) for item in error.absolute_path),
    )
    if errors:
        raise LunaAnalysisError("luna_output_schema_invalid")
    if value["decision"] == "alert" and value["observed_value_seconds"] is None:
        raise LunaAnalysisError("luna_alert_missing_observed_value")
    observed = value["observed_value_seconds"]
    if value["decision"] == "alert" and observed <= P99_THRESHOLD_SECONDS:
        raise LunaAnalysisError("luna_decision_inconsistent_with_rule")
    if (
        value["decision"] == "no_alert"
        and observed is not None
        and observed > P99_THRESHOLD_SECONDS
    ):
        raise LunaAnalysisError("luna_decision_inconsistent_with_rule")


def _codex_command(output_path: Path) -> list[str]:
    return build_command(llm_settings("analysis"), output_path, ANALYSIS_SCHEMA_PATH)


class CodexLunaAnalyzer:
    def __init__(self, *, timeout_seconds: int = 120) -> None:
        self.timeout_seconds = timeout_seconds

    def analyze(self, payload: dict[str, Any]) -> dict[str, Any]:
        prompt = _read_text(RULES_PATH, "agent_rules")
        prompt += "\n\n<DATA>\n"
        prompt += json.dumps(payload, ensure_ascii=False, sort_keys=True)
        prompt += "\n</DATA>\n"
        try:
            decision = json.loads(
                run_analysis(
                    prompt,
                    schema=ANALYSIS_SCHEMA_PATH,
                    timeout_seconds=self.timeout_seconds,
                    runner=subprocess.run,
                )
            )
        except LlmError as error:
            raise LunaAnalysisError(str(error)) from error
        _validate_analysis(decision)
        return decision


def build_analysis_payload(html_document: str) -> dict[str, Any]:
    if "<html" not in html_document.lower() or "</html>" not in html_document.lower():
        raise LunaAnalysisError("select_latency_html_incomplete")
    return {
        "purpose": "agent_owned_alert_decision",
        "report_html": html_document,
    }


def _validate_report_pair(report: dict[str, Any], html_document: str) -> str:
    audit_id = report.get("audit_id")
    collected_at = report.get("collected_at")
    schemas = (report.get("scope") or {}).get("schemas") or []
    if not isinstance(audit_id, str) or not audit_id:
        raise LunaAnalysisError("select_latency_report_invalid_audit_id")
    if schemas != ["sakila"]:
        raise LunaAnalysisError("report_schema_out_of_scope")
    if audit_id not in html_document:
        raise LunaAnalysisError("select_latency_report_pair_audit_mismatch")
    if not isinstance(collected_at, str) or collected_at not in html_document:
        raise LunaAnalysisError("select_latency_report_pair_timestamp_mismatch")
    return audit_id


def build_agent_alert(
    latency_report: dict[str, Any],
    latency_html: str,
    general_report: dict[str, Any],
    general_report_html: str,
    decision: dict[str, Any],
    environment: str,
    *,
    detected_at: datetime | None = None,
) -> dict[str, Any] | None:
    """Translate Luna's structured decision into the versioned MCP contract."""
    _validate_analysis(decision)
    if decision["decision"] != "alert":
        return None
    latency_audit_id = _validate_report_pair(latency_report, latency_html)
    general_audit_id = _validate_report_pair(general_report, general_report_html)
    latest_collection = max(
        _parse_utc(latency_report["collected_at"]),
        _parse_utc(general_report["collected_at"]),
    )
    detection = max((detected_at or _utc_now()).astimezone(UTC), latest_collection)
    observed = decision["observed_value_seconds"]
    alert = {
        "contract_version": CONTRACT_VERSION,
        "alert_id": str(uuid.uuid4()),
        "audit_id": general_audit_id,
        "detected_at": _iso(detection),
        "environment": environment,
        "source": SOURCE,
        "severity": "critical",
        "category": "query_latency",
        "title": "P99 acima de 2 segundos na SELECT monitorada do Sakila",
        "summary": decision["summary"],
        "findings": [
            {
                "check_id": "health-check-luna.select-latency.p99-gt-2s",
                "metric": "p99_seconds",
                "observed_value": observed,
                "threshold": P99_THRESHOLD_SECONDS,
                "unit": "seconds",
                "evidence": {
                    "agent_decision": decision["decision"],
                    "evidence_quality": decision["evidence_quality"],
                    "p99_seconds": observed,
                    "threshold_seconds": P99_THRESHOLD_SECONDS,
                    "source_document": "select_latency/results/latest.html",
                    "source_audit_id": latency_audit_id,
                },
                "affected_objects": [
                    "schema:sakila",
                    "query:actor_popularity",
                    f"digest:{TARGET_DIGEST}",
                ],
            }
        ],
        "dedupe_key": DEDUPE_KEY,
        "report": {
            "json": general_report,
            "html": general_report_html,
            "report_generated_at": general_report["collected_at"],
            "report_format_version": general_report["schema_version"],
        },
        "metadata": {
            "decision_owner": "health-check-luna",
            "decision_source_audit_id": latency_audit_id,
            "dba_report_source": "general_report/results/report.html",
            "model": llm_settings("analysis").model or "client-default",
            "provider": llm_settings("analysis").provider,
            "rule_source": "agents/health-check/advisor/rules.md",
        },
    }
    validate_alert(alert)
    return alert


def _write_analysis(output_directory: Path, envelope: dict[str, Any]) -> None:
    output_directory.mkdir(parents=True, exist_ok=True)
    target = output_directory / "latest.json"
    temporary = target.with_name(target.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(envelope, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, target)


def _previous_audit_id(output_directory: Path) -> str | None:
    try:
        value = _read_object(output_directory / "latest.json", "previous_analysis")
    except LunaAnalysisError:
        return None
    source_audit_id = value.get("source_audit_id")
    return source_audit_id if isinstance(source_audit_id, str) else None


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name.lower()}_must_be_boolean")


def _cooldown_from_environment() -> CooldownPolicy:
    raw = os.environ.get("HEALTHCHECK_ALERT_CRITICAL_COOLDOWN_SECONDS")
    critical = 120 if raw is None else int(raw)
    if not 0 <= critical <= 86_400:
        raise ValueError("healthcheck_alert_critical_cooldown_seconds_out_of_range")
    return CooldownPolicy(critical_seconds=critical)


def _alerting_runtime(
    publisher: AlertPublisher | None,
    state_path: Path | None,
    environment: str | None,
) -> tuple[AlertPublisher | None, AlertStateStore | None, str | None]:
    enabled = publisher is not None or _env_bool("HEALTHCHECK_ALERTING_ENABLED")
    if not enabled:
        return None, None, None
    selected_environment = (
        environment or os.environ.get("HEALTHCHECK_ENVIRONMENT", "")
    ).strip()
    return (
        publisher or McpIncidentPublisher(),
        AlertStateStore(state_path or ALERT_STATE_PATH),
        selected_environment,
    )


def _publication_fields(publication: dict[str, Any]) -> dict[str, Any]:
    outcomes = publication.get("outcomes") or []
    first = outcomes[0] if outcomes and isinstance(outcomes[0], dict) else {}
    delivery = first.get("delivery") if isinstance(first.get("delivery"), dict) else {}
    return {
        "publication_status": publication.get("status"),
        "published_count": publication.get("published_count", 0),
        "suppressed_count": publication.get("suppressed_count", 0),
        "published_to_mcp": first.get("published_to_mcp"),
        "mcp_status": first.get("mcp_status"),
        "delivery_status": first.get("delivery_status"),
        "email_delivered": delivery.get("delivered"),
        "dba_status": first.get("dba_status"),
    }


def run_agent(
    *,
    analyzer: Analyzer | None = None,
    html_path: Path = REPORT_HTML_PATH,
    report_json_path: Path = REPORT_JSON_PATH,
    general_report_html_path: Path = GENERAL_REPORT_HTML_PATH,
    general_report_json_path: Path = GENERAL_REPORT_JSON_PATH,
    general_report_collector: Callable[[], None] = collect_latest_general_report,
    output_directory: Path = OUTPUT_DIRECTORY,
    publisher: AlertPublisher | None = None,
    alert_state_path: Path | None = None,
    environment: str | None = None,
    cooldown: CooldownPolicy | None = None,
    interval_seconds: float = INTERVAL_SECONDS,
    max_cycles: int | None = None,
    analyze_existing: bool = False,
    sleep_fn: Callable[[float], None] = time.sleep,
    monotonic_fn: Callable[[], float] = time.monotonic,
    now_fn: Callable[[], datetime] = _utc_now,
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> int:
    if interval_seconds <= 0:
        raise ValueError("agent_interval_must_be_positive")
    selected_analyzer = analyzer or CodexLunaAnalyzer()
    selected_publisher, state, selected_environment = _alerting_runtime(
        publisher,
        alert_state_path,
        environment,
    )
    selected_cooldown = cooldown or _cooldown_from_environment()
    last_audit_id = _previous_audit_id(output_directory)
    if last_audit_id is None and not analyze_existing:
        try:
            existing_html = _read_text(html_path, "select_latency_html")
            existing_report = _read_object(
                report_json_path,
                "select_latency_report",
            )
            last_audit_id = _validate_report_pair(existing_report, existing_html)
        except LunaAnalysisError:
            pass
    analyses = 0
    cycles = 0
    next_tick = monotonic_fn()
    while max_cycles is None or cycles < max_cycles:
        remaining = next_tick - monotonic_fn()
        if remaining > 0:
            sleep_fn(remaining)
        cycles += 1
        try:
            html_document = _read_text(html_path, "select_latency_html")
            report = _read_object(report_json_path, "select_latency_report")
            audit_id = _validate_report_pair(report, html_document)
            if audit_id == last_audit_id:
                event = {
                    "status": "waiting_for_new_collection",
                    "source_audit_id": audit_id,
                }
            else:
                started = monotonic_fn()
                decision = selected_analyzer.analyze(
                    build_analysis_payload(html_document)
                )
                _validate_analysis(decision)
                analyzed_at = now_fn()
                if selected_publisher is None:
                    publication = {"status": "disabled", "published_count": 0}
                elif state is None or not selected_environment:
                    publication = {
                        "status": "failed",
                        "published_count": 0,
                        "error_code": "agent_alerting_configuration_invalid",
                    }
                elif decision["decision"] == "alert":
                    try:
                        general_report_collector()
                    except LunaAnalysisError:
                        raise
                    except Exception as error:
                        raise LunaAnalysisError(
                            "general_report_collection_failed"
                        ) from error
                    general_report_html = _read_text(
                        general_report_html_path,
                        "general_report_html",
                    )
                    general_report = _read_object(
                        general_report_json_path,
                        "general_report_json",
                    )
                    alert = build_agent_alert(
                        report,
                        html_document,
                        general_report,
                        general_report_html,
                        decision,
                        selected_environment,
                        detected_at=analyzed_at,
                    )
                    if alert is None:
                        raise LunaAnalysisError("luna_alert_decision_missing_alert")
                    publication = publish_validated_alerts(
                        [alert],
                        selected_publisher,
                        state,
                        cooldown=selected_cooldown,
                        evaluated_at=analyzed_at,
                    )
                elif decision["decision"] == "no_alert":
                    publication = publish_validated_alerts(
                        [],
                        selected_publisher,
                        state,
                        cooldown=selected_cooldown,
                        evaluated_at=analyzed_at,
                    )
                else:
                    publication = publish_validated_alerts(
                        [],
                        selected_publisher,
                        state,
                        cooldown=selected_cooldown,
                        evaluated_at=analyzed_at,
                        preserve_dedupe_keys={DEDUPE_KEY},
                    )
                envelope = {
                    "schema_version": "health_check_luna_decision.v1",
                    "analysis_id": str(uuid.uuid4()),
                    "analyzed_at": _iso(analyzed_at),
                    "source_audit_id": audit_id,
                    "source_collected_at": report.get("collected_at"),
                    "source_document": "select_latency/results/latest.html",
                    "model": llm_settings("analysis").model or "client-default",
                    "provider": llm_settings("analysis").provider,
                    "reasoning_effort": llm_settings("analysis").effort,
                    "decision_owner": "health-check-luna",
                    "decision": decision,
                    "publication": publication,
                }
                _write_analysis(output_directory, envelope)
                last_audit_id = audit_id
                analyses += 1
                event = {
                    "status": "analyzed",
                    "analysis_id": envelope["analysis_id"],
                    "source_audit_id": audit_id,
                    "model": llm_settings("analysis").model or "client-default",
                    "provider": llm_settings("analysis").provider,
                    "reasoning_effort": llm_settings("analysis").effort,
                    "agent_decision": decision["decision"],
                    "duration_seconds": round(monotonic_fn() - started, 3),
                    **_publication_fields(publication),
                }
        except (AlertContractError, LunaAnalysisError) as error:
            event = {"status": "analysis_failed", "error_code": str(error)}
        if on_event is not None:
            on_event(event)
        next_tick += interval_seconds
        if next_tick < monotonic_fn():
            next_tick = monotonic_fn() + interval_seconds
    return analyses


def _print_event(event: dict[str, Any]) -> None:
    json.dump(event, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    sys.stdout.flush()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Let GPT-5.6 Luna decide from SELECT-latency HTML and alert MCP"
    )
    parser.add_argument(
        "--interval-seconds",
        type=float,
        default=INTERVAL_SECONDS,
    )
    parser.add_argument(
        "--max-cycles",
        type=int,
        help="stop after N polling cycles (intended for validation)",
    )
    parser.add_argument(
        "--analyze-existing",
        action="store_true",
        help="analyze the current HTML instead of waiting for a new collection",
    )
    arguments = parser.parse_args()
    if arguments.max_cycles is not None and arguments.max_cycles <= 0:
        parser.error("--max-cycles must be positive")
    try:
        run_agent(
            interval_seconds=arguments.interval_seconds,
            max_cycles=arguments.max_cycles,
            analyze_existing=arguments.analyze_existing,
            on_event=_print_event,
        )
    except KeyboardInterrupt:
        _print_event({"status": "stopped"})
    except (OSError, RuntimeError, ValueError) as error:
        _print_event(
            {
                "status": "agent_failed",
                "error_code": type(error).__name__,
            }
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
