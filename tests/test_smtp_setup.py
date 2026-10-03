import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import smtp_setup  # noqa: E402


def test_init_creates_private_template_and_preserves_existing(tmp_path, monkeypatch):
    example = tmp_path / ".env.example"
    config = tmp_path / ".notification.local.env"
    example.write_text("NOTIFICATION_DELIVERY_ENABLED=false\n")
    monkeypatch.setattr(smtp_setup, "EXAMPLE", example)
    monkeypatch.setattr(smtp_setup, "CONFIG", config)

    assert smtp_setup.initialize()["status"] == "created"
    assert config.read_text() == example.read_text()
    assert config.stat().st_mode & 0o777 == 0o600
    config.write_text("operator settings\n")
    assert smtp_setup.initialize()["status"] == "existing_preserved"
    assert config.read_text() == "operator settings\n"


def configured(tmp_path: Path, monkeypatch) -> Path:
    helper = tmp_path / "smtp-helper"
    helper.write_text("#!/bin/sh\nexit 0\n")
    helper.chmod(0o700)
    config = tmp_path / ".notification.local.env"
    config.write_text(
        "NOTIFICATION_DELIVERY_ENABLED=false\n"
        "NOTIFICATION_EMAIL_FROM=alerts@example.org\n"
        "NOTIFICATION_EMAIL_RECIPIENTS_WARNING=one@example.org,two@example.org\n"
        "NOTIFICATION_EMAIL_RECIPIENTS_CRITICAL=critical@example.org\n"
        "SMTP_HOST=smtp.gmail.com\n"
        "SMTP_PORT=587\n"
        "SMTP_USERNAME=alerts@example.org\n"
        "SMTP_USE_STARTTLS=true\n"
        f"NOTIFICATION_SMTP_CREDENTIAL_HELPER={helper}\n"
    )
    config.chmod(0o600)
    monkeypatch.setattr(smtp_setup, "CONFIG", config)
    return helper


def test_check_validates_configuration_without_reading_secret(tmp_path, monkeypatch):
    configured(tmp_path, monkeypatch)
    result = smtp_setup.check()
    assert result == {
        "status": "ready_for_send_test",
        "credential_source": "helper",
        "warning_recipient_count": 2,
        "critical_recipient_count": 1,
        "smtp_connected": False,
        "secret_read": False,
    }


def test_check_rejects_placeholder_or_unprotected_config(tmp_path, monkeypatch):
    configured(tmp_path, monkeypatch)
    smtp_setup.CONFIG.chmod(0o644)
    with pytest.raises(smtp_setup.SmtpSetupError, match="permissions_insecure"):
        smtp_setup.check()
    smtp_setup.CONFIG.chmod(0o600)
    smtp_setup.CONFIG.write_text(
        smtp_setup.CONFIG.read_text().replace("one@example.org", "one@example.invalid")
    )
    with pytest.raises(smtp_setup.SmtpSetupError, match="invalid_or_placeholder"):
        smtp_setup.check()


def test_real_send_uses_notification_runtime_and_never_passes_password(
    tmp_path, monkeypatch
):
    helper = configured(tmp_path, monkeypatch)
    notification = tmp_path / "notification"
    interpreter = notification / ".venv/bin/python"
    interpreter.parent.mkdir(parents=True)
    interpreter.touch()
    dispatch = tmp_path / "notification_dispatch.py"
    dispatch.touch()
    monkeypatch.setattr(smtp_setup, "NOTIFICATION", notification)
    monkeypatch.setattr(smtp_setup, "DISPATCH", dispatch)
    monkeypatch.setenv("SMTP_PASSWORD", "synthetic-must-not-pass")

    def fake_run(command, **options):
        assert command == [str(interpreter), str(dispatch), "--send"]
        assert options["cwd"] == notification
        assert "SMTP_PASSWORD" not in options["env"]
        assert options["env"]["NOTIFICATION_DELIVERY_ENABLED"] == "true"
        assert options["env"]["NOTIFICATION_SMTP_CREDENTIAL_HELPER"] == str(helper)
        return subprocess.CompletedProcess(
            command,
            0,
            json.dumps({"status": "sent", "delivered": True, "recipient_count": 2}),
            "",
        )

    monkeypatch.setattr(smtp_setup.subprocess, "run", fake_run)
    assert smtp_setup.send_test() == {
        "status": "sent",
        "delivered": True,
        "recipient_count": 2,
        "inbox_receipt_verified": False,
    }
    assert os.stat(smtp_setup.CONFIG).st_mode & 0o777 == 0o600
