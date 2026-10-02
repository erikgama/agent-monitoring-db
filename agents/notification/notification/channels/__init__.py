"""Extensible notification channel interfaces and implementations."""

from notification.channels.base import NotificationChannel
from notification.channels.email import EmailChannel

__all__ = ["EmailChannel", "NotificationChannel"]
