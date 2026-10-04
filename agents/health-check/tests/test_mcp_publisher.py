from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = ROOT.parents[1]
sys.path.insert(0, str(ROOT))

from src.alerting import McpIncidentPublisher  # noqa: E402


class McpPublisherEnvironmentTests(unittest.TestCase):
    def test_forwards_required_notification_environment_to_stdio_mcp(self) -> None:
        configured = {
            "MCP_DBA_ENABLED": "true",
            "MCP_DBA_ALERTS_DIR": "/tmp/dba-alert-test",
            "MCP_NOTIFICATION_ENABLED": "true",
            "NOTIFICATION_DELIVERY_ENABLED": "true",
            "NOTIFICATION_EMAIL_FROM": "sender@example.invalid",
            "NOTIFICATION_EMAIL_RECIPIENTS_CRITICAL": ("critical@example.invalid"),
            "SMTP_HOST": "smtp.example.invalid",
            "SMTP_PORT": "587",
            "SMTP_USERNAME": "sender@example.invalid",
            "SMTP_PASSWORD": "test-secret",
            "SMTP_USE_STARTTLS": "true",
            "NOTIFICATION_SMTP_CREDENTIAL_HELPER": "/tmp/example-secret-helper",
            "UV_CACHE_DIR": "/tmp/mysqlconf-mcp-uv-cache",
            "DATABASE_PASSWORD": "must-not-cross-boundary",
        }

        with patch.dict(os.environ, configured, clear=False):
            parameters = McpIncidentPublisher(
                repository_root=REPOSITORY_ROOT
            )._parameters()

        self.assertEqual(parameters.env["MCP_NOTIFICATION_ENABLED"], "true")
        self.assertEqual(parameters.env["MCP_DBA_ENABLED"], "true")
        self.assertEqual(parameters.env["MCP_DBA_ALERTS_DIR"], "/tmp/dba-alert-test")
        self.assertEqual(parameters.env["NOTIFICATION_DELIVERY_ENABLED"], "true")
        self.assertNotIn("SMTP_PASSWORD", parameters.env)
        self.assertEqual(
            parameters.env["NOTIFICATION_SMTP_CREDENTIAL_HELPER"],
            "/tmp/example-secret-helper",
        )
        self.assertEqual(parameters.env["UV_CACHE_DIR"], "/tmp/mysqlconf-mcp-uv-cache")
        self.assertNotIn("DATABASE_PASSWORD", parameters.env)

    def test_explicit_environment_overrides_parent_for_tests(self) -> None:
        with patch.dict(
            os.environ,
            {"NOTIFICATION_DELIVERY_ENABLED": "true"},
            clear=False,
        ):
            parameters = McpIncidentPublisher(
                repository_root=REPOSITORY_ROOT,
                server_environment={"NOTIFICATION_DELIVERY_ENABLED": "false"},
            )._parameters()

        self.assertEqual(parameters.env["NOTIFICATION_DELIVERY_ENABLED"], "false")

    def test_uses_an_isolated_uv_cache_by_default(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            parameters = McpIncidentPublisher(
                repository_root=REPOSITORY_ROOT
            )._parameters()

        self.assertIn("UV_CACHE_DIR", parameters.env)
        self.assertTrue(parameters.env["UV_CACHE_DIR"].endswith("mysqlconf-uv-cache"))


if __name__ == "__main__":
    unittest.main()
