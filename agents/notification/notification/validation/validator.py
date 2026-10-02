"""Revalidate schema, integrity, and sensitive-data rules before delivery."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

_SENSITIVE_KEYS = frozenset(
    {
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
)
_PRIVATE_IPV4 = re.compile(
    r"(?<!\d)(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|"
    r"192\.168\.\d{1,3}\.\d{1,3}\.\d{1,3}|"
    r"172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})(?!\d)"
)
_SECRET_TEXT = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"(?:password|passwd|pwd|token|api[_-]?key|client[_-]?secret)\s*[:=]\s*\S+|"
    r"(?:mysql|mariadb)://[^\s/:@]+:[^\s@]+@",
    re.IGNORECASE,
)
_TIMESTAMP_IN_KEY = re.compile(r"\d{4}-\d{2}-\d{2}(?:[T_][0-9:.+-]+Z?)?")
CONTRACTS = Path(__file__).resolve().parents[2] / "contracts"


class AlertValidationError(ValueError):
    """Raised with safe codes only; payload values are never embedded."""

    def __init__(self, codes: Iterable[str]) -> None:
        self.codes = tuple(dict.fromkeys(codes))
        super().__init__(",".join(self.codes))


_SUPPORTED_CONTRACTS = frozenset({"health_check_alert.v1", "audit_security_alert.v1"})


@lru_cache(maxsize=2)
def _schema(contract_version: str) -> dict[str, Any]:
    if contract_version == "health_check_alert.v1":
        path = CONTRACTS / "health_check_alert.v1.schema.json"
    elif contract_version == "audit_security_alert.v1":
        path = CONTRACTS / "audit_security_alert.v1.schema.json"
    else:
        raise ValueError("unsupported_contract_version")
    schema = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return schema


@lru_cache(maxsize=2)
def _validator(contract_version: str) -> Draft202012Validator:
    return Draft202012Validator(
        _schema(contract_version), format_checker=FormatChecker()
    )


def _schema_code(error: ValidationError) -> str:
    parts = list(error.absolute_path)
    if parts == ["contract_version"] and error.validator == "const":
        return "invalid_contract_version"
    if parts == ["severity"] and error.validator == "enum":
        return "invalid_severity"
    if parts == ["category"] and error.validator == "enum":
        return "invalid_category"
    if parts == ["source"] and error.validator == "const":
        return "invalid_source"
    if error.validator == "required":
        return "required_field_missing"
    if error.validator == "additionalProperties":
        return "unknown_field"
    if error.validator == "format":
        return "invalid_format"
    return "schema_validation_error"


def _sensitive_codes(value: Any) -> list[str]:
    codes: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            if normalized in _SENSITIVE_KEYS:
                codes.append("sensitive_field")
            else:
                codes.extend(_sensitive_codes(child))
    elif isinstance(value, list):
        for child in value:
            codes.extend(_sensitive_codes(child))
    elif isinstance(value, str) and (
        _PRIVATE_IPV4.search(value) or _SECRET_TEXT.search(value)
    ):
        codes.append("sensitive_value")
    return codes


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _integrity_codes(alert: dict[str, Any]) -> list[str]:
    codes: list[str] = []
    report = alert["report"]
    report_json = report["json"]
    report_html = report["html"]

    dedupe_key = alert["dedupe_key"]
    if (
        _TIMESTAMP_IN_KEY.search(dedupe_key)
        or alert["alert_id"] in dedupe_key
        or alert["audit_id"] in dedupe_key
    ):
        codes.append("unstable_dedupe_key")
    if "<html" not in report_html.lower() or "</html>" not in report_html.lower():
        codes.append("incomplete_report_html")
    if report_json["audit_id"] != alert["audit_id"]:
        codes.append("audit_id_mismatch")
    if alert["audit_id"] not in report_html:
        codes.append("report_html_audit_id_mismatch")
    if report_json["collected_at"] not in report_html:
        codes.append("report_html_collected_at_mismatch")
    if _parse_utc(alert["detected_at"]) < _parse_utc(report_json["collected_at"]):
        codes.append("detected_before_collection")
    if _parse_utc(report["report_generated_at"]) != _parse_utc(
        report_json["collected_at"]
    ):
        codes.append("report_generated_at_mismatch")
    if report["report_format_version"] != report_json["schema_version"]:
        codes.append("report_format_version_mismatch")
    return codes


def validate_alert(alert: Any) -> tuple[str, ...]:
    """Return stable error codes; an empty tuple means the alert is valid."""
    if not isinstance(alert, dict):
        return ("alert_must_be_object",)

    contract_version = alert.get("contract_version")
    if contract_version not in _SUPPORTED_CONTRACTS:
        return ("invalid_contract_version",)

    try:
        json.dumps(alert, allow_nan=False)
    except (TypeError, ValueError):
        return ("non_json_value",)

    schema_codes = [
        _schema_code(error)
        for error in sorted(
            _validator(contract_version).iter_errors(alert),
            key=lambda item: (
                tuple(str(part) for part in item.absolute_path),
                item.validator,
            ),
        )
    ]
    sensitive_codes = _sensitive_codes(alert)
    if schema_codes:
        integrity_codes: list[str] = []
    else:
        try:
            integrity_codes = _integrity_codes(alert)
        except (TypeError, ValueError):
            integrity_codes = ["timestamp_integrity_invalid"]
    return tuple(dict.fromkeys(schema_codes + sensitive_codes + integrity_codes))


def validate_alert_or_raise(alert: Any) -> None:
    codes = validate_alert(alert)
    if codes:
        raise AlertValidationError(codes)


def validate_refactor_result(result: Any) -> tuple[str, ...]:
    """Revalidate a Refactor result before creating a completion notice."""
    if not isinstance(result, dict):
        return ("result_must_be_object",)
    if result.get("contract_version") != "query_refactor_result.v1":
        return ("invalid_contract_version",)
    try:
        json.dumps(result, allow_nan=False)
    except (TypeError, ValueError):
        return ("non_json_value",)

    try:
        schema = json.loads(
            (CONTRACTS / "query_refactor_result.v1.schema.json").read_text(
                encoding="utf-8"
            )
        )
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        codes = [
            "schema_validation_error"
            for _ in sorted(
                validator.iter_errors(result),
                key=lambda item: tuple(str(part) for part in item.absolute_path),
            )
        ]
    except (OSError, TypeError, ValueError):
        return ("schema_validation_error",)
    if codes:
        return tuple(dict.fromkeys(codes))

    for field in ("original", "proposed"):
        digest = hashlib.sha256(result[f"{field}_sql"].encode("utf-8")).hexdigest()
        if digest != result[f"{field}_sql_sha256"]:
            codes.append("hash_mismatch")
    if result["status"] == "approved_lab":
        if not result["validation"]["equivalent"]:
            codes.append("approval_without_equivalence")
        if result["timings"]["after_seconds"] >= result["timings"]["before_seconds"]:
            codes.append("approval_without_gain")
    return tuple(dict.fromkeys(codes))
