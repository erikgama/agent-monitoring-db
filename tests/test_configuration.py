import json
import subprocess
from pathlib import Path

import pytest

from agent_monitoring.config import (
    EXAMPLE_CONFIG,
    ConfigurationError,
    database_settings,
    load_config,
)
from agent_monitoring.llm import LlmError, LlmSettings, build_command, run_analysis


@pytest.fixture
def central(tmp_path, monkeypatch):
    path = tmp_path / "agent-monitoring.toml"
    path.write_text(EXAMPLE_CONFIG.read_text())
    monkeypatch.setenv("AGENT_MONITORING_CONFIG", str(path))
    monkeypatch.delenv("AGENT_MONITORING_LLM_PROVIDER", raising=False)
    return path


def test_monitoring_agents_resolve_the_same_profile_without_reading_it(
    central, monkeypatch
):
    central.write_text(
        central.read_text().replace(
            "~/.config/agent-monitoring/mylogin.cnf",
            "profiles/not-a-real-login-file.cnf",
        )
    )
    original_read = Path.read_text

    def guarded(path, *args, **kwargs):
        if path.suffix == ".cnf":
            raise AssertionError("Credential content must never be opened")
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded)
    profiles = [database_settings(role) for role in ("health-check", "audit", "dba")]
    assert len({item.login_file for item in profiles}) == 1
    assert len({item.login_path for item in profiles}) == 1
    assert (
        profiles[0].login_file == central.parent / "profiles/not-a-real-login-file.cnf"
    )
    assert all(item.database == "sakila" for item in profiles)
    assert database_settings("refactor").database == "sakila_dev"
    assert database_settings("audit-lab").login_path != profiles[0].login_path


@pytest.mark.parametrize(
    "setting", ['ssl_mode = "DISABLED"', 'password = "synthetic-forbidden-value"']
)
def test_insecure_configuration_is_rejected(central, setting):
    value = central.read_text()
    if setting.startswith("ssl_mode"):
        value = value.replace('ssl_mode = "REQUIRED"', setting)
    else:
        value = value.replace("[database]", "[database]\n" + setting)
    central.write_text(value)
    with pytest.raises(ConfigurationError):
        database_settings("health-check")


def test_missing_explicit_configuration_never_falls_back(tmp_path):
    with pytest.raises(ConfigurationError, match="central_config_not_found"):
        load_config({"AGENT_MONITORING_CONFIG": str(tmp_path / "missing.toml")})


def test_password_environment_override_is_removed(central):
    env = database_settings("dba").environment(
        {"MYSQL_PWD": "synthetic-forbidden-value", "PATH": "/usr/bin"}
    )
    assert "MYSQL_PWD" not in env
    assert env["MYSQL_TEST_LOGIN_FILE"] == str(database_settings("dba").login_file)


def test_llm_commands_disable_tools_or_require_a_read_only_sandbox(central, tmp_path):
    for provider in ("codex", "claude", "kimi"):
        command = build_command(
            LlmSettings(provider, "", "low"), tmp_path / "result.json"
        )
        if provider == "codex":
            assert command[command.index("--sandbox") + 1] == "read-only"
            assert "shell_tool" in command
            assert "--ephemeral" in command
        elif provider == "claude":
            assert command[command.index("--tools") + 1] == ""
            assert "--strict-mcp-config" in command
        else:
            profile = Path(command[command.index("--agent-file") + 1]).read_text()
            assert "tools: []" in profile
            assert "subagents: []" in profile


@pytest.mark.parametrize("provider", ["codex", "claude", "kimi"])
def test_realistic_provider_output_is_parsed_and_schema_checked(
    central, tmp_path, monkeypatch, provider
):
    monkeypatch.setenv("AGENT_MONITORING_LLM_PROVIDER", provider)
    schema = tmp_path / "schema.json"
    schema.write_text(
        json.dumps(
            {
                "type": "object",
                "properties": {"status": {"const": "ok"}},
                "required": ["status"],
                "additionalProperties": False,
            }
        )
    )

    def complete(command, **kwargs):
        assert kwargs["cwd"] != str(central.parent)
        assert "MYSQL_PWD" not in kwargs["env"]
        if provider == "codex":
            Path(command[command.index("--output-last-message") + 1]).write_text(
                '{"status":"ok"}'
            )
            stdout = ""
        elif provider == "claude":
            stdout = '{"structured_output":{"status":"ok"},"is_error":false}'
        else:
            stdout = json.dumps(
                {
                    "role": "assistant",
                    "content": [{"type": "text", "text": '{"status":"ok"}'}],
                }
            )
        return subprocess.CompletedProcess(command, 0, stdout, "")

    assert json.loads(
        run_analysis("Return the status", schema=schema, runner=complete)
    ) == {"status": "ok"}


@pytest.mark.parametrize("provider", ["codex", "claude", "kimi"])
def test_llm_failures_are_sanitized(central, monkeypatch, provider):
    monkeypatch.setenv("AGENT_MONITORING_LLM_PROVIDER", provider)

    def fail(command, **kwargs):
        return subprocess.CompletedProcess(
            command, 1, "private-output", "private-error"
        )

    with pytest.raises(LlmError, match="^llm_execution_failed$"):
        run_analysis("data", runner=fail)


def test_schema_mismatch_is_rejected_for_unstructured_client(
    central, tmp_path, monkeypatch
):
    monkeypatch.setenv("AGENT_MONITORING_LLM_PROVIDER", "kimi")
    schema = tmp_path / "schema.json"
    schema.write_text('{"type":"object","required":["status"]}')

    def complete(command, **kwargs):
        return subprocess.CompletedProcess(
            command, 0, '{"role":"assistant","content":"{}"}', ""
        )

    with pytest.raises(LlmError, match="llm_output_schema_invalid"):
        run_analysis("data", schema=schema, runner=complete)
