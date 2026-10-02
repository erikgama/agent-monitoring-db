from __future__ import annotations

import hashlib
import json
import logging
import ssl
import unittest
import uuid
from contextlib import redirect_stderr
from copy import deepcopy
from email.message import EmailMessage
from io import StringIO
from pathlib import Path
from typing import Any

from notification.channels import EmailChannel, NotificationChannel
from notification.config import NotificationSettings
from notification.demo import ManualTestError
from notification.demo import run as run_manual_test
from notification.dispatcher import NotificationDispatcher
from notification.domain import DeliveryResult
from notification.refactor_dispatcher import RefactorCompletionDispatcher

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures" / "connection-warning.json"
QUERY_LATENCY_FIXTURE = ROOT / "fixtures" / "query-latency-critical.json"
AUDIT_FIXTURE = ROOT / "fixtures" / "audit-destructive-ddl-critical.json"


def load_alert() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def load_query_latency_alert() -> dict[str, Any]:
    return json.loads(QUERY_LATENCY_FIXTURE.read_text(encoding="utf-8"))


def load_audit_alert() -> dict[str, Any]:
    return json.loads(AUDIT_FIXTURE.read_text(encoding="utf-8"))


def refactor_result() -> dict[str, Any]:
    original = "SELECT customer_id, SUM(amount) FROM payment GROUP BY customer_id"
    proposed = "SELECT customer_id, SUM(amount) total FROM payment GROUP BY customer_id"
    return {
        "contract_version": "query_refactor_result.v1",
        "result_id": str(uuid.uuid4()),
        "request_id": str(uuid.uuid4()),
        "completed_at": "2026-09-22T20:05:00.000Z",
        "source": "refactor",
        "destination": "dba",
        "query_id": "correlated_running_total",
        "query_fingerprint": "sha256:" + "a" * 64,
        "status": "approved_lab",
        "validation_schema": "sakila_dev",
        "original_sql": original,
        "original_sql_sha256": hashlib.sha256(original.encode()).hexdigest(),
        "proposed_sql": proposed,
        "proposed_sql_sha256": hashlib.sha256(proposed.encode()).hexdigest(),
        "change_summary": "Agregação equivalente validada no laboratório.",
        "timings": {
            "before_seconds": 86.0,
            "after_seconds": 1.0,
            "saved_seconds": 85.0,
            "improvement_percent": 98.837,
            "speedup": 86.0,
        },
        "validation": {
            "equivalent": True,
            "original_exit_code": 0,
            "proposed_exit_code": 0,
            "original_rows": 10,
            "proposed_rows": 10,
            "original_result_sha256": "b" * 64,
            "proposed_result_sha256": "b" * 64,
        },
        "artifacts": {
            "original_path": "query_refactor/advisor/results/demo/original.sql",
            "proposal_path": "query_refactor/advisor/results/demo/proposed.sql",
            "report_path": "query_refactor/advisor/results/demo/report.md",
        },
        "limitations": ["Somente sakila_dev."],
    }


def enabled_settings(**changes: Any) -> NotificationSettings:
    values: dict[str, Any] = {
        "delivery_enabled": True,
        "email_from": "notifier@example.invalid",
        "warning_recipients": (
            "warning-one@example.invalid",
            "warning-two@example.invalid",
        ),
        "critical_recipients": ("critical@example.invalid",),
        "smtp_host": "smtp.example.invalid",
        "smtp_port": 587,
        "smtp_username": "notifier@example.invalid",
        "smtp_password": "smtp-password-for-tests",
        "smtp_use_starttls": True,
    }
    values.update(changes)
    return NotificationSettings(**values)


class FakeSMTP:
    def __init__(self, host: str, port: int, *, timeout: float) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.started_tls = False
        self.tls_contexts: list[ssl.SSLContext] = []
        self.login_calls: list[tuple[str, str]] = []
        self.messages: list[EmailMessage] = []

    def __enter__(self) -> FakeSMTP:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None

    def starttls(self, *, context: ssl.SSLContext) -> None:
        self.started_tls = True
        self.tls_contexts.append(context)

    def login(self, user: str, password: str) -> None:
        self.login_calls.append((user, password))

    def send_message(self, message: EmailMessage) -> None:
        self.messages.append(message)


