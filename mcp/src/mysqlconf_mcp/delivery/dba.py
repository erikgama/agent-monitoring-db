"""Adapter from the MCP boundary to the DBA private alert inbox."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from dba_audit_security_alerts import (
    DbaAuditSecurityInbox,
    DbaAuditSecurityInboxError,
)
from dba_health_check_alerts import DbaAlertInbox, DbaAlertInboxError

_SAFE_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_RECORD_ID_PATTERN = (
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}-[0-9]{2}-[0-9]{2}"
    r"\.[0-9]{6}Z__[a-z_]+__[0-9a-f-]{36}"
)
_SAFE_RECORD_ID = re.compile(rf"{_RECORD_ID_PATTERN}$")
_SAFE_RELATIVE_FILE = re.compile(
    rf"(?P<directory>{_RECORD_ID_PATTERN})/"
    r"(?:alert\.json|latest\.json|latest\.html)$"
)
_SAFE_AUDIT_SUMMARY_FILE = re.compile(
    rf"(?P<directory>{_RECORD_ID_PATTERN})/"
    r"(?:alert-summary\.json|audit-event-summary\.json)$"
)


@dataclass(frozen=True, slots=True)
class DbaDelivery:
    target: str
    status: str
    recorded: bool | None = None
    record_id: str | None = None
    directory: str | None = None
    alert_file: str | None = None
    report_json_file: str | None = None
    report_html_file: str | None = None
    alert_summary_file: str | None = None
    event_summary_file: str | None = None
    error_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value is not None}

    @classmethod
    def failed(cls, error_code: str) -> DbaDelivery:
        safe = error_code if _SAFE_CODE.fullmatch(error_code) else "dba_delivery_failed"
        return cls(target="dba", status="failed", recorded=False, error_code=safe)


class DbaGateway(Protocol):
    def deliver(self, alert: dict[str, Any]) -> DbaDelivery: ...


class DbaAdapterError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class DbaInboxAdapter:
    def __init__(
        self,
        health_check_inbox: DbaAlertInbox,
        audit_security_inbox: DbaAuditSecurityInbox,
    ) -> None:
        self._health_check_inbox = health_check_inbox
        self._audit_security_inbox = audit_security_inbox

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> DbaInboxAdapter:
        source = os.environ if environ is None else environ
        health_configured = source.get("MCP_DBA_ALERTS_DIR")
        audit_configured = source.get("MCP_DBA_AUDIT_ALERTS_DIR")
        health_directory = (
            Path(health_configured).expanduser() if health_configured else None
        )
        audit_directory = (
            Path(audit_configured).expanduser() if audit_configured else None
        )
        if health_directory is not None and not health_directory.is_absolute():
            raise DbaAdapterError("dba_alerts_directory_invalid")
        if audit_directory is not None and not audit_directory.is_absolute():
            raise DbaAdapterError("dba_audit_alerts_directory_invalid")
        return cls(
            DbaAlertInbox(health_directory),
            DbaAuditSecurityInbox(audit_directory),
        )

    def deliver(self, alert: dict[str, Any]) -> DbaDelivery:
        if alert.get("contract_version") == "audit_security_alert.v1":
            return self._deliver_audit_security(alert)
        return self._deliver_health_check(alert)

    def _deliver_health_check(self, alert: dict[str, Any]) -> DbaDelivery:
        try:
            receipt = self._health_check_inbox.record(alert)
        except DbaAlertInboxError as error:
            return DbaDelivery.failed(error.code)
        except OSError:
            return DbaDelivery.failed("dba_persistence_failed")

        if (
            receipt.target != "dba"
            or receipt.status not in {"recorded", "duplicate"}
            or not _SAFE_RECORD_ID.fullmatch(receipt.record_id)
            or receipt.directory != receipt.record_id
            or not _SAFE_RECORD_ID.fullmatch(receipt.directory)
            or not _SAFE_RELATIVE_FILE.fullmatch(receipt.alert_file)
            or not _SAFE_RELATIVE_FILE.fullmatch(receipt.report_json_file)
            or not _SAFE_RELATIVE_FILE.fullmatch(receipt.report_html_file)
            or receipt.alert_file != f"{receipt.directory}/alert.json"
            or receipt.report_json_file != f"{receipt.directory}/latest.json"
            or receipt.report_html_file != f"{receipt.directory}/latest.html"
        ):
            return DbaDelivery.failed("dba_invalid_result")
        return DbaDelivery(
            target="dba",
            status=receipt.status,
            recorded=receipt.recorded,
            record_id=receipt.record_id,
            directory=receipt.directory,
            alert_file=receipt.alert_file,
            report_json_file=receipt.report_json_file,
            report_html_file=receipt.report_html_file,
        )

    def _deliver_audit_security(self, alert: dict[str, Any]) -> DbaDelivery:
        try:
            receipt = self._audit_security_inbox.record(alert)
        except DbaAuditSecurityInboxError as error:
            return DbaDelivery.failed(error.code)
        except OSError:
            return DbaDelivery.failed("dba_audit_persistence_failed")

        if (
            receipt.target != "dba"
            or receipt.status not in {"recorded", "duplicate"}
            or not _SAFE_RECORD_ID.fullmatch(receipt.record_id)
            or receipt.directory != receipt.record_id
            or not _SAFE_RECORD_ID.fullmatch(receipt.directory)
            or not _SAFE_AUDIT_SUMMARY_FILE.fullmatch(receipt.alert_summary_file)
            or not _SAFE_AUDIT_SUMMARY_FILE.fullmatch(receipt.event_summary_file)
            or receipt.alert_summary_file != f"{receipt.directory}/alert-summary.json"
            or receipt.event_summary_file
            != f"{receipt.directory}/audit-event-summary.json"
        ):
            return DbaDelivery.failed("dba_invalid_result")
        return DbaDelivery(
            target="dba",
            status=receipt.status,
            recorded=receipt.recorded,
            record_id=receipt.record_id,
            directory=receipt.directory,
            alert_summary_file=receipt.alert_summary_file,
            event_summary_file=receipt.event_summary_file,
        )
