"""Luna-owned decisions and MCP publication for Audit Security reports."""

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

from ..alerting import AlertState, McpIncidentPublisher, build_agent_alert

BASE_DIR = Path(__file__).resolve().parents[2]
MODEL = "gpt-5.6-luna"
REASONING_EFFORT = "low"
INTERVAL_SECONDS = 15.0
REPORT_JSON_PATH = BASE_DIR / "audit_security" / "results" / "latest.json"
REPORT_HTML_PATH = BASE_DIR / "audit_security" / "results" / "latest.html"
OUTPUT_DIRECTORY = BASE_DIR / "audit_security" / "advisor" / "results"
RULES_PATH = BASE_DIR / "audit_security" / "advisor" / "rules.md"
ANALYSIS_SCHEMA_PATH = BASE_DIR / "audit_security" / "advisor" / "analysis.schema.json"
ALERT_STATE_PATH = OUTPUT_DIRECTORY / "runtime" / "alert-state.json"


class LunaAnalysisError(RuntimeError):
    """Safe failure code for the Luna decision boundary."""


class Analyzer(Protocol):
    def analyze(self, payload: dict[str, Any]) -> dict[str, Any]: ...


class Publisher(Protocol):
    def publish(self, alert: dict[str, Any]) -> Any: ...


def _iso(value: datetime) -> str:
    return (
        value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    )


def _utc_now() -> datetime:
    return datetime.now(UTC)


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
    errors = list(Draft202012Validator(schema).iter_errors(value))
    if errors:
        raise LunaAnalysisError("luna_output_schema_invalid")
    alerts = value["alerts"]
    if value["decision"] == "alert" and not alerts:
        raise LunaAnalysisError("luna_alerts_missing")
    if value["decision"] != "alert" and alerts:
        raise LunaAnalysisError("luna_unexpected_alerts")
    keys = [item["event_key"] for item in alerts]
    if len(keys) != len(set(keys)):
        raise LunaAnalysisError("luna_duplicate_event_keys")
    if any(item["event_key"] != item["evidence"]["event_key"] for item in alerts):
        raise LunaAnalysisError("luna_event_key_mismatch")


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
            analysis = json.loads(
                run_analysis(
                    prompt,
                    schema=ANALYSIS_SCHEMA_PATH,
                    timeout_seconds=self.timeout_seconds,
                    runner=subprocess.run,
                )
            )
        except LlmError as error:
            raise LunaAnalysisError(str(error)) from error
        _validate_analysis(analysis)
        return analysis


def build_analysis_payload(
    html_document: str, *, agent_started_at: datetime
) -> dict[str, Any]:
    lowered = html_document.lower()
    if "<html" not in lowered or "</html>" not in lowered:
        raise LunaAnalysisError("audit_html_incomplete")
    return {
        "purpose": "agent_owned_alert_decision",
        "agent_started_at": _iso(agent_started_at),
        "report_html": html_document,
    }


def _validate_report_pair(report: dict[str, Any], html_document: str) -> str:
    audit_id = report.get("audit_id")
    collected_at = report.get("collected_at")
    if report.get("schema_version") != "mysql_audit_security_snapshot.v1":
        raise LunaAnalysisError("audit_report_schema_invalid")
    if report.get("scope", {}).get("functional_schemas") != ["sakila"]:
        raise LunaAnalysisError("report_schema_out_of_scope")
    if not isinstance(audit_id, str) or audit_id not in html_document:
        raise LunaAnalysisError("audit_report_pair_audit_mismatch")
    if not isinstance(collected_at, str) or collected_at not in html_document:
        raise LunaAnalysisError("audit_report_pair_timestamp_mismatch")
    return audit_id


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
    audit_id = value.get("source_audit_id")
    return audit_id if isinstance(audit_id, str) else None


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


