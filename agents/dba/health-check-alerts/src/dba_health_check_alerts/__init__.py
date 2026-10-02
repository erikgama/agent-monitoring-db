"""Private DBA consumer for MCP-validated Health Check alerts."""

from dba_health_check_alerts.inbox import (
    DbaAlertInbox,
    DbaAlertInboxError,
    DbaAlertReceipt,
    default_inbox_directory,
)

__all__ = [
    "DbaAlertInbox",
    "DbaAlertInboxError",
    "DbaAlertReceipt",
    "default_inbox_directory",
]
