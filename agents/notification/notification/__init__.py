"""Safe channel routing for validated MySQL Conf events."""

from notification.advisor import NotificationAdvisor
from notification.channels.email import EmailChannel
from notification.config import NotificationSettings
from notification.dispatcher import NotificationDispatcher
from notification.refactor_dispatcher import RefactorCompletionDispatcher
from notification.runtime import (
    CredentialResolutionError,
    build_dispatcher,
    build_refactor_dispatcher,
    resolve_runtime_settings,
)

__all__ = [
    "CredentialResolutionError",
    "EmailChannel",
    "NotificationAdvisor",
    "NotificationDispatcher",
    "RefactorCompletionDispatcher",
    "NotificationSettings",
    "build_dispatcher",
    "build_refactor_dispatcher",
    "resolve_runtime_settings",
]