class FakeSMTPFactory:
    def __init__(self, *, failure_message: str | None = None) -> None:
        self.instances: list[FakeSMTP] = []
        self.failure_message = failure_message
        self.calls = 0

    def __call__(self, host: str, port: int, *, timeout: float) -> FakeSMTP:
        self.calls += 1
        if self.failure_message:
            raise OSError(self.failure_message)
        instance = FakeSMTP(host, port, timeout=timeout)
        self.instances.append(instance)
        return instance


class SpyChannel(NotificationChannel):
    name = "email"

    def __init__(self) -> None:
        self.calls = 0

    def send(
        self,
        alert: dict[str, Any],
        recipients: tuple[str, ...],
        *,
        high_priority: bool,
    ) -> DeliveryResult:
        self.calls += 1
        return DeliveryResult(True, str(alert["alert_id"]), self.name, "sent")


class NotificationTests(unittest.TestCase):
    def make_dispatcher(
        self,
        settings: NotificationSettings,
        factory: FakeSMTPFactory,
    ) -> NotificationDispatcher:
        return NotificationDispatcher(settings, EmailChannel(settings, factory))

    def test_warning_uses_only_warning_recipients(self) -> None:
        settings = enabled_settings()
        factory = FakeSMTPFactory()
        result = self.make_dispatcher(settings, factory).dispatch(load_alert())

        self.assertEqual(
            result,
            {
                "delivered": True,
                "alert_id": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
                "channel": "email",
                "status": "sent",
                "recipient_count": 2,
            },
        )
        self.assertEqual(
            str(factory.instances[0].messages[0]["To"]),
            "warning-one@example.invalid, warning-two@example.invalid",
        )
        self.assertTrue(factory.instances[0].started_tls)
        self.assertEqual(
            factory.instances[0].login_calls,
            [("notifier@example.invalid", "smtp-password-for-tests")],
        )
        self.assertEqual(factory.instances[0].timeout, 10.0)
        tls_context = factory.instances[0].tls_contexts[0]
        self.assertTrue(tls_context.check_hostname)
        self.assertEqual(tls_context.verify_mode, ssl.CERT_REQUIRED)

    def test_critical_uses_critical_recipients_and_high_priority(self) -> None:
        alert = load_alert()
        alert["severity"] = "critical"
        settings = enabled_settings()
        factory = FakeSMTPFactory()
        result = self.make_dispatcher(settings, factory).dispatch(alert)
        message = factory.instances[0].messages[0]

        self.assertTrue(result["delivered"])
        self.assertEqual(result["recipient_count"], 1)
        self.assertEqual(str(message["To"]), "critical@example.invalid")
        self.assertTrue(
            str(message["Subject"]).startswith("[CRÍTICO] Health Check MySQL")
        )
        self.assertEqual(str(message["X-Priority"]), "1")
        self.assertEqual(str(message["Importance"]), "high")
        body = message.get_body(preferencelist=("html",)).get_content()
        self.assertIn("#fef2f2", body)
        self.assertIn("#dc2626", body)

    def test_info_is_suppressed_by_policy(self) -> None:
        alert = load_alert()
        alert["severity"] = "info"
        settings = enabled_settings()
        factory = FakeSMTPFactory()
        result = self.make_dispatcher(settings, factory).dispatch(alert)

        self.assertFalse(result["delivered"])
        self.assertEqual(result["status"], "suppressed_by_policy")
        self.assertEqual(factory.calls, 0)

    def test_delivery_disabled_returns_dry_run_without_smtp(self) -> None:
        settings = enabled_settings(delivery_enabled=False)
        factory = FakeSMTPFactory()
        result = self.make_dispatcher(settings, factory).dispatch(load_alert())

        self.assertFalse(result["delivered"])
        self.assertEqual(result["status"], "dry_run")
        self.assertEqual(result["recipient_count"], 2)
        self.assertEqual(factory.calls, 0)

    def test_missing_recipients_fails_without_smtp(self) -> None:
        settings = enabled_settings(warning_recipients=())
        factory = FakeSMTPFactory()
        result = self.make_dispatcher(settings, factory).dispatch(load_alert())

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], "recipients_not_configured")
        self.assertEqual(factory.calls, 0)

    def test_missing_smtp_configuration_fails_clearly(self) -> None:
        settings = enabled_settings(smtp_host=None)
        factory = FakeSMTPFactory()
        result = self.make_dispatcher(settings, factory).dispatch(load_alert())

        self.assertEqual(result["error_code"], "smtp_not_configured")
        self.assertEqual(factory.calls, 0)

    def test_invalid_payload_never_reaches_channel(self) -> None:
        alert = load_alert()
        alert["severity"] = "urgent"
        settings = enabled_settings()
        channel = SpyChannel()
        result = NotificationDispatcher(settings, channel).dispatch(alert)

        self.assertEqual(result["error_code"], "invalid_alert")
        self.assertEqual(channel.calls, 0)

    def test_unimplemented_audit_category_never_reaches_smtp(self) -> None:
        alert = load_audit_alert()
        alert["category"] = "unimplemented_category"
        settings = enabled_settings()
        factory = FakeSMTPFactory()

        result = self.make_dispatcher(settings, factory).dispatch(alert)

        self.assertFalse(result["delivered"])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], "invalid_alert")
        self.assertEqual(factory.calls, 0)

    def test_subject_body_and_no_attachments(self) -> None:
        alert = load_alert()
        settings = enabled_settings()
        factory = FakeSMTPFactory()
        self.make_dispatcher(settings, factory).dispatch(alert)
        message = factory.instances[0].messages[0]

        self.assertEqual(
            str(message["Subject"]),
            "[ATENÇÃO] Health Check MySQL · example-prod · Uso elevado de conexões",
        )
        body = message.get_body(preferencelist=("html",)).get_content()
        for expected in (
            "ATENÇÃO",
            "Health Check MySQL",
            "example-prod",
            "Conexões",
            "Uso elevado de conexões",
            "Evidência principal",
            "15/01/2026 11:00:05 BRT",
            "76.0%",
            "Limite:",
            "70%",
            "Central de ocorrências do DBA",
            "#fff7ed",
            "#f59e0b",
            alert["alert_id"],
        ):
            self.assertIn(expected, body)
        self.assertGreaterEqual(body.count('role="presentation"'), 4)
        self.assertNotIn("display:flex", body)

        plain = message.get_body(preferencelist=("plain",)).get_content()
        self.assertEqual(list(message.iter_attachments()), [])
        self.assertNotIn("anexad", body.lower())
        self.assertNotIn("anexad", plain.lower())

    def test_query_latency_email_explains_current_actor_popularity_problem(
        self,
    ) -> None:
        alert = load_query_latency_alert()
        settings = enabled_settings()
        factory = FakeSMTPFactory()

        result = self.make_dispatcher(settings, factory).dispatch(alert)
        message = factory.instances[0].messages[0]
        body = message.get_body(preferencelist=("html",)).get_content()
        plain = message.get_body(preferencelist=("plain",)).get_content()

        self.assertTrue(result["delivered"])
        self.assertEqual(result["recipient_count"], 1)
        self.assertEqual(str(message["To"]), "critical@example.invalid")
        self.assertEqual(
            str(message["Subject"]),
            "[CRÍTICO] Health Check MySQL · demo-sakila · "
            "P99 above 2 seconds for the monitored Sakila SELECT",
        )
        for expected in (
            "Latência P99",
            "2.5 s",
            "Limite:",
            "2.0 s",
            "Query monitorada",
            "actor_popularity",
            "Janela analisada",
            "120 s",
            "Execuções na janela",
            "525",
            "Central de ocorrências do DBA",
        ):
            self.assertIn(expected, body)
        for excluded in (
            "Amostras do percentil",
            "Cobertura das amostras",
            "97eb2e3ec6c2ecefc310ed0c449384c04c59dc149f6cb31ca75e95a9a7836fe5",
            "SELECT a.actor_id",
        ):
            self.assertNotIn(excluded, body)
        self.assertIn("Latência P99", plain)
        self.assertIn("Query monitorada: actor_popularity", plain)

    def test_audit_email_is_safe_and_concise(self) -> None:
        alert = load_audit_alert()
        settings = enabled_settings()
        factory = FakeSMTPFactory()

        result = self.make_dispatcher(settings, factory).dispatch(alert)
        message = factory.instances[0].messages[0]
        body = message.get_body(preferencelist=("html",)).get_content()
        plain = message.get_body(preferencelist=("plain",)).get_content()

        self.assertTrue(result["delivered"])
        self.assertEqual(
            str(message["Subject"]),
            "[CRÍTICO] Audit MySQL · demo-sakila · "
            "Tentativa sem permissão de DDL destrutivo em sakila",
        )
        for expected in (
            "Audit MySQL",
            "Evento detectado",
            "Comando normalizado",
            "drop_table",
            "Resultado no MySQL",
            "failure",
            "Código MySQL",
            "1142",
            "SQL literal, credenciais e identidades não fazem parte deste e-mail",
        ):
            self.assertIn(expected, body)
        for excluded in (
            "actor_id",
            "source_id",
            "session_id",
            "sql_fingerprint",
            "sha256:1111111111111111",
            "sha256:2222222222222222",
            "sha256:3333333333333333",
            "sha256:4444444444444444",
        ):
            self.assertNotIn(excluded, body)
            self.assertNotIn(excluded, plain)

        self.assertEqual(list(message.iter_attachments()), [])
        self.assertNotIn("anexad", body.lower())
        self.assertNotIn("anexad", plain.lower())

    def test_refactor_completion_uses_warning_recipients_and_hides_sql(self) -> None:
        result = refactor_result()
        settings = enabled_settings()
        factory = FakeSMTPFactory()
        dispatcher = RefactorCompletionDispatcher(
            settings,
            EmailChannel(settings, factory),
        )

        delivery = dispatcher.dispatch(result)
        message = factory.instances[0].messages[0]
        html_body = message.get_body(preferencelist=("html",)).get_content()
        plain_body = message.get_body(preferencelist=("plain",)).get_content()

        self.assertTrue(delivery["delivered"])
        self.assertEqual(delivery["alert_id"], result["result_id"])
        self.assertEqual(
            str(message["To"]),
            "warning-one@example.invalid, warning-two@example.invalid",
        )
        self.assertIn("[REFACTOR CONCLUÍDO]", str(message["Subject"]))
        self.assertIn("86.0 s", html_body)
        self.assertIn("1.0 s", html_body)
        self.assertNotIn(result["original_sql"], html_body)
        self.assertNotIn(result["proposed_sql"], html_body)
        self.assertNotIn(result["original_sql"], plain_body)
        self.assertNotIn(result["proposed_sql"], plain_body)

    def test_refactor_completion_dry_run_never_opens_smtp(self) -> None:
        settings = enabled_settings(delivery_enabled=False)
        factory = FakeSMTPFactory()
        delivery = RefactorCompletionDispatcher(
            settings,
            EmailChannel(settings, factory),
        ).dispatch(refactor_result())

        self.assertEqual(delivery["status"], "dry_run")
        self.assertEqual(factory.calls, 0)

    def test_invalid_refactor_result_never_reaches_smtp(self) -> None:
        result = refactor_result()
        result["proposed_sql_sha256"] = "0" * 64
        settings = enabled_settings()
        factory = FakeSMTPFactory()
        delivery = RefactorCompletionDispatcher(
            settings,
            EmailChannel(settings, factory),
        ).dispatch(result)

        self.assertEqual(delivery["error_code"], "invalid_refactor_result")
        self.assertEqual(factory.calls, 0)

    def test_audit_alert_dry_run_never_calls_smtp(self) -> None:
        settings = enabled_settings(delivery_enabled=False)
        factory = FakeSMTPFactory()
        result = self.make_dispatcher(settings, factory).dispatch(load_audit_alert())

        self.assertEqual(result["status"], "dry_run")
        self.assertFalse(result["delivered"])
        self.assertEqual(factory.calls, 0)

    def test_blocked_alter_table_uses_schema_change_email_context(self) -> None:
        alert = deepcopy(load_audit_alert())
        alert["category"] = "schema_change"
        alert["title"] = "Tentativa sem permissão de ALTER TABLE em sakila"
        alert["summary"] = (
            "O MySQL negou por permissão uma tentativa de alteração estrutural "
            "em sakila."
        )
        alert["findings"][0]["check_id"] = "audit_security.sakila.blocked_alter_table"
        alert["findings"][0]["metric"] = "blocked_schema_change_attempt"
        alert["findings"][0]["evidence"]["sql_command"] = "alter_table"
        alert["report"]["json"]["domains"]["audit_ddl"]["rows"][0]["sql_command"] = (
            "alter_table"
        )
        alert["dedupe_key"] = alert["dedupe_key"].replace(
            "blocked-destructive-ddl", "blocked-alter-table"
        )

        settings = enabled_settings()
        factory = FakeSMTPFactory()
        result = self.make_dispatcher(settings, factory).dispatch(alert)
        message = factory.instances[0].messages[0]
        body = message.get_body(preferencelist=("html",)).get_content()

        self.assertTrue(result["delivered"])
        self.assertIn("[CRÍTICO] Audit MySQL", str(message["Subject"]))
        for expected in (
            "Alteração estrutural bloqueada",
            "Tentativa de alteração estrutural bloqueada",
            "alter_table",
            "1142",
        ):
            self.assertIn(expected, body)

    def test_query_latency_fixture_is_valid_in_manual_dry_run(self) -> None:
        result = run_manual_test(
            [str(QUERY_LATENCY_FIXTURE)],
            environ=self.manual_environment(delivery_enabled="false"),
        )

        self.assertEqual(result["status"], "dry_run")
        self.assertFalse(result["delivered"])
        self.assertEqual(result["recipient_count"], 1)

    def test_dynamic_html_is_escaped(self) -> None:
        alert = load_alert()
        unsafe = '<script>alert("owned")</script>'
        alert["title"] = unsafe
        alert["summary"] = unsafe
        alert["findings"][0]["metric"] = unsafe
        settings = enabled_settings()
        factory = FakeSMTPFactory()
        self.make_dispatcher(settings, factory).dispatch(alert)
        body = (
            factory.instances[0]
            .messages[0]
            .get_body(preferencelist=("html",))
            .get_content()
        )

        self.assertNotIn(unsafe, body)
        self.assertIn("&lt;script&gt;", body)

    def test_payload_recipients_are_ignored(self) -> None:
        alert = load_alert()
        alert["metadata"]["recipients"] = ["payload-recipient@example.invalid"]
        settings = enabled_settings()
        factory = FakeSMTPFactory()
        self.make_dispatcher(settings, factory).dispatch(alert)
        message = factory.instances[0].messages[0]

        self.assertNotIn("payload-recipient", str(message["To"]))
        self.assertEqual(
            str(message["To"]),
            "warning-one@example.invalid, warning-two@example.invalid",
        )

    def test_secrets_do_not_appear_in_logs_or_results(self) -> None:
        payload_secret = "payload-secret-must-not-leak"
        alert = load_alert()
        alert["metadata"]["password"] = payload_secret
        settings = enabled_settings(smtp_password="smtp-secret-must-not-leak")
        factory = FakeSMTPFactory()

        with self.assertLogs("notification.dispatcher", level=logging.INFO) as captured:
            result = self.make_dispatcher(settings, factory).dispatch(alert)

        rendered = json.dumps(result) + "\n".join(captured.output)
        self.assertNotIn(payload_secret, rendered)
        self.assertNotIn("smtp-secret-must-not-leak", rendered)
        self.assertEqual(result["error_code"], "invalid_alert")
        self.assertEqual(factory.calls, 0)

    def test_smtp_password_and_report_do_not_appear_in_success_logs(self) -> None:
        smtp_secret = "smtp-secret-must-not-leak"
        alert = load_alert()
        settings = enabled_settings(smtp_password=smtp_secret)
        factory = FakeSMTPFactory()

        with self.assertLogs("notification.dispatcher", level=logging.INFO) as captured:
            result = self.make_dispatcher(settings, factory).dispatch(alert)

        rendered = json.dumps(result) + "\n".join(captured.output)
        self.assertTrue(result["delivered"])
        self.assertNotIn(smtp_secret, rendered)
        self.assertNotIn(alert["summary"], rendered)
        self.assertNotIn(alert["report"]["html"], rendered)

    def test_transport_error_is_sanitized_and_not_retried(self) -> None:
        transport_secret = "transport-secret-must-not-leak"
        settings = enabled_settings()
        factory = FakeSMTPFactory(failure_message=transport_secret)

        with self.assertLogs("notification.dispatcher", level=logging.INFO) as captured:
            result = self.make_dispatcher(settings, factory).dispatch(load_alert())

        rendered = json.dumps(result) + "\n".join(captured.output)
        self.assertEqual(result["error_code"], "smtp_delivery_failed")
        self.assertNotIn(transport_secret, rendered)
        self.assertEqual(factory.calls, 1)

    def test_delivery_is_disabled_by_default(self) -> None:
        self.assertFalse(NotificationSettings.from_env({}).delivery_enabled)

    def test_settings_load_operator_environment(self) -> None:
        settings = NotificationSettings.from_env(
            {
                "NOTIFICATION_DELIVERY_ENABLED": "true",
                "NOTIFICATION_EMAIL_FROM": "notifier@example.invalid",
                "NOTIFICATION_EMAIL_RECIPIENTS_WARNING": (
                    "one@example.invalid, two@example.invalid"
                ),
                "NOTIFICATION_EMAIL_RECIPIENTS_CRITICAL": ("critical@example.invalid"),
                "SMTP_HOST": "smtp.example.invalid",
                "SMTP_PORT": "2525",
                "SMTP_USERNAME": "notifier@example.invalid",
                "SMTP_PASSWORD": "smtp-password",
                "SMTP_USE_STARTTLS": "false",
            }
        )

        self.assertTrue(settings.delivery_enabled)
        self.assertEqual(
            settings.warning_recipients,
            ("one@example.invalid", "two@example.invalid"),
        )
        self.assertEqual(settings.smtp_port, 2525)
        self.assertFalse(settings.smtp_use_starttls)

    def test_gmail_configuration_requires_587_starttls_and_matching_sender(
        self,
    ) -> None:
        common = {
            "smtp_host": "smtp.gmail.com",
            "smtp_username": "alerts@example.invalid",
            "smtp_password": "test-app-password",
            "email_from": "alerts@example.invalid",
        }
        self.assertIsNone(enabled_settings(**common).smtp_error_code())
        self.assertEqual(
            enabled_settings(**common, smtp_port=465).smtp_error_code(),
            "gmail_smtp_port_invalid",
        )
        self.assertEqual(
            enabled_settings(**common, smtp_use_starttls=False).smtp_error_code(),
            "gmail_starttls_required",
        )
        self.assertEqual(
            enabled_settings(
                **(common | {"email_from": "different@example.invalid"})
            ).smtp_error_code(),
            "smtp_sender_mismatch",
        )

    def test_manual_command_is_dry_run_without_send_even_when_enabled(self) -> None:
        environment = self.manual_environment(delivery_enabled="true")
        result = run_manual_test([str(FIXTURE)], environ=environment)

        self.assertEqual(result["status"], "dry_run")
        self.assertFalse(result["delivered"])

    def test_manual_send_requires_environment_gate(self) -> None:
        environment = self.manual_environment(delivery_enabled="false")

        with self.assertRaisesRegex(ManualTestError, "delivery_not_enabled"):
            run_manual_test([str(FIXTURE), "--send"], environ=environment)

    def test_manual_send_uses_fake_smtp_when_both_gates_are_open(self) -> None:
        environment = self.manual_environment(delivery_enabled="true")
        factory = FakeSMTPFactory()
        result = run_manual_test(
            [str(FIXTURE), "--send"],
            environ=environment,
            smtp_factory=factory,
        )

        self.assertEqual(result["status"], "sent")
        self.assertEqual(factory.calls, 1)
        self.assertEqual(
            str(factory.instances[0].messages[0]["To"]),
            "warning@example.invalid",
        )

    def test_manual_command_has_no_password_argument(self) -> None:
        stderr = StringIO()
        attempted_value = "not-a-real-password"
        with redirect_stderr(stderr), self.assertRaises(SystemExit):
            run_manual_test(
                [str(FIXTURE), "--password", attempted_value],
                environ=self.manual_environment(delivery_enabled="false"),
            )
        self.assertNotIn(attempted_value, stderr.getvalue())

    @staticmethod
    def manual_environment(*, delivery_enabled: str) -> dict[str, str]:
        return {
            "NOTIFICATION_DELIVERY_ENABLED": delivery_enabled,
            "NOTIFICATION_EMAIL_FROM": "alerts@example.invalid",
            "NOTIFICATION_EMAIL_RECIPIENTS_WARNING": "warning@example.invalid",
            "NOTIFICATION_EMAIL_RECIPIENTS_CRITICAL": "critical@example.invalid",
            "SMTP_HOST": "smtp.gmail.com",
            "SMTP_PORT": "587",
            "SMTP_USERNAME": "alerts@example.invalid",
            "SMTP_PASSWORD": "test-app-password",
            "SMTP_USE_STARTTLS": "true",
        }


if __name__ == "__main__":
    unittest.main()
