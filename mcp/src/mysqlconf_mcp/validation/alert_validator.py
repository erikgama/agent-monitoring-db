"""Schema, integrity, and sensitive-data validation for Health Check alerts."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from datetime import datetime
from functools import lru_cache
from importlib.resources import files
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
    r"192\.168\.\d{1,3}\.\d{1,3}|"
    r"172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})(?!\d)"
)
_SECRET_TEXT = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"(?:password|passwd|pwd|token|api[_-]?key|client[_-]?secret)\s*[:=]\s*\S+|"
    r"(?:mysql|mariadb)://[^\s/:@]+:[^\s@]+@",
    re.IGNORECASE,
)
_TIMESTAMP_IN_KEY = re.compile(r"\d{4}-\d{2}-\d{2}(?:[T_][0-9:.+-]+Z?)?")


def _error(field: str, code: str, message: str) -> dict[str, str]:
    return {"field": field, "code": code, "message": message}


_SUPPORTED_CONTRACTS = frozenset({"health_check_alert.v1", "audit_security_alert.v1"})


@lru_cache(maxsize=2)
def _schema(contract_version: str) -> dict[str, Any]:
    if contract_version == "health_check_alert.v1":
        schema_path = files("mysqlconf_mcp.contracts").joinpath(
            "health_check_alert.v1.schema.json"
        )
    elif contract_version == "audit_security_alert.v1":
        schema_path = files("mysqlconf_mcp.contracts").joinpath(
            "audit_security_alert.v1.schema.json"
        )
    else:
        raise ValueError("unsupported_contract_version")
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return schema


@lru_cache(maxsize=2)
def _validator(contract_version: str) -> Draft202012Validator:
    return Draft202012Validator(
        _schema(contract_version), format_checker=FormatChecker()
    )


def contract_allows_value(field: str, value: str) -> bool:
    """Check a closed value across every supported alert contract."""
    for contract_version in _SUPPORTED_CONTRACTS:
        definition = _schema(contract_version)["properties"].get(field, {})
        if "enum" in definition and value in definition["enum"]:
            return True
        if "const" in definition and value == definition["const"]:
            return True
    return False


def _field_path(parts: Iterable[object]) -> str:
    path = "alert"
    for part in parts:
        path += f"[{part}]" if isinstance(part, int) else f".{part}"
    return path


def _child_path(path: str, key: object) -> str:
    text = str(key)
    safe_key = text if re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", text) else "<field>"
    return f"{path}.{safe_key}"


def _required_property(error: ValidationError) -> str | None:
    match = re.match(r"^'([^']+)' is a required property$", error.message)
    return match.group(1) if match else None


def _schema_error(error: ValidationError) -> dict[str, str]:
    parts = list(error.absolute_path)
    missing = _required_property(error) if error.validator == "required" else None
    if missing:
        parts.append(missing)
    field = _field_path(parts)

    if parts == ["contract_version"] and error.validator == "const":
        return _error(
            field,
            "invalid_contract_version",
            "contract_version deve ser uma versão de alerta suportada.",
        )
    if parts == ["severity"] and error.validator == "enum":
        return _error(
            field,
            "invalid_severity",
            "Severidade fora dos valores permitidos pelo contrato.",
        )
    if parts == ["category"] and error.validator == "enum":
        return _error(
            field,
            "invalid_category",
            "Categoria fora dos valores permitidos pelo contrato.",
        )
    if parts == ["source"] and error.validator == "const":
        return _error(field, "invalid_source", "A origem não corresponde ao contrato.")
    if error.validator == "required":
        return _error(field, "required_field_missing", "Campo obrigatório ausente.")
    if error.validator == "additionalProperties":
        return _error(
            field, "unknown_field", "O objeto contém campo não previsto pelo contrato."
        )
    if error.validator == "format":
        return _error(field, "invalid_format", "Campo com formato inválido.")
    return _error(
        field,
        "schema_validation_error",
        "Campo inválido segundo o contrato de alerta selecionado.",
    )


def _sensitive_errors(value: Any, path: str = "alert") -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = _child_path(path, key)
            normalized = str(key).strip().lower().replace("-", "_")
            if normalized in _SENSITIVE_KEYS:
                errors.append(
                    _error(
                        child_path,
                        "sensitive_field",
                        "O payload contém um campo sensível não permitido.",
                    )
                )
            else:
                errors.extend(_sensitive_errors(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            errors.extend(_sensitive_errors(child, f"{path}[{index}]"))
    elif isinstance(value, str) and (
        _PRIVATE_IPV4.search(value) or _SECRET_TEXT.search(value)
    ):
        errors.append(
            _error(
                path,
                "sensitive_value",
                "O payload contém um valor sensível não permitido.",
            )
        )
    return errors


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _integrity_errors(alert: dict[str, Any]) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    report = alert["report"]
    report_json = report["json"]
    report_html = report["html"]

    dedupe_key = alert["dedupe_key"]
    if (
        _TIMESTAMP_IN_KEY.search(dedupe_key)
        or alert["alert_id"] in dedupe_key
        or alert["audit_id"] in dedupe_key
    ):
        errors.append(
            _error(
                "alert.dedupe_key",
                "unstable_dedupe_key",
                "dedupe_key não pode conter timestamp, alert_id ou audit_id.",
            )
        )

    if "<html" not in report_html.lower() or "</html>" not in report_html.lower():
        errors.append(
            _error(
                "alert.report.html",
                "incomplete_report_html",
                "O relatório HTML deve conter um documento HTML completo.",
            )
        )
    if report_json["audit_id"] != alert["audit_id"]:
        errors.append(
            _error(
                "alert.report.json.audit_id",
                "audit_id_mismatch",
                "O audit_id do relatório JSON não corresponde ao audit_id do alerta.",
            )
        )
    if alert["audit_id"] not in report_html:
        errors.append(
            _error(
                "alert.report.html",
                "report_html_audit_id_mismatch",
                "O relatório HTML não corresponde ao audit_id do alerta.",
            )
        )
    if report_json["collected_at"] not in report_html:
        errors.append(
            _error(
                "alert.report.html",
                "report_html_collected_at_mismatch",
                "O relatório HTML não corresponde ao horário da coleta JSON.",
            )
        )
    if _parse_utc(alert["detected_at"]) < _parse_utc(report_json["collected_at"]):
        errors.append(
            _error(
                "alert.detected_at",
                "detected_before_collection",
                "detected_at não pode ser anterior a collected_at.",
            )
        )
    if _parse_utc(report["report_generated_at"]) != _parse_utc(
        report_json["collected_at"]
    ):
        errors.append(
            _error(
                "alert.report.report_generated_at",
                "report_generated_at_mismatch",
                "report_generated_at deve corresponder a collected_at.",
            )
        )
    if report["report_format_version"] != report_json["schema_version"]:
        errors.append(
            _error(
                "alert.report.report_format_version",
                "report_format_version_mismatch",
                "report_format_version deve corresponder a schema_version.",
            )
        )
    return errors


def validate_alert(alert: Any) -> list[dict[str, str]]:
    """Return safe structured errors; an empty list means the alert is valid."""
    if not isinstance(alert, dict):
        return [
            _error("alert", "alert_must_be_object", "O alerta deve ser um objeto JSON.")
        ]

    contract_version = alert.get("contract_version")
    if contract_version not in _SUPPORTED_CONTRACTS:
        return [
            _error(
                "alert.contract_version",
                "invalid_contract_version",
                "Versão de contrato não suportada.",
            )
        ]

    schema_errors = [
        _schema_error(error)
        for error in sorted(
            _validator(contract_version).iter_errors(alert),
            key=lambda item: (
                tuple(str(part) for part in item.absolute_path),
                item.message,
            ),
        )
    ]
    sensitive_errors = _sensitive_errors(alert)
    integrity_errors = _integrity_errors(alert) if not schema_errors else []

    unique: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for error in schema_errors + sensitive_errors + integrity_errors:
        identity = (error["field"], error["code"])
        if identity not in seen:
            seen.add(identity)
            unique.append(error)
    return unique
