"""Carrega a configuracao local nao secreta do Notification sem shell."""

from __future__ import annotations

import shlex
from pathlib import Path


class NotificationConfigError(ValueError):
    """Configuracao local ausente, invalida ou insegura."""


RUNTIME_KEYS = {
    "NOTIFICATION_SMTP_CREDENTIAL_HELPER",
    "NOTIFICATION_EMAIL_FROM",
    "NOTIFICATION_EMAIL_RECIPIENTS_CRITICAL",
    "NOTIFICATION_EMAIL_RECIPIENTS_WARNING",
    "NOTIFICATION_EMAIL_RECIPIENTS_REFACTOR",
    "SMTP_HOST",
    "SMTP_PORT",
    "SMTP_USERNAME",
    "SMTP_USE_STARTTLS",
}
FILE_KEYS = RUNTIME_KEYS | {"NOTIFICATION_DELIVERY_ENABLED"}
REQUIRED_KEYS = {
    "NOTIFICATION_EMAIL_FROM",
    "NOTIFICATION_EMAIL_RECIPIENTS_CRITICAL",
    "NOTIFICATION_EMAIL_RECIPIENTS_WARNING",
    "SMTP_HOST",
    "SMTP_USERNAME",
}


def _parse_value(raw_value: str, *, line_number: int) -> str:
    lexer = shlex.shlex(raw_value, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = "#"
    try:
        values = list(lexer)
    except ValueError as error:
        raise NotificationConfigError(
            f"valor invalido na linha {line_number}"
        ) from error
    if not values:
        return ""
    if len(values) != 1:
        raise NotificationConfigError(
            f"valor deve estar entre aspas na linha {line_number}"
        )
    return values[0]


def load_notification_environment(path: Path) -> dict[str, str]:
    """Le um arquivo KEY=VALUE estrito e devolve somente chaves permitidas."""

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise NotificationConfigError(
            "nao foi possivel ler a configuracao local do Notification"
        ) from error

    parsed: dict[str, str] = {}
    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").lstrip()
        key, separator, raw_value = line.partition("=")
        key = key.strip()
        if not separator or key not in FILE_KEYS:
            raise NotificationConfigError(f"chave nao permitida na linha {line_number}")
        if key in parsed:
            raise NotificationConfigError(f"chave duplicada: {key}")
        parsed[key] = _parse_value(raw_value.strip(), line_number=line_number)

    if parsed.get("NOTIFICATION_DELIVERY_ENABLED", "false").lower() != "false":
        raise NotificationConfigError(
            "NOTIFICATION_DELIVERY_ENABLED deve permanecer false no arquivo local"
        )

    selected = {
        key: value for key, value in parsed.items() if key in RUNTIME_KEYS and value
    }
    selected.setdefault("SMTP_PORT", "587")
    selected.setdefault("SMTP_USE_STARTTLS", "true")
    if missing := sorted(REQUIRED_KEYS - selected.keys()):
        raise NotificationConfigError(
            "configuracao do Notification incompleta: " + ", ".join(missing)
        )
    return selected
