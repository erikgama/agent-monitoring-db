"""Durable inbox containing only sanitized Audit Security summaries."""

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

RECORD_VERSION = "dba_audit_security_incident.v1"
CONTRACT_VERSION = "audit_security_alert.v1"
SOURCE = "audit-security"
ALLOWED_SCHEMAS = frozenset({"sakila"})
ALLOWED_SEVERITIES = frozenset({"info", "warning", "critical"})
ALLOWED_CATEGORIES = frozenset(
    {
        "destructive_ddl",
        "schema_change",
    }
)
_EVENT_KEY = re.compile(r"^sha256:[0-9a-f]{64}$")
_RECORD_ID = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}-[0-9]{2}-[0-9]{2}"
    r"\.[0-9]{6}Z__[a-z_]+__[0-9a-f-]{36}$"
)
_ALERT_SUMMARY = "alert-summary.json"
_EVENT_SUMMARY = "audit-event-summary.json"
_EVENT_EVIDENCE_FIELDS = (
    "occurred_at_utc",
    "event_key",
    "sql_command",
    "outcome",
    "status_code",
    "schema_scope",
    "scope_evidence",
)


class DbaAuditSecurityInboxError(ValueError):
    """Safe validation or persistence failure with a stable code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class DbaAuditSecurityReceipt:
    target: str
    status: str
    recorded: bool
    record_id: str
    directory: str
    alert_summary_file: str
    event_summary_file: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class _ValidatedAlert:
    alert_id: str
    audit_id: str
    detected_at: datetime
    severity: str
    category: str
    finding: dict[str, Any]
    evidence: dict[str, Any]
    event_key: str


def default_inbox_directory() -> Path:
    return Path(__file__).resolve().parents[2] / "runtime" / "inbox"


def _parse_datetime(value: Any, code: str) -> datetime:
    if not isinstance(value, str):
        raise DbaAuditSecurityInboxError(code)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise DbaAuditSecurityInboxError(code) from error
    if parsed.tzinfo is None:
        raise DbaAuditSecurityInboxError(code)
    return parsed.astimezone(UTC)


def _uuid(value: Any, code: str) -> str:
    if not isinstance(value, str):
        raise DbaAuditSecurityInboxError(code)
    try:
        return str(uuid.UUID(value))
    except ValueError as error:
        raise DbaAuditSecurityInboxError(code) from error


def _validate(alert: Any) -> _ValidatedAlert:
    if not isinstance(alert, dict):
        raise DbaAuditSecurityInboxError("dba_audit_alert_invalid")
    if alert.get("contract_version") != CONTRACT_VERSION:
        raise DbaAuditSecurityInboxError("dba_audit_contract_invalid")
    if alert.get("source") != SOURCE:
        raise DbaAuditSecurityInboxError("dba_audit_source_invalid")

    alert_id = _uuid(alert.get("alert_id"), "dba_audit_alert_id_invalid")
    audit_id = _uuid(alert.get("audit_id"), "dba_audit_id_invalid")
    detected_at = _parse_datetime(
        alert.get("detected_at"), "dba_audit_detected_at_invalid"
    )
    severity = alert.get("severity")
    category = alert.get("category")
    if severity not in ALLOWED_SEVERITIES:
        raise DbaAuditSecurityInboxError("dba_audit_severity_invalid")
    if category not in ALLOWED_CATEGORIES:
        raise DbaAuditSecurityInboxError("dba_audit_category_invalid")

    findings = alert.get("findings")
    if not isinstance(findings, list) or len(findings) != 1:
        raise DbaAuditSecurityInboxError("dba_audit_finding_invalid")
    finding = findings[0]
    evidence = finding.get("evidence") if isinstance(finding, dict) else None
    if not isinstance(evidence, dict):
        raise DbaAuditSecurityInboxError("dba_audit_evidence_invalid")

    metadata = alert.get("metadata")
    metadata_key = metadata.get("event_key") if isinstance(metadata, dict) else None
    evidence_key = evidence.get("event_key")
    if (
        not isinstance(metadata_key, str)
        or not _EVENT_KEY.fullmatch(metadata_key)
        or evidence_key != metadata_key
        or not str(alert.get("dedupe_key", "")).endswith(metadata_key)
    ):
        raise DbaAuditSecurityInboxError("dba_audit_event_key_invalid")

    report = alert.get("report")
    report_json = report.get("json") if isinstance(report, dict) else None
    if not isinstance(report_json, dict):
        raise DbaAuditSecurityInboxError("dba_audit_report_invalid")
    if _uuid(report_json.get("audit_id"), "dba_audit_report_id_invalid") != audit_id:
        raise DbaAuditSecurityInboxError("dba_audit_id_mismatch")
    scope = report_json.get("scope")
    schemas = scope.get("functional_schemas") if isinstance(scope, dict) else None
    if not isinstance(schemas, list) or not schemas:
        raise DbaAuditSecurityInboxError("dba_audit_schema_scope_missing")
    if any(schema not in ALLOWED_SCHEMAS for schema in schemas):
        raise DbaAuditSecurityInboxError("dba_audit_schema_scope_not_allowed")
    if evidence.get("schema_scope") not in ALLOWED_SCHEMAS:
        raise DbaAuditSecurityInboxError("dba_audit_event_schema_not_allowed")

    return _ValidatedAlert(
        alert_id,
        audit_id,
        detected_at,
        str(severity),
        str(category),
        finding,
        evidence,
        metadata_key,
    )


def _atomic_write(path: Path, value: dict[str, Any]) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", text=True
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


class DbaAuditSecurityInbox:
    """Record one immutable, sanitized event summary per Audit event key."""

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = (directory or default_inbox_directory()).resolve()

    @staticmethod
    def _receipt(
        record_id: str, *, status: str, recorded: bool
    ) -> DbaAuditSecurityReceipt:
        return DbaAuditSecurityReceipt(
            target="dba",
            status=status,
            recorded=recorded,
            record_id=record_id,
            directory=record_id,
            alert_summary_file=f"{record_id}/{_ALERT_SUMMARY}",
            event_summary_file=f"{record_id}/{_EVENT_SUMMARY}",
        )

    @staticmethod
    def _complete(path: Path) -> bool:
        return path.is_dir() and all(
            (path / name).is_file() for name in (_ALERT_SUMMARY, _EVENT_SUMMARY)
        )

    def _existing_for(self, event_key: str) -> str | None:
        if not self.directory.exists():
            return None
        for path in sorted(self.directory.iterdir()):
            if not self._complete(path) or not _RECORD_ID.fullmatch(path.name):
                continue
            try:
                summary = json.loads(
                    (path / _EVENT_SUMMARY).read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError):
                continue
            if summary.get("event_key") == event_key:
                return path.name
        return None

    def record(self, alert: dict[str, Any]) -> DbaAuditSecurityReceipt:
        value = _validate(alert)
        self.directory.mkdir(parents=True, exist_ok=True)
        existing = self._existing_for(value.event_key)
        if existing is not None:
            return self._receipt(existing, status="duplicate", recorded=False)

        stamp = value.detected_at.strftime("%Y-%m-%dT%H-%M-%S.%fZ")
        record_id = f"{stamp}__{value.category}__{value.alert_id}"
        if not _RECORD_ID.fullmatch(record_id):
            raise DbaAuditSecurityInboxError("dba_audit_record_id_invalid")
        record_directory = self.directory / record_id
        if record_directory.exists():
            raise DbaAuditSecurityInboxError("dba_audit_record_incomplete")

        now = (
            datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        )
        alert_summary = {
            "record_version": RECORD_VERSION,
            "received_at": now,
            "contract_version": CONTRACT_VERSION,
            "alert_id": value.alert_id,
            "audit_id": value.audit_id,
            "detected_at": alert["detected_at"],
            "environment": alert["environment"],
            "source": SOURCE,
            "severity": value.severity,
            "category": value.category,
            "title": alert["title"],
            "summary": alert["summary"],
            "dedupe_key": alert["dedupe_key"],
            "rule_id": value.finding["check_id"],
            "report_generated_at": alert["report"]["report_generated_at"],
            "report_format_version": alert["report"]["report_format_version"],
            "payload_policy": "sanitized_summary_only",
        }
        event_summary = {
            "record_version": RECORD_VERSION,
            "alert_id": value.alert_id,
            "audit_id": value.audit_id,
            "rule_id": value.finding["check_id"],
            "metric": value.finding["metric"],
            "observed_value": value.finding["observed_value"],
            "affected_objects": value.finding.get("affected_objects", []),
            **{
                key: value.evidence[key]
                for key in _EVENT_EVIDENCE_FIELDS
                if key in value.evidence
            },
            "sanitization": {
                "raw_sql_persisted": False,
                "credentials_persisted": False,
                "identities_persisted": False,
                "full_report_persisted": False,
            },
        }

        staging = Path(
            tempfile.mkdtemp(dir=self.directory, prefix=f".{record_id}.", suffix=".tmp")
        )
        try:
            _atomic_write(staging / _ALERT_SUMMARY, alert_summary)
            _atomic_write(staging / _EVENT_SUMMARY, event_summary)
            try:
                os.rename(staging, record_directory)
            except OSError as error:
                existing = self._existing_for(value.event_key)
                if existing is not None:
                    return self._receipt(existing, status="duplicate", recorded=False)
                raise DbaAuditSecurityInboxError(
                    "dba_audit_persistence_failed"
                ) from error
        finally:
            if staging.exists():
                shutil.rmtree(staging)
        return self._receipt(record_id, status="recorded", recorded=True)
