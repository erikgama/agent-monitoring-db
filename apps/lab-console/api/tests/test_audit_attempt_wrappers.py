import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def load_script(name: str) -> ModuleType:
    path = SCRIPTS / name
    spec = importlib.util.spec_from_file_location(name.removesuffix(".py"), path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "script_name", ["run-audit-drop-lab.py", "run-audit-alter-lab.py"]
)
def test_audit_attempt_allows_slow_server_and_accepts_only_permission_denial(
    script_name, monkeypatch, capsys
):
    module = load_script(script_name)
    captured = {}

    def denied(command, **options):
        captured["command"] = command
        captured["timeout"] = options["timeout"]
        return subprocess.CompletedProcess(command, 1, "", "ERROR 1142 denied")

    monkeypatch.setattr(module.subprocess, "run", denied)
    monkeypatch.setattr(sys, "argv", [script_name, "--execute"])

    assert module.main() == 0
    assert capsys.readouterr().out.strip() == "expected_permission_denied"
    assert "--connect-timeout=15" in captured["command"]
    assert captured["timeout"] == 300


@pytest.mark.parametrize(
    "script_name", ["run-audit-drop-lab.py", "run-audit-alter-lab.py"]
)
def test_audit_attempt_reports_bounded_timeout(script_name, monkeypatch, capsys):
    module = load_script(script_name)

    def timeout(*_args, **_options):
        raise subprocess.TimeoutExpired("mysql", 300)

    monkeypatch.setattr(module.subprocess, "run", timeout)
    monkeypatch.setattr(sys, "argv", [script_name, "--execute"])

    assert module.main() == 1
    assert capsys.readouterr().out.strip() == "attempt_timeout"
