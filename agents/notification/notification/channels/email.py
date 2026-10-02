"""Single-attempt SMTP delivery with an escaped, locally owned template."""

from __future__ import annotations

import html
import json
import smtplib
import ssl
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta
from email.message import EmailMessage
from importlib.resources import files
from string import Template
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from notification.channels.base import NotificationChannel
from notification.config import NotificationSettings
from notification.domain import DeliveryResult


class SMTPClient(Protocol):
    def __enter__(self) -> SMTPClient: ...

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None: ...

    def starttls(self, *, context: ssl.SSLContext) -> object: ...

    def login(self, user: str, password: str) -> object: ...

    def send_message(self, message: EmailMessage) -> object: ...


SMTPFactory = Callable[..., SMTPClient]

_DISPLAY_TIMEZONE = ZoneInfo("America/Sao_Paulo")
_CATEGORY_LABELS = {
    "deadlock": "Deadlock",
    "lock_wait": "Espera por bloqueio",
    "query_latency": "Latência de consultas",
    "connections": "Conexões",
    "innodb": "InnoDB",
    "replication": "Replicação",
    "database_error": "Erro de banco",
    "destructive_ddl": "DDL destrutivo bloqueado",
    "schema_change": "Alteração estrutural bloqueada",
}
_SEVERITY_STYLES = {
    "info": ("#eff6ff", "#1e40af", "#2563eb"),
    "warning": ("#fff7ed", "#9a3412", "#f59e0b"),
    "critical": ("#fef2f2", "#991b1b", "#dc2626"),
}
_SEVERITY_LABELS = {
    "info": "INFORMATIVO",
    "warning": "ATENÇÃO",
    "critical": "CRÍTICO",
}
_METRIC_LABELS = {
    "avg_seconds": "Latência média",
    "p95_seconds": "Latência P95",
    "p99_seconds": "Latência P99",
    "connection_usage_pct": "Uso de conexões",
    "blocked_destructive_ddl_attempt": "Tentativa de DDL destrutivo bloqueada",
    "blocked_schema_change_attempt": "Tentativa de alteração estrutural bloqueada",
}
_EVIDENCE_LABELS = {
    "schema": "Schema",
    "query_name": "Query monitorada",
    "window_seconds": "Janela analisada",
    "executions": "Execuções na janela",
    "samples": "Amostras do percentil",
    "percentile_coverage": "Cobertura das amostras",
    "percentile_coverage_pct": "Cobertura das amostras",
    "digest": "Digest",
    "normalized_select": "SELECT normalizado",
    "occurred_at_utc": "Horário do evento",
    "event_key": "Chave do evento",
    "sql_command": "Comando normalizado",
    "outcome": "Resultado no MySQL",
    "status_code": "Código MySQL",
    "schema_scope": "Schema",
    "scope_evidence": "Evidência de escopo",
}
_HEALTH_EVIDENCE_ORDER = (
    "schema",
    "query_name",
    "window_seconds",
    "executions",
)
_AUDIT_EVIDENCE_ORDER = (
    "occurred_at_utc",
    "sql_command",
    "outcome",
    "status_code",
    "schema_scope",
)


def _header_text(value: Any) -> str:
    text = str(value)
    without_controls = "".join(
        " " if ord(char) < 32 or ord(char) == 127 else char for char in text
    )
    return " ".join(without_controls.split())


def _display_value(value: Any) -> str:
    rendered = (
        value
        if isinstance(value, str)
        else json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    )
    return rendered if len(rendered) <= 500 else rendered[:497] + "..."


def _metric_value(value: Any, unit: Any) -> str:
    rendered = _display_value(value)
    if unit == "percent":
        return f"{rendered}%"
    if unit == "seconds":
        return f"{rendered} s"
    if unit:
        return f"{rendered} {unit}"
    return rendered


def _evidence_value(key: str, value: Any) -> str:
    if key == "window_seconds":
        return _metric_value(value, "seconds")
    if key in {"percentile_coverage", "percentile_coverage_pct"}:
        return _metric_value(value, "percent")
    return _display_value(value)


def _evidence_order(source: str) -> tuple[str, ...]:
    if source == "audit-security":
        return _AUDIT_EVIDENCE_ORDER
    return _HEALTH_EVIDENCE_ORDER