def run_agent(
    *,
    analyzer: Analyzer | None = None,
    publisher: Publisher | None = None,
    state: AlertState | None = None,
    report_json_path: Path = REPORT_JSON_PATH,
    report_html_path: Path = REPORT_HTML_PATH,
    output_directory: Path = OUTPUT_DIRECTORY,
    interval_seconds: float = INTERVAL_SECONDS,
    max_cycles: int | None = None,
    alerting_enabled: bool | None = None,
    environment: str | None = None,
    start_from_current: bool = False,
    sleep_fn: Callable[[float], None] = time.sleep,
    monotonic_fn: Callable[[], float] = time.monotonic,
    now_fn: Callable[[], datetime] = _utc_now,
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> int:
    if interval_seconds <= 0:
        raise ValueError("agent_interval_must_be_positive")
    selected_analyzer = analyzer or CodexLunaAnalyzer()
    selected_publisher = publisher
    selected_state = state or AlertState(ALERT_STATE_PATH)
    enabled = (
        _env_bool("AUDIT_SECURITY_ALERTING_ENABLED")
        if alerting_enabled is None
        else alerting_enabled
    )
    selected_environment = environment or os.environ.get(
        "AUDIT_SECURITY_ENVIRONMENT", "local"
    )
    agent_started_at = now_fn().astimezone(UTC)
    last_audit_id = _previous_audit_id(output_directory)
    if start_from_current:
        current_report = _read_object(report_json_path, "audit_report")
        current_html = _read_text(report_html_path, "audit_html")
        last_audit_id = _validate_report_pair(current_report, current_html)
    analyses = 0
    cycles = 0
    next_tick = monotonic_fn()

    while max_cycles is None or cycles < max_cycles:
        remaining = next_tick - monotonic_fn()
        if remaining > 0:
            sleep_fn(remaining)
        cycles += 1
        try:
            report = _read_object(report_json_path, "audit_report")
            html_document = _read_text(report_html_path, "audit_html")
            audit_id = _validate_report_pair(report, html_document)
            if audit_id == last_audit_id:
                event = {
                    "status": "waiting_for_new_collection",
                    "source_audit_id": audit_id,
                }
            else:
                analysis = selected_analyzer.analyze(
                    build_analysis_payload(
                        html_document, agent_started_at=agent_started_at
                    )
                )
                _validate_analysis(analysis)
                selected_llm = llm_settings("analysis")
                publications: list[dict[str, Any]] = []
                for decision in analysis["alerts"]:
                    alert = build_agent_alert(
                        report,
                        html_document,
                        decision,
                        selected_environment,
                        agent_started_at=agent_started_at,
                        detected_at=now_fn(),
                        llm_model=selected_llm.model or "client-default",
                        llm_provider=selected_llm.provider,
                    )
                    key = alert["dedupe_key"]
                    if selected_state.was_published(key):
                        publications.append({"dedupe_key": key, "status": "duplicate"})
                    elif not enabled:
                        publications.append({"dedupe_key": key, "status": "disabled"})
                    else:
                        if selected_publisher is None:
                            selected_publisher = McpIncidentPublisher()
                        result = selected_publisher.publish(alert)
                        accepted = result.published_to_mcp and result.accepted is True
                        if accepted:
                            selected_state.record(key, alert)
                        publications.append(
                            {
                                "dedupe_key": key,
                                "status": ("accepted" if accepted else "not_confirmed"),
                                "publication": result.to_dict(),
                            }
                        )
                envelope = {
                    "schema_version": "audit_security_luna_monitor.v2",
                    "analysis_id": str(uuid.uuid4()),
                    "analyzed_at": _iso(now_fn()),
                    "source_audit_id": audit_id,
                    "source_collected_at": report.get("collected_at"),
                    "model": selected_llm.model or "client-default",
                    "provider": selected_llm.provider,
                    "reasoning_effort": selected_llm.effort,
                    "decision_owner": "audit-luna",
                    "analysis": analysis,
                    "publications": publications,
                }
                _write_analysis(output_directory, envelope)
                last_audit_id = audit_id
                analyses += 1
                event = {
                    "status": "analyzed",
                    "analysis_id": envelope["analysis_id"],
                    "source_audit_id": audit_id,
                    "decision": analysis["decision"],
                    "alert_count": len(analysis["alerts"]),
                    "publications": publications,
                }
        except (LunaAnalysisError, ValueError) as error:
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interval-seconds", type=float, default=INTERVAL_SECONDS)
    parser.add_argument("--max-cycles", type=int)
    parser.add_argument(
        "--start-from-current",
        action="store_true",
        help="Use the current report as the startup baseline without analyzing it.",
    )
    args = parser.parse_args(argv)
    try:
        run_agent(
            interval_seconds=args.interval_seconds,
            max_cycles=args.max_cycles,
            start_from_current=args.start_from_current,
            on_event=_print_event,
        )
    except KeyboardInterrupt:
        _print_event({"status": "stopped"})
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        _print_event({"status": "agent_failed", "error_code": type(error).__name__})
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
