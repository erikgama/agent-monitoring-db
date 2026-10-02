"""Deterministic validation for the versioned health-check alert contract."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

CONTRACT_VERSION = "health_check_alert.v1"
SOURCE = "health-check"
SEVERITIES = frozenset({"info", "warning", "critical"})
CATEGORIES = frozenset(
    {
        "deadlock",
        "lock_wait",
        "query_latency",
        "connections",
        "innodb",
        "replication",
        "database_error",
    }
)

_TOP_LEVEL_FIELDS = {
    "contract_version",
    "alert_id",
    "audit_id",
    "detected_at",
    "environment",
    "source",
    "severity",
    "category",
    "title",
    "summary",
    "findings",
    "dedupe_key",
    "report",
    "metadata",
}
_REQUIRED_TOP_LEVEL = _TOP_LEVEL_FIELDS - {"metadata"}
_FINDING_FIELDS = {
    "check_id",
    "metric",
    "observed_value",
    "threshold",
    "unit",
    "evidence",
    "affected_objects",
}
_REQUIRED_FINDING_FIELDS = {"check_id", "metric", "observed_value", "evidence"}
_REPORT_FIELDS = {
    "json",
    "html",
    "report_generated_at",
    "report_format_version",
}
_COLLECTION_REQUIRED_FIELDS = {
    "schema_version",
    "audit_id",
    "started_at",
    "finished_at",
    "collected_at",
    "duration_ms",
    "target",
    "scope",
    "overall_status",
    "capabilities",
    "instance",
    "domains",
    "findings",
    "data_retention",
}
_SENSITIVE_KEYS = {
    "password",
    "passwd",
    "pwd",
    "token",
    "access_token",
    "refresh_token",
    "api_key",
    "apikey",
    "secret",
    "client_secret",
    "private_key",
    "connection_string",
    "dsn",
    "credential",
    "credentials",
    "private_host",
    "database_host",
    "db_host",
    "endpoint",
}
_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.I,
)
_ENVIRONMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_PRIVATE_IPV4 = re.compile(
    r"(?<!\d)(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|"
    r"192\.168\.\d{1,3}\.\d{1,3}|"
    r"172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})(?!\d)"
)
_SECRET_TEXT = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"(?:password|passwd|pwd|token|api[_-]?key|client[_-]?secret)\s*[:=]\s*\S+|"
    r"(?:mysql|mariadb)://[^\s/:@]+:[^\s@]+@",
    re.I,
)
_TIMESTAMP_IN_KEY = re.compile(r"\d{4}-\d{2}-\d{2}(?:[T_][0-9:.+-]+Z?)?")


class AlertContractError(ValueError):
    """Raised with a stable, non-sensitive validation code."""


def _mapping(value: Any, code: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AlertContractError(code)
    return value


def _nonempty_string(value: Any, code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AlertContractError(code)
    return value


def _utc_timestamp(value: Any, code: str) -> datetime:
    text = _nonempty_string(value, code)
    if not text.endswith("Z"):
        raise AlertContractError(code)
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError as error:
        raise AlertContractError(code) from error
    if parsed.tzinfo != UTC:
        raise AlertContractError(code)
    return parsed


def _check_sensitive(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            if normalized in _SENSITIVE_KEYS:
                raise AlertContractError(f"sensitive_field:{path}.{key}")
            _check_sensitive(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _check_sensitive(child, f"{path}[{index}]")
    elif isinstance(value, str):
        if _PRIVATE_IPV4.search(value) or _SECRET_TEXT.search(value):
            raise AlertContractError(f"sensitive_value:{path}")


def validate_alert(alert: dict[str, Any]) -> None:
    """Validate one alert payload without sending or persisting it."""
    value = _mapping(alert, "alert_must_be_object")
    missing = _REQUIRED_TOP_LEVEL - set(value)
    if missing:
        raise AlertContractError("alert_missing_fields:" + ",".join(sorted(missing)))
    extra = set(value) - _TOP_LEVEL_FIELDS
    if extra:
        raise AlertContractError("alert_unknown_fields:" + ",".join(sorted(extra)))

    if value["contract_version"] != CONTRACT_VERSION:
        raise AlertContractError("invalid_contract_version")
    alert_id = _nonempty_string(value["alert_id"], "invalid_alert_id")
    audit_id = _nonempty_string(value["audit_id"], "invalid_audit_id")
    if not _UUID.fullmatch(alert_id) or not _UUID.fullmatch(audit_id):
        raise AlertContractError("invalid_identifier_format")
    if value["source"] != SOURCE:
        raise AlertContractError("invalid_source")
    if value["severity"] not in SEVERITIES:
        raise AlertContractError("invalid_severity")
    if value["category"] not in CATEGORIES:
        raise AlertContractError("invalid_category")
    environment = _nonempty_string(value["environment"], "invalid_environment")
    if not _ENVIRONMENT.fullmatch(environment):
        raise AlertContractError("invalid_environment")
    _nonempty_string(value["title"], "invalid_title")
    _nonempty_string(value["summary"], "invalid_summary")

    findings = value["findings"]
    if not isinstance(findings, list) or not findings:
        raise AlertContractError("invalid_findings")
    for index, raw_finding in enumerate(findings):
        finding = _mapping(raw_finding, f"invalid_finding:{index}")
        missing_finding = _REQUIRED_FINDING_FIELDS - set(finding)
        if missing_finding:
            raise AlertContractError(f"finding_missing_fields:{index}")
        if set(finding) - _FINDING_FIELDS:
            raise AlertContractError(f"finding_unknown_fields:{index}")
        _nonempty_string(finding["check_id"], f"invalid_check_id:{index}")
        _nonempty_string(finding["metric"], f"invalid_metric:{index}")
        _mapping(finding["evidence"], f"invalid_evidence:{index}")
        if "unit" in finding and finding["unit"] is not None:
            _nonempty_string(finding["unit"], f"invalid_unit:{index}")
        if "affected_objects" in finding:
            objects = finding["affected_objects"]
            if not isinstance(objects, list) or any(
                not isinstance(item, str) or not item for item in objects
            ):
                raise AlertContractError(f"invalid_affected_objects:{index}")

    dedupe_key = _nonempty_string(value["dedupe_key"], "invalid_dedupe_key")
    if (
        any(character.isspace() for character in dedupe_key)
        or _TIMESTAMP_IN_KEY.search(dedupe_key)
        or alert_id in dedupe_key
        or audit_id in dedupe_key
    ):
        raise AlertContractError("unstable_dedupe_key")

    report = _mapping(value["report"], "invalid_report")
    missing_report = _REPORT_FIELDS - set(report)
    if missing_report:
        raise AlertContractError(
            "report_missing_fields:" + ",".join(sorted(missing_report))
        )
    if set(report) - _REPORT_FIELDS:
        raise AlertContractError("report_unknown_fields")
    report_json = _mapping(report["json"], "invalid_report_json")
    missing_collection = _COLLECTION_REQUIRED_FIELDS - set(report_json)
    if missing_collection:
        raise AlertContractError("incomplete_report_json")
    report_html = _nonempty_string(report["html"], "invalid_report_html")
    if "<html" not in report_html.lower() or "</html>" not in report_html.lower():
        raise AlertContractError("incomplete_report_html")

    collected_at = _utc_timestamp(report_json["collected_at"], "invalid_collected_at")
    detected_at = _utc_timestamp(value["detected_at"], "invalid_detected_at")
    generated_at = _utc_timestamp(
        report["report_generated_at"], "invalid_report_generated_at"
    )
    if detected_at < collected_at:
        raise AlertContractError("detected_before_collection")
    if generated_at != collected_at:
        raise AlertContractError("report_generated_at_mismatch")
    if report_json["audit_id"] != audit_id:
        raise AlertContractError("report_json_audit_id_mismatch")
    if audit_id not in report_html:
        raise AlertContractError("report_html_audit_id_mismatch")
    if report_json["collected_at"] not in report_html:
        raise AlertContractError("report_html_collected_at_mismatch")
    if report["report_format_version"] != report_json["schema_version"]:
        raise AlertContractError("report_format_version_mismatch")

    if "metadata" in value:
        _mapping(value["metadata"], "invalid_metadata")
    _check_sensitive(value)
