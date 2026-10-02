"""Load non-secret settings; never open a MySQL credential or login file."""

from __future__ import annotations

import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "agent-monitoring.toml"
EXAMPLE_CONFIG = ROOT / "config" / "agent-monitoring.example.toml"
ROLES = {
    "health-check": "monitoring",
    "audit": "monitoring",
    "dba": "monitoring",
    "refactor": "refactor",
    "workload": "workload",
    "audit-lab": "audit_lab",
}


class ConfigurationError(ValueError):
    """A stable error code without configuration values."""


def config_path(environ: Mapping[str, str] | None = None) -> Path:
    source = os.environ if environ is None else environ
    return (
        Path(source.get("AGENT_MONITORING_CONFIG", str(DEFAULT_CONFIG)))
        .expanduser()
        .resolve()
    )


def load_config(environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    path = config_path(environ)
    # An unconfigured clone can run the isolated demo and offline tests.
    selected = path if path.is_file() else EXAMPLE_CONFIG
    if path != DEFAULT_CONFIG.resolve() and not path.is_file():
        raise ConfigurationError("central_config_not_found")
    try:
        value = tomllib.loads(selected.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
        raise ConfigurationError("central_config_invalid") from error
    allowed = {
        "database": {
            "login_file",
            "mysql_binary",
            "ssl_mode",
            "ssl_ca",
            "expected_port",
            "target_label",
            "monitoring_login_path",
            "refactor_login_path",
            "workload_login_path",
            "audit_lab_login_path",
        },
        "llm": {"provider", "codex", "claude", "kimi"},
    }
    if set(value) - allowed.keys():
        raise ConfigurationError("central_config_unknown_section")
    for section, keys in allowed.items():
        if not isinstance(value.get(section), dict) or set(value[section]) - keys:
            raise ConfigurationError("central_config_unknown_key")
    for provider in ("codex", "claude", "kimi"):
        settings = value["llm"].get(provider, {})
        if not isinstance(settings, dict) or set(settings) - {
            "analysis_model",
            "refactor_model",
            "analysis_effort",
            "refactor_effort",
        }:
            raise ConfigurationError("llm_config_unknown_key")
        if any(not isinstance(item, str) for item in settings.values()):
            raise ConfigurationError("llm_config_invalid")
    return value


@dataclass(frozen=True, slots=True)
class DatabaseSettings:
    login_file: Path
    login_path: str
    database: str
    mysql_binary: str
    ssl_mode: str
    ssl_ca: Path | None
    expected_port: int
    target_label: str

    def tls_flags(self) -> list[str]:
        return [
            f"--ssl-mode={self.ssl_mode}",
            *([f"--ssl-ca={self.ssl_ca}"] if self.ssl_ca else []),
        ]

    def environment(self, environ: Mapping[str, str] | None = None) -> dict[str, str]:
        selected = dict(os.environ if environ is None else environ)
        # Passwords must be supplied by the client login file, never MYSQL_PWD.
        selected.pop("MYSQL_PWD", None)
        selected["MYSQL_TEST_LOGIN_FILE"] = str(self.login_file)
        return selected


def database_settings(
    role: str, environ: Mapping[str, str] | None = None
) -> DatabaseSettings:
    if role not in ROLES:
        raise ConfigurationError("database_role_invalid")
    raw = load_config(environ)["database"]
    try:
        reference = raw["login_file"]
        profile = raw[f"{ROLES[role]}_login_path"]
        mode = raw.get("ssl_mode", "REQUIRED")
        port = raw.get("expected_port", 3306)
        binary = raw.get("mysql_binary", "mysql")
        label = raw.get("target_label", "configured-target")
        ca = raw.get("ssl_ca", "")
        if not all(
            isinstance(item, str)
            for item in (reference, profile, mode, binary, label, ca)
        ):
            raise ConfigurationError("database_config_invalid")
        if not reference or not binary or not re.fullmatch(r"[a-zA-Z0-9_-]+", profile):
            raise ConfigurationError("database_config_invalid")
        if mode not in {"REQUIRED", "VERIFY_CA", "VERIFY_IDENTITY"}:
            raise ConfigurationError("database_tls_required")
        if type(port) is not int or not 1 <= port <= 65535:
            raise ConfigurationError("database_port_invalid")
    except KeyError as error:
        raise ConfigurationError("database_config_incomplete") from error
    base = config_path(environ).parent

    def resolve_reference(value: str) -> Path:
        path = Path(value).expanduser()
        return (path if path.is_absolute() else base / path).resolve()

    return DatabaseSettings(
        login_file=resolve_reference(reference),
        login_path=profile,
        database="sakila_dev" if role == "refactor" else "sakila",
        mysql_binary=binary,
        ssl_mode=mode,
        ssl_ca=resolve_reference(ca) if ca else None,
        expected_port=port,
        target_label=label,
    )
