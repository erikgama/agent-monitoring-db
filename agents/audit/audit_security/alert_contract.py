"""Validation for audit_security_alert.v1."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

BASE_DIR = Path(__file__).resolve().parents[1]
SCHEMA_PATH = BASE_DIR / "contracts" / "audit_security_alert.v1.schema.json"
_SENSITIVE_KEYS = {
    "password",
    "passwd",
    "pwd",
    "token",
    "access_token",
    "refresh_token",
    "api_key",
    "secret",
    "private_key",
    "connection_string",
    "dsn",
    "credentials",
    "endpoint",
}
_PRIVATE_IPV4 = re.compile(
    r"(?<!\d)(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|"
    r"192\.168\.\d{1,3}\.\d{1,3}|"
    r"172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})(?!\d)"
)
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}(?:[T_][0-9:.+-]+Z?)?")


class AlertValidationError(ValueError):
    def __init__(self, codes: list[str]) -> None:
        self.codes = sorted(set(codes))
        super().__init__(",".join(self.codes))


def _sensitive_codes(value: Any) -> list[str]:
    codes: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).lower().replace("-", "_")
            if normalized in _SENSITIVE_KEYS:
                codes.append("sensitive_field")
            else:
                codes.extend(_sensitive_codes(child))
    elif isinstance(value, list):
        for child in value:
            codes.extend(_sensitive_codes(child))
    elif isinstance(value, str) and _PRIVATE_IPV4.search(value):
        codes.append("sensitive_value")
    return codes


def validate_alert(alert: dict[str, Any]) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    codes = ["schema_validation_error" for _ in validator.iter_errors(alert)]
    codes.extend(_sensitive_codes(alert))
    if not codes:
        report = alert["report"]
        report_json = report["json"]
        report_html = report["html"]
        if report_json["audit_id"] != alert["audit_id"]:
            codes.append("audit_id_mismatch")
        if alert["audit_id"] not in report_html:
            codes.append("report_html_audit_id_mismatch")
        if report_json["collected_at"] not in report_html:
            codes.append("report_html_collected_at_mismatch")
        if datetime.fromisoformat(alert["detected_at"]) < datetime.fromisoformat(
            report_json["collected_at"]
        ):
            codes.append("detected_before_collection")
        if report["report_generated_at"] != report_json["collected_at"]:
            codes.append("report_generated_at_mismatch")
        if report["report_format_version"] != report_json["schema_version"]:
            codes.append("report_format_version_mismatch")
        dedupe = alert["dedupe_key"]
        if (
            _TIMESTAMP.search(dedupe)
            or alert["alert_id"] in dedupe
            or alert["audit_id"] in dedupe
        ):
            codes.append("unstable_dedupe_key")
    if codes:
        raise AlertValidationError(codes)
