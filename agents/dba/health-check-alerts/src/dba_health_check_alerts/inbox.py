"""Durable, read-only incident inbox owned by the DBA agent."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

RECORD_VERSION = "dba_health_check_incident.v2"
ALLOWED_SCHEMAS = frozenset({"sakila"})
CONTRACT_VERSION = "health_check_alert.v1"
SOURCE = "health-check"
CATEGORIES = {
    "deadlock",
    "lock_wait",
    "query_latency",
    "connections",
    "innodb",
    "replication",
    "database_error",
}

_SAFE_RECORD_ID = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}-[0-9]{2}-[0-9]{2}"
    r"\.[0-9]{6}Z__[a-z_]+__[0-9a-f-]{36}$"
)
_ALERT_FILE = "alert.json"
_REPORT_JSON_FILE = "latest.json"
_REPORT_HTML_FILE = "latest.html"


class DbaAlertInboxError(ValueError):
    """Safe validation or persistence failure with a stable code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class DbaAlertReceipt:
    target: str
    status: str
    recorded: bool
    record_id: str
    directory: str
    alert_file: str
    report_json_file: str
    report_html_file: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def default_inbox_directory() -> Path:
    return Path(__file__).resolve().parents[2] / "runtime" / "inbox"


def _parse_datetime(value: Any, code: str) -> datetime:
    if not isinstance(value, str):
        raise DbaAlertInboxError(code)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise DbaAlertInboxError(code) from error
    if parsed.tzinfo is None:
        raise DbaAlertInboxError(code)
    return parsed.astimezone(UTC)


def _uuid(value: Any, code: str) -> str:
    if not isinstance(value, str):
        raise DbaAlertInboxError(code)
    try:
        parsed = uuid.UUID(value)
    except ValueError as error:
        raise DbaAlertInboxError(code) from error
    return str(parsed)


def _validated_identity(
    alert: Any,
) -> tuple[str, str, datetime, str, dict[str, Any], str]:
    if not isinstance(alert, dict):
        raise DbaAlertInboxError("dba_alert_invalid")
    if alert.get("contract_version") != CONTRACT_VERSION:
        raise DbaAlertInboxError("dba_contract_version_invalid")
    if alert.get("source") != SOURCE:
        raise DbaAlertInboxError("dba_alert_source_invalid")

    alert_id = _uuid(alert.get("alert_id"), "dba_alert_id_invalid")
    audit_id = _uuid(alert.get("audit_id"), "dba_audit_id_invalid")
    detected_at = _parse_datetime(alert.get("detected_at"), "dba_detected_at_invalid")
    category = alert.get("category")
    if category not in CATEGORIES:
        raise DbaAlertInboxError("dba_category_invalid")

    findings = alert.get("findings")
    if not isinstance(findings, list) or not findings:
        raise DbaAlertInboxError("dba_findings_missing")

    report = alert.get("report")
    if not isinstance(report, dict):
        raise DbaAlertInboxError("dba_report_missing")
    report_json = report.get("json")
    report_html = report.get("html")
    if not isinstance(report_json, dict) or not isinstance(report_html, str):
        raise DbaAlertInboxError("dba_report_incomplete")
    report_audit_id = _uuid(
        report_json.get("audit_id"),
        "dba_report_audit_id_invalid",
    )
    if report_audit_id != audit_id or str(alert["audit_id"]) not in report_html:
        raise DbaAlertInboxError("dba_audit_id_mismatch")

    scope = report_json.get("scope")
    schemas = scope.get("schemas") if isinstance(scope, dict) else None
    if not isinstance(schemas, list) or not schemas:
        raise DbaAlertInboxError("dba_schema_scope_missing")
    if any(schema not in ALLOWED_SCHEMAS for schema in schemas):
        raise DbaAlertInboxError("dba_schema_scope_not_allowed")

    return alert_id, audit_id, detected_at, category, report_json, report_html


def _atomic_write(path: Path, content: str) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _build_alert_reference(alert: dict[str, Any]) -> dict[str, Any]:
    report = alert["report"]
    report_reference = {
        key: value for key, value in report.items() if key not in {"json", "html"}
    }
    report_reference.update(
        {
            "json_file": _REPORT_JSON_FILE,
            "html_file": _REPORT_HTML_FILE,
        }
    )
    return {
        **{key: value for key, value in alert.items() if key != "report"},
        "report": report_reference,
    }


class DbaAlertInbox:
    """Record one immutable technical incident per MCP alert ID."""

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = (directory or default_inbox_directory()).resolve()

    @staticmethod
    def _receipt(record_id: str, *, status: str, recorded: bool) -> DbaAlertReceipt:
        return DbaAlertReceipt(
            target="dba",
            status=status,
            recorded=recorded,
            record_id=record_id,
            directory=record_id,
            alert_file=f"{record_id}/{_ALERT_FILE}",
            report_json_file=f"{record_id}/{_REPORT_JSON_FILE}",
            report_html_file=f"{record_id}/{_REPORT_HTML_FILE}",
        )

    @staticmethod
    def _is_complete(record_directory: Path) -> bool:
        return record_directory.is_dir() and all(
            (record_directory / name).is_file()
            for name in (_ALERT_FILE, _REPORT_JSON_FILE, _REPORT_HTML_FILE)
        )

    def record(self, alert: dict[str, Any]) -> DbaAlertReceipt:
        alert_id, _, detected_at, category, report_json, report_html = (
            _validated_identity(alert)
        )
        self.directory.mkdir(parents=True, exist_ok=True)

        stamp = detected_at.strftime("%Y-%m-%dT%H-%M-%S.%fZ")
        record_id = f"{stamp}__{category}__{alert_id}"
        if not _SAFE_RECORD_ID.fullmatch(record_id):
            raise DbaAlertInboxError("dba_record_id_invalid")
        record_directory = self.directory / record_id

        if self._is_complete(record_directory):
            return self._receipt(record_id, status="duplicate", recorded=False)
        if record_directory.exists():
            raise DbaAlertInboxError("dba_record_incomplete")

        received_at = (
            datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        )
        record = {
            "record_version": RECORD_VERSION,
            "received_at": received_at,
            "alert": _build_alert_reference(alert),
        }
        staging_directory = Path(
            tempfile.mkdtemp(
                dir=self.directory,
                prefix=f".{record_id}.",
                suffix=".tmp",
            )
        )
        try:
            _atomic_write(
                staging_directory / _ALERT_FILE,
                json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            )
            _atomic_write(
                staging_directory / _REPORT_JSON_FILE,
                json.dumps(report_json, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n",
            )
            _atomic_write(staging_directory / _REPORT_HTML_FILE, report_html)
            try:
                os.rename(staging_directory, record_directory)
            except OSError as error:
                if self._is_complete(record_directory):
                    return self._receipt(
                        record_id,
                        status="duplicate",
                        recorded=False,
                    )
                raise DbaAlertInboxError("dba_persistence_failed") from error
        finally:
            if staging_directory.exists():
                shutil.rmtree(staging_directory)

        return self._receipt(record_id, status="recorded", recorded=True)
