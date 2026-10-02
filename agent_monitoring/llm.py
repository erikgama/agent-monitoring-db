"""Bounded CLI analysis with shared provider settings and local schema checks."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from .config import ConfigurationError, load_config


class LlmError(RuntimeError):
    """Sanitized provider error; CLI stderr and prompts are never logged."""


@dataclass(frozen=True, slots=True)
class LlmSettings:
    provider: str
    model: str
    effort: str


def llm_settings(
    role: str = "analysis", environ: Mapping[str, str] | None = None
) -> LlmSettings:
    if role not in {"analysis", "refactor"}:
        raise ConfigurationError("llm_role_invalid")
    source = os.environ if environ is None else environ
    raw = load_config(environ)["llm"]
    provider = source.get("AGENT_MONITORING_LLM_PROVIDER", raw.get("provider", "codex"))
    if provider not in {"codex", "claude", "kimi"}:
        raise ConfigurationError("llm_provider_invalid")
    selected = raw.get(provider, {})
    effort = selected.get(f"{role}_effort", "low" if role == "analysis" else "medium")
    if effort not in {"low", "medium", "high", "xhigh", "max"}:
        raise ConfigurationError("llm_effort_invalid")
    return LlmSettings(provider, selected.get(f"{role}_model", ""), effort)


def build_command(
    settings: LlmSettings, output: Path, schema: Path | None = None
) -> list[str]:
    model = ["--model", settings.model] if settings.model else []
    if settings.provider == "codex":
        return [
            "codex",
            "exec",
            *model,
            "--config",
            f'model_reasoning_effort="{settings.effort}"',
            "--config",
            "shell_environment_policy.inherit=none",
            "--config",
            "mcp_servers={}",
            "--config",
            'web_search="disabled"',
            "--disable",
            "shell_tool",
            "--sandbox",
            "read-only",
            "--ephemeral",
            "--skip-git-repo-check",
            "--cd",
            str(output.parent),
            *(["--output-schema", str(schema)] if schema else []),
            "--output-last-message",
            str(output),
            "-",
        ]
    if settings.provider == "claude":
        return [
            "claude",
            "--print",
            *model,
            "--effort",
            settings.effort,
            "--tools",
            "",
            "--strict-mcp-config",
            "--mcp-config",
            '{"mcpServers":{}}',
            "--setting-sources",
            "",
            "--no-session-persistence",
            "--output-format",
            "json",
            *(["--json-schema", schema.read_text(encoding="utf-8")] if schema else []),
        ]
    return [
        "kimi",
        *model,
        "--agent-file",
        str(Path(__file__).with_name("kimi-analyzer.md")),
        "--output-format",
        "stream-json",
        "--prompt",
        "Analyze the input supplied on stdin.",
    ]


def _kimi_text(stdout: str) -> str:
    replies = []
    try:
        for line in stdout.splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            message = item.get("message", item)
            if message.get("role", item.get("type")) != "assistant":
                continue
            if message.get("tool_calls"):
                raise LlmError("llm_tool_call_rejected")
            content = message.get("content", "")
            if isinstance(content, list):
                content = "".join(
                    part.get("text", "")
                    for part in content
                    if part.get("type") == "text"
                )
            if isinstance(content, str) and content.strip():
                replies.append(content)
    except (ValueError, AttributeError, TypeError) as error:
        raise LlmError("llm_output_invalid") from error
    if not replies:
        raise LlmError("llm_output_missing")
    return replies[-1]


def run_analysis(
    prompt: str,
    *,
    role: str = "analysis",
    schema: Path | None = None,
    timeout_seconds: int = 120,
    runner: Callable[..., Any] | None = None,
) -> str:
    settings = llm_settings(role)
    if schema is not None:
        prompt += "\nReturn only JSON matching this schema:\n" + schema.read_text(
            encoding="utf-8"
        )
    with tempfile.TemporaryDirectory(prefix="agent-monitoring-analysis-") as name:
        output = Path(name) / "output.json"
        command = build_command(settings, output, schema)
        if settings.provider == "kimi":
            command[-1] = prompt
        environment = dict(os.environ)
        for key in tuple(environment):
            if key.startswith(("MYSQL_", "SMTP_", "LAB_RUNNER_")):
                environment.pop(key, None)
        try:
            completed = (runner or subprocess.run)(
                command,
                input=prompt,
                text=True,
                capture_output=True,
                cwd=name,
                timeout=timeout_seconds,
                check=False,
                env=environment,
            )
        except FileNotFoundError as error:
            raise LlmError("llm_cli_not_found") from error
        except subprocess.TimeoutExpired as error:
            raise LlmError("llm_analysis_timeout") from error
        except OSError as error:
            raise LlmError("llm_execution_failed") from error
        if completed.returncode != 0:
            raise LlmError("llm_execution_failed")
        try:
            if settings.provider == "codex":
                result = output.read_text(encoding="utf-8").strip()
            elif settings.provider == "claude":
                envelope = json.loads(completed.stdout)
                if envelope.get("is_error"):
                    raise LlmError("llm_execution_failed")
                result = (
                    json.dumps(envelope["structured_output"], ensure_ascii=False)
                    if schema
                    else envelope["result"]
                )
            else:
                result = _kimi_text(completed.stdout).strip()
            if (
                not isinstance(result, str)
                or not result
                or len(result.encode()) > 4_000_000
            ):
                raise LlmError("llm_output_invalid")
            if schema:
                value = json.loads(result)
                validator = Draft202012Validator(
                    json.loads(schema.read_text(encoding="utf-8")),
                    format_checker=FormatChecker(),
                )
                if (
                    not isinstance(value, dict)
                    or next(validator.iter_errors(value), None) is not None
                ):
                    raise LlmError("llm_output_schema_invalid")
            return result
        except (OSError, UnicodeError, ValueError, KeyError, TypeError) as error:
            raise LlmError("llm_output_invalid") from error