def _evidence_html(evidence: Mapping[str, Any], source: str) -> str:
    if not evidence:
        return ""
    ordered_keys = [key for key in _evidence_order(source) if key in evidence]
    rows: list[str] = []
    for key in ordered_keys:
        label = html.escape(_EVIDENCE_LABELS.get(key, key), quote=True)
        value = html.escape(_evidence_value(key, evidence[key]), quote=True)
        rows.append(
            '<tr><td style="color:#6b7280;font-size:12px;padding:5px 8px 5px 0;'
            f'width:145px;">{label}</td><td style="color:#111827;font-size:13px;'
            f'padding:5px 0;">{value}</td></tr>'
        )
    return (
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" '
        'border="0" style="border-collapse:collapse;margin-top:12px;width:100%;">'
        f"{''.join(rows)}</table>"
    )


def _format_timestamp(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    local = parsed.astimezone(_DISPLAY_TIMEZONE)
    if local.utcoffset() == timedelta(hours=-3):
        abbreviation = "BRT"
    elif local.utcoffset() == timedelta(hours=-2):
        abbreviation = "BRST"
    else:
        abbreviation = local.tzname() or "America/Sao_Paulo"
    return f"{local:%d/%m/%Y %H:%M:%S} {abbreviation}"


def _findings_html(findings: Sequence[Mapping[str, Any]], source: str) -> str:
    cards: list[str] = []
    for finding in findings:
        metric_name = str(finding["metric"])
        metric = html.escape(_METRIC_LABELS.get(metric_name, metric_name), quote=True)
        unit = finding.get("unit")
        is_audit_event = metric_name in {
            "blocked_destructive_ddl_attempt",
            "blocked_schema_change_attempt",
        }
        observed = html.escape(
            "Evento detectado"
            if is_audit_event
            else _metric_value(finding["observed_value"], unit),
            quote=True,
        )
        threshold = finding.get("threshold")
        threshold_html = ""
        if threshold is not None and not is_audit_event:
            threshold_value = html.escape(_metric_value(threshold, unit), quote=True)
            threshold_html = (
                '<span style="color:#6b7280;font-size:13px;">Limite: '
                f'<strong style="color:#111827;">{threshold_value}</strong></span>'
            )
        evidence_html = _evidence_html(finding.get("evidence", {}), source)
        cards.append(
            '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" '
            'border="0" bgcolor="#f9fafb" style="background:#f9fafb;'
            'border-collapse:separate;margin:0 0 10px;width:100%;">'
            '<tr><td style="padding:16px;">'
            f'<div style="color:#6b7280;font-size:12px;">{metric}</div>'
            f'<div style="color:#111827;font-size:22px;font-weight:700;'
            f'margin-top:4px;">{observed}</div>'
            f'<div style="margin-top:5px;">{threshold_html}</div>{evidence_html}'
            "</td></tr></table>"
        )
    return "\n".join(cards)


def _render_html(alert: Mapping[str, Any]) -> str:
    source = str(alert["source"])
    template_path = files("notification.templates").joinpath("alert_email.html")
    template = Template(template_path.read_text(encoding="utf-8"))
    severity = str(alert["severity"])
    background, color, border = _SEVERITY_STYLES[severity]
    category = str(alert["category"])
    source_label = "Audit MySQL" if source == "audit-security" else "Health Check MySQL"
    values = {
        "severity": html.escape(_SEVERITY_LABELS[severity], quote=True),
        "severity_background": background,
        "severity_color": color,
        "severity_border": border,
        "source_label": source_label,
        "environment": html.escape(str(alert["environment"]), quote=True),
        "category_label": html.escape(_CATEGORY_LABELS[category], quote=True),
        "title": html.escape(str(alert["title"]), quote=True),
        "summary": html.escape(str(alert["summary"]), quote=True),
        "detected_at": html.escape(
            _format_timestamp(str(alert["detected_at"])), quote=True
        ),
        "alert_id": html.escape(str(alert["alert_id"]), quote=True),
        "findings_html": _findings_html(alert["findings"], source),
        "closing_note": (
            "O evento foi sanitizado. SQL literal, credenciais e identidades não "
            "fazem parte deste e-mail."
            if source == "audit-security"
            else (
                "O relatório completo está disponível na Central de ocorrências do DBA."
            )
        ),
    }
    return template.substitute(values)


def _plain_body(alert: Mapping[str, Any]) -> str:
    source = str(alert["source"])
    source_label = "Audit MySQL" if source == "audit-security" else "Health Check MySQL"
    findings = []
    for finding in alert["findings"]:
        metric_name = str(finding["metric"])
        is_audit_event = metric_name in {
            "blocked_destructive_ddl_attempt",
            "blocked_schema_change_attempt",
        }
        observed = (
            "Evento detectado"
            if is_audit_event
            else _metric_value(finding["observed_value"], finding.get("unit"))
        )
        threshold = finding.get("threshold")
        threshold_text = (
            f" | Limite: {_metric_value(threshold, finding.get('unit'))}"
            if threshold is not None and not is_audit_event
            else ""
        )
        evidence = finding.get("evidence", {})
        evidence_lines = [
            f"  {_EVIDENCE_LABELS.get(key, key)}: {_evidence_value(key, evidence[key])}"
            for key in [item for item in _evidence_order(source) if item in evidence]
        ]
        evidence_text = "\n" + "\n".join(evidence_lines) if evidence_lines else ""
        findings.append(
            f"- {_METRIC_LABELS.get(metric_name, metric_name)}: "
            f"{observed}{threshold_text}{evidence_text}"
        )
    findings_text = "\n".join(findings)
    return (
        f"{_SEVERITY_LABELS[str(alert['severity'])]} · {source_label}\n"
        f"{alert['title']}\n\n"
        f"{alert['summary']}\n\n"
        f"Ambiente: {alert['environment']}\n"
        f"Categoria: {_CATEGORY_LABELS[str(alert['category'])]}\n"
        f"Detectado em: {_format_timestamp(str(alert['detected_at']))}\n\n"
        f"Evidência:\n{findings_text}\n\n"
        + (
            "O evento foi sanitizado. SQL literal, credenciais e identidades não "
            "fazem parte deste e-mail.\n\n"
            if source == "audit-security"
            else "Consulte o relatório completo na Central de ocorrências do DBA.\n\n"
        )
        + f"Referência: {alert['alert_id']}\n"
    )


def _refactor_status_label(status: str) -> str:
    if status == "approved_lab":
        return "VALIDADA NO LABORATÓRIO"
    return "REQUER VALIDAÇÃO DO DBA"


def _render_refactor_html(result: Mapping[str, Any]) -> str:
    template_path = files("notification.templates").joinpath(
        "refactor_completion_email.html"
    )
    template = Template(template_path.read_text(encoding="utf-8"))
    timings = result["timings"]
    values = {
        "status": html.escape(
            _refactor_status_label(str(result["status"])), quote=True
        ),
        "query_id": html.escape(str(result["query_id"]), quote=True),
        "completed_at": html.escape(
            _format_timestamp(str(result["completed_at"])), quote=True
        ),
        "change_summary": html.escape(str(result["change_summary"]), quote=True),
        "before_seconds": html.escape(str(timings["before_seconds"]), quote=True),
        "after_seconds": html.escape(str(timings["after_seconds"]), quote=True),
        "improvement_percent": html.escape(
            str(timings["improvement_percent"]), quote=True
        ),
        "equivalent": "Sim" if result["validation"]["equivalent"] else "Não",
        "result_id": html.escape(str(result["result_id"]), quote=True),
    }
    return template.substitute(values)


def _refactor_plain_body(result: Mapping[str, Any]) -> str:
    timings = result["timings"]
    return (
        "REFACTOR MYSQL · CONCLUSÃO\n"
        f"{_refactor_status_label(str(result['status']))}\n\n"
        f"A análise da query {result['query_id']} foi concluída. "
        "O resultado está disponível para revisão na área Refactor do DBA.\n\n"
        f"Mudança: {result['change_summary']}\n"
        f"Antes: {timings['before_seconds']} s\n"
        f"Depois: {timings['after_seconds']} s\n"
        f"Melhoria no laboratório: {timings['improvement_percent']}%\n"
        f"Resultado equivalente: "
        f"{'sim' if result['validation']['equivalent'] else 'não'}\n"
        f"Concluído em: {_format_timestamp(str(result['completed_at']))}\n\n"
        "Nenhuma mudança foi aplicada em produção. O e-mail não contém SQL literal.\n\n"
        f"Referência: {result['result_id']}\n"
    )


class EmailChannel(NotificationChannel):
    """Build and send one e-mail without retries or filesystem persistence."""

    name = "email"

    def __init__(
        self,
        settings: NotificationSettings,
        smtp_factory: SMTPFactory = smtplib.SMTP,
    ) -> None:
        self._settings = settings
        self._smtp_factory = smtp_factory

    def send(
        self,
        alert: Mapping[str, Any],
        recipients: Sequence[str],
        *,
        high_priority: bool,
    ) -> DeliveryResult:
        alert_id = str(alert["alert_id"])
        if not recipients:
            return DeliveryResult(
                False,
                alert_id,
                self.name,
                "failed",
                error_code="recipients_not_configured",
            )

        config_error = self._settings.smtp_error_code()
        if config_error:
            return DeliveryResult(
                False,
                alert_id,
                self.name,
                "failed",
                error_code=config_error,
            )

        try:
            message = self.build_message(alert, recipients, high_priority=high_priority)
        except (TypeError, ValueError):
            return DeliveryResult(
                False,
                alert_id,
                self.name,
                "failed",
                error_code="email_configuration_invalid",
            )

        return self._deliver_message(alert_id, message, recipients)

    def send_refactor_completion(
        self,
        result: Mapping[str, Any],
        recipients: Sequence[str],
    ) -> DeliveryResult:
        """Send one concise completion notice without exposing either SQL text."""
        result_id = str(result["result_id"])
        if not recipients:
            return DeliveryResult(
                False,
                result_id,
                self.name,
                "failed",
                error_code="recipients_not_configured",
            )
        config_error = self._settings.smtp_error_code()
        if config_error:
            return DeliveryResult(
                False,
                result_id,
                self.name,
                "failed",
                error_code=config_error,
            )
        try:
            message = self.build_refactor_message(result, recipients)
        except (OSError, TypeError, ValueError):
            return DeliveryResult(
                False,
                result_id,
                self.name,
                "failed",
                error_code="email_configuration_invalid",
            )
        return self._deliver_message(result_id, message, recipients)

    def _deliver_message(
        self,
        reference_id: str,
        message: EmailMessage,
        recipients: Sequence[str],
    ) -> DeliveryResult:
        tls_context: ssl.SSLContext | None = None
        if self._settings.smtp_use_starttls:
            try:
                tls_context = ssl.create_default_context()
            except Exception:
                return DeliveryResult(
                    False,
                    reference_id,
                    self.name,
                    "failed",
                    error_code="tls_configuration_failed",
                )
            if (
                not tls_context.check_hostname
                or tls_context.verify_mode != ssl.CERT_REQUIRED
            ):
                return DeliveryResult(
                    False,
                    reference_id,
                    self.name,
                    "failed",
                    error_code="tls_certificate_validation_required",
                )

        try:
            with self._smtp_factory(
                self._settings.smtp_host,
                self._settings.smtp_port,
                timeout=self._settings.smtp_timeout_seconds,
            ) as smtp:
                if tls_context is not None:
                    smtp.starttls(context=tls_context)
                if self._settings.smtp_username and self._settings.smtp_password:
                    smtp.login(
                        self._settings.smtp_username,
                        self._settings.smtp_password,
                    )
                smtp.send_message(message)
        except Exception:
            return DeliveryResult(
                False,
                reference_id,
                self.name,
                "failed",
                error_code="smtp_delivery_failed",
            )

        return DeliveryResult(
            True,
            reference_id,
            self.name,
            "sent",
            recipient_count=len(recipients),
        )

    def build_message(
        self,
        alert: Mapping[str, Any],
        recipients: Sequence[str],
        *,
        high_priority: bool,
    ) -> EmailMessage:
        """Build an in-memory message from a previously validated alert."""
        product = (
            "Audit MySQL"
            if alert.get("source") == "audit-security"
            else "Health Check MySQL"
        )
        severity_label = _SEVERITY_LABELS[str(alert["severity"])]
        subject = (
            f"[{severity_label}] {product} · {alert['environment']} · {alert['title']}"
        )
        message = EmailMessage()
        message["Subject"] = _header_text(subject)
        message["From"] = self._settings.email_from
        message["To"] = ", ".join(recipients)
        if high_priority:
            message["X-Priority"] = "1"
            message["Priority"] = "urgent"
            message["Importance"] = "high"

        message.set_content(_plain_body(alert))
        message.add_alternative(_render_html(alert), subtype="html")
        return message

    def build_refactor_message(
        self,
        result: Mapping[str, Any],
        recipients: Sequence[str],
    ) -> EmailMessage:
        """Build an in-memory, SQL-free notice for one validated result."""
        status = _refactor_status_label(str(result["status"]))
        subject = f"[REFACTOR CONCLUÍDO] {result['query_id']} · {status}"
        message = EmailMessage()
        message["Subject"] = _header_text(subject)
        message["From"] = self._settings.email_from
        message["To"] = ", ".join(recipients)
        message.set_content(_refactor_plain_body(result))
        message.add_alternative(_render_refactor_html(result), subtype="html")
        return message
