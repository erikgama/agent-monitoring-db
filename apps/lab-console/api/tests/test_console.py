import asyncio
import contextlib
import json
import os
import signal
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import psutil
import pytest
from fastapi.testclient import TestClient

from labconsole.app import create_app
from labconsole.artifacts import Catalog, safe_read
from labconsole.models import ACTIONS
from labconsole.public_demo import create_public_demo
from labconsole.refactor_deliveries import RefactorDeliveries
from labconsole.runner import (
    ProcessTree,
    Runner,
    child_environment,
    command_for,
    parse_line,
)
from labconsole.security import Signer, password_hash, sanitize

ORIGIN = {"origin": "http://localhost:3000"}
KEY = "test-key-not-a-real-secret-1234567890123456"
HASH = password_hash("test-password")
USERS = {r: {"hash": HASH, "role": r} for r in ("viewer", "operator", "dba_approver")}
LAB_CONSOLE_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_RUNTIME", str(tmp_path / "runtime"))
    monkeypatch.setenv("LAB_DEMO_DELAY", "0.001")
    repository = tmp_path / "repository"
    fixtures = {
        "agents/health-check/skills/health-check-skill.md": "# Skill\n",
        "agents/health-check/advisor/rules.md": "# Rules\n",
        "agents/health-check/contracts/health_check_alert.v1.schema.json": "{}\n",
        "agents/health-check/select_latency/collector.py": "print('latency')\n",
        "agents/health-check/src/db.py": "print('db')\n",
        "agents/health-check/src/models.py": "print('models')\n",
        "agents/health-check/src/mysql_cli.py": "print('cli')\n",
        "agents/health-check/src/normalizer.py": "print('normalizer')\n",
        "agents/health-check/advisor/agent.py": "print('advisor')\n",
        "agents/health-check/src/alert_contract.py": "print('contract')\n",
        "agents/health-check/src/alerting/mcp_publisher.py": "print('mcp')\n",
        "agents/health-check/refactor_collector/mcp_publisher.py": (
            "print('refactor request')\n"
        ),
        "agents/health-check/refactor_collector/README.md": "# Refactor Collector\n",
        "agents/health-check/refactor_collector/advisor.py": "# advisor\n",
        "agents/health-check/refactor_collector/analysis.schema.json": "{}\n",
        "agents/health-check/refactor_collector/bad_queries_with_llm_refactor.sql": (
            "SELECT 1;\n"
        ),
        "agents/health-check/refactor_collector/collect_query_tuning_snapshot.py": (
            "# collector\n"
        ),
        "agents/health-check/refactor_collector/results/analysis.json": "{}\n",
        "agents/health-check/refactor_collector/results/latest.html": (
            "<h1>Slow queries</h1>\n"
        ),
        "agents/health-check/refactor_collector/results/latest.json": "{}\n",
        "agents/health-check/refactor_collector/results/runtime/private.json": "{}\n",
        "agents/health-check/refactor_collector/rules.md": "# Refactor rules\n",
        "agents/health-check/refactor_collector/rules/collector-policy.json": "{}\n",
        "agents/health-check/refactor_collector/sql/120_slow_query_refactor.sql": (
            "SELECT 1;\n"
        ),
        "agents/audit/audit_security/alerting.py": "print('audit mcp')\n",
        "agents/health-check/general_report/main.py": "print('health')\n",
        "agents/health-check/general_report/sql/00_capabilities.sql": "SELECT 1;\n",
        "agents/health-check/select_latency/results/latest.html": "<h1>Latest</h1>\n",
        "agents/health-check/select_latency/results/latest.json": "{}\n",
        "agents/health-check/select_latency/sql/110_select_latency.sql": "SELECT 1;\n",
        "agents/notification/skills/notification-skill.md": "# Skill\n",
        "agents/notification/notification/advisor/rules.md": "# Role\n",
        "agents/notification/notification/advisor/agent.py": "# advisor\n",
        "agents/notification/notification/channels/email.py": "# email\n",
        "agents/notification/contracts/health_check_alert.v1.schema.json": "{}\n",
        "agents/refactor/skills/refactor-skill.md": "# Skill Refactor\n",
        "agents/refactor/query_refactor/advisor/rules.md": "# Rules Refactor\n",
        "agents/refactor/query_refactor/advisor/agent.py": "# advisor\n",
        "agents/refactor/query_refactor/mysql_client.py": "# mysql\n",
        "agents/refactor/query_refactor/mcp_publisher.py": "# mcp\n",
        "agents/refactor/query_refactor/advisor/results/job/request.json": "{}\n",
        "agents/refactor/query_refactor/advisor/results/job/original.sql": (
            "SELECT 1;\n"
        ),
        "agents/refactor/query_refactor/advisor/results/job/proposed.sql": (
            "SELECT 1;\n"
        ),
        "agents/refactor/query_refactor/advisor/results/job/result.json": "{}\n",
        "agents/refactor/contracts/query_refactor_result.v1.schema.json": "{}\n",
        "agents/refactor/contracts/refactor_advisor_proposal.v1.schema.json": "{}\n",
        "apps/lab-console/scripts/run-health-select-simulation.py": "# selects\n",
        "apps/lab-console/scripts/run-slow-query-log-demo.py": "# slow log\n",
        "apps/lab-console/scripts/run-audit-drop-lab.py": "# drop\n",
        "apps/lab-console/scripts/run-audit-alter-lab.py": "# alter\n",
        "agents/dba/load-tests/sakila-read-only/sakila_read_demo_35.py": (
            "# latency workload\n"
        ),
        "agents/dba/analise-ocorrencia-health-check/prompt.md": (
            "# Resumo do Health Check\n"
        ),
        "agents/dba/analise-ocorrencia-audit/prompt.md": "# Resumo do Audit\n",
        "agents/dba/AGENTS.md": "# Regras do DBA\n",
        "apps/lab-console/api/labconsole/incident_analysis.py": (
            "SUMMARY_MODEL = 'gpt-5.6-luna'\n"
        ),
        "mcp/src/mysqlconf_mcp/server.py": "mcp.run(transport='stdio')\n",
        "mcp/src/mysqlconf_mcp/tools/README.md": "# Tools do MCP Central\n",
        "mcp/src/mysqlconf_mcp/tools/incidents.py": "def incident_raise(): ...\n",
        "mcp/src/mysqlconf_mcp/tools/refactor_workflow.py": (
            "def refactor_request_raise(): ...\n"
        ),
        "mcp/src/mysqlconf_mcp/contracts/health.json": "{}\n",
    }
    for relative, content in fixtures.items():
        path = repository / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    monkeypatch.setenv("LAB_REPOSITORY_ROOT", str(repository))
    with TestClient(
        create_app(database=f"sqlite:///{tmp_path}/test.db", users=USERS)
    ) as value:
        yield value


def login(client, role="operator"):
    result = client.post(
        "/api/login",
        headers=ORIGIN,
        json={"username": role, "password": "test-password"},
    )
    assert result.status_code == 200
    return {**ORIGIN, "x-csrf-token": result.json()["csrf"]}


def start(client, headers, action="lab.three", **extras):
    return client.post(
        "/api/jobs",
        headers=headers,
        json={
            "action": action,
            "execute": True,
            "confirmation": "sakila",
            "request_id": "test-request-1234",
            **extras,
        },
    )


def test_auth_csrf_origin(client):
    assert client.get("/api/state").status_code == 401
    assert (
        client.post(
            "/api/login", json={"username": "operator", "password": "test-password"}
        ).status_code
        == 403
    )
    headers = login(client)
    assert client.get("/api/state").status_code == 200
    assert start(client, ORIGIN).status_code == 403
    assert (
        start(client, {**headers, "origin": "https://evil.invalid"}).status_code == 403
    )
    assert client.post("/api/logout", headers=headers).status_code == 200
    assert client.get("/api/state").status_code == 401


def test_public_demo_hides_repository_and_real_refactor_results(client, tmp_path):
    repository = tmp_path / "repository"
    report = repository / "agents/refactor/query_refactor/advisor/results/job/report.md"
    report.write_text(
        "# Handoff: Laboratory query\n"
        "- Origem: Refactor\n"
        "- Destino: DBA\n"
        "- ID: `request-test`\n"
        "- Original literal: `query_refactor/advisor/results/job/original.sql`\n"
        "- Proposta canônica: `query_refactor/advisor/results/job/proposed.sql`\n"
        "- Tempo: original `1.0 s`, proposta `0.1 s`\n",
        encoding="utf-8",
    )
    assert len(RefactorDeliveries(repository).list()) == 1

    login(client)
    assert client.get("/api/dba/refactor-deliveries").json() == []

    with TestClient(create_public_demo()) as public:
        assert public.post("/api/access", headers=ORIGIN).status_code == 200
        assert public.get("/api/health").json() == {"status": "ok", "mode": "demo"}
        assert public.get("/api/dba/refactor-deliveries").json() == []
        assert public.get("/api/resources/health-check/scripts").status_code == 404
        assert public.get("/api/resources/health-check/skill").status_code == 404


def test_viewer_cannot_start_stop_or_propose(client):
    headers = login(client, "viewer")
    assert start(client, headers).status_code == 403
    assert client.post("/api/emergency-stop", headers=headers).status_code == 403
    assert (
        client.post(
            "/api/chat",
            headers=headers,
            json={"message": "contar atores", "intent": "propose"},
        ).status_code
        == 403
    )


def test_local_resource_editor_reads_writes_and_browses_fixed_paths(client):
    headers = login(client)
    skill = client.get("/api/resources/health-check/skill", headers=headers)
    assert skill.status_code == 200
    assert skill.json()["content"] == "# Skill\n"

    saved = client.put(
        "/api/resources/health-check/skill",
        headers=headers,
        json={
            "path": "agents/health-check/skills/health-check-skill.md",
            "content": "# Atualizada\n",
        },
    )
    assert saved.status_code == 200
    assert (
        client.get("/api/resources/health-check/skill", headers=headers).json()[
            "content"
        ]
        == "# Atualizada\n"
    )

    rules = client.get("/api/resources/health-check/rules", headers=headers)
    assert rules.json()["source_path"] == ("agents/health-check/advisor/rules.md")

    scripts = client.get("/api/resources/health-check/scripts", headers=headers)
    assert scripts.status_code == 200
    assert {
        "general-report/main.py",
        "general-report/sql/00_capabilities.sql",
        "health-check-refactor/README.md",
        "health-check-refactor/advisor.py",
        "health-check-refactor/analysis.schema.json",
        "health-check-refactor/collect_query_tuning_snapshot.py",
        "health-check-refactor/mcp_publisher.py",
        "health-check-refactor/results/analysis.json",
        "health-check-refactor/results/latest.html",
        "health-check-refactor/results/latest.json",
        "health-check-refactor/rules.md",
        "health-check-refactor/rules/collector-policy.json",
        "health-check-refactor/sql/120_slow_query_refactor.sql",
        "select_latency/results/latest.html",
        "select_latency/results/latest.json",
        "select_latency/sql/110_select_latency.sql",
        "select_latency/collector.py",
        "advisor/agent.py",
        "src/alerting/mcp_publisher.py",
    } <= set(scripts.json()["files"])
    assert all(
        path.startswith(
            (
                "advisor/",
                "contracts/",
                "src/",
                "general-report/",
                "health-check-refactor/",
                "select_latency/",
            )
        )
        for path in scripts.json()["files"]
    )
    assert (
        "health-check-refactor/results/runtime/private.json"
        not in scripts.json()["files"]
    )
    assert (
        "health-check-refactor/bad_queries_with_llm_refactor.sql"
        not in scripts.json()["files"]
    )
    assert not any(
        sibling in path
        for path in scripts.json()["files"]
        for sibling in ("agents/audit", "agents/dba", "agents/refactor")
    )
    script = client.get(
        "/api/resources/health-check/scripts",
        params={"path": "general-report/main.py"},
        headers=headers,
    )
    assert script.json()["path"] == "general-report/main.py"
    assert script.json()["source_path"] == "agents/health-check/general_report/main.py"
    latest = client.get(
        "/api/resources/health-check/scripts",
        params={"path": "select_latency/results/latest.html"},
        headers=headers,
    )
    assert latest.json()["editable"] is False
    refactor_latest = client.get(
        "/api/resources/health-check/scripts",
        params={"path": "health-check-refactor/results/latest.html"},
        headers=headers,
    )
    assert refactor_latest.status_code == 200
    assert refactor_latest.json()["source_path"] == (
        "agents/health-check/refactor_collector/results/latest.html"
    )
    assert refactor_latest.json()["editable"] is False
    assert (
        client.get(
            "/api/resources/health-check/scripts",
            params={"path": "health-check-refactor/results/runtime/private.json"},
            headers=headers,
        ).status_code
        == 404
    )
    assert (
        client.put(
            "/api/resources/health-check/scripts",
            headers=headers,
            json={
                "path": "select_latency/results/latest.html",
                "content": "altered",
            },
        ).status_code
        == 403
    )
    assert (
        client.get(
            "/api/resources/health-check/scripts",
            params={"path": "src/../../policy.json"},
            headers=headers,
        ).status_code
        == 404
    )
    assert (
        client.get(
            "/api/resources/health-check/scripts",
            params={"path": "src/../../audit/src/main.py"},
            headers=headers,
        ).status_code
        == 404
    )

    dba_scripts = client.get("/api/resources/dba/scripts", headers=headers)
    assert dba_scripts.json()["files"] == [
        "execucao-resumo/incident_analysis.py",
        "resumo-audit/prompt.md",
        "resumo-health-check/prompt.md",
    ]
    dba_summary = client.get(
        "/api/resources/dba/scripts",
        params={"path": "execucao-resumo/incident_analysis.py"},
        headers=headers,
    )
    assert dba_summary.json()["source_path"] == (
        "apps/lab-console/api/labconsole/incident_analysis.py"
    )
    assert dba_summary.json()["content"] == "SUMMARY_MODEL = 'gpt-5.6-luna'\n"
    dba_health_prompt = client.get(
        "/api/resources/dba/prompt-health-check", headers=headers
    )
    assert dba_health_prompt.status_code == 200
    assert dba_health_prompt.json()["source_path"] == (
        "agents/dba/analise-ocorrencia-health-check/prompt.md"
    )
    dba_audit_prompt = client.get("/api/resources/dba/prompt-audit", headers=headers)
    assert dba_audit_prompt.status_code == 200
    assert dba_audit_prompt.json()["source_path"] == (
        "agents/dba/analise-ocorrencia-audit/prompt.md"
    )
    assert client.get("/api/resources/dba/rules", headers=headers).status_code == 404

    mcp_scripts = client.get("/api/resources/mcp/scripts", headers=headers)
    assert mcp_scripts.json()["files"] == [
        "audit/alerting.py",
        "health-check/mcp_publisher.py",
        "health-query-refactor/mcp_publisher.py",
        "refactor/mcp_publisher.py",
        "servidor/server.py",
    ]
    server = client.get(
        "/api/resources/mcp/scripts",
        params={"path": "servidor/server.py"},
        headers=headers,
    )
    assert server.json()["content"] == "mcp.run(transport='stdio')\n"
    tools = client.get("/api/resources/mcp/tools", headers=headers)
    assert tools.json()["files"] == [
        "tools/README.md",
        "tools/incidents.py",
        "tools/refactor_workflow.py",
    ]
    contracts = client.get("/api/resources/mcp/contracts", headers=headers)
    assert contracts.json()["files"] == ["contracts/health.json"]

    refactor_scripts = client.get("/api/resources/refactor/scripts", headers=headers)
    assert refactor_scripts.json()["files"] == [
        "advisor/agent.py",
        "contracts/query_refactor_result.v1.schema.json",
        "contracts/refactor_advisor_proposal.v1.schema.json",
        "execucao/mcp_publisher.py",
        "execucao/mysql_client.py",
        "jobs-mcp/job/original.sql",
        "jobs-mcp/job/proposed.sql",
        "jobs-mcp/job/request.json",
        "jobs-mcp/job/result.json",
    ]
    request = client.get(
        "/api/resources/refactor/scripts",
        params={"path": "jobs-mcp/job/request.json"},
        headers=headers,
    )
    assert request.json()["editable"] is False

    simulation = client.get("/api/resources/simulation/scripts", headers=headers)
    assert simulation.status_code == 200
    assert simulation.json()["files"] == [
        "acoes/run-audit-alter-lab.py",
        "acoes/run-audit-drop-lab.py",
        "acoes/run-health-select-simulation.py",
        "acoes/run-slow-query-log-demo.py",
        "carga-latencia/sakila_read_demo_35.py",
    ]
    simulation_script = client.get(
        "/api/resources/simulation/scripts",
        params={"path": "acoes/run-audit-drop-lab.py"},
        headers=headers,
    )
    assert simulation_script.status_code == 200
    assert simulation_script.json()["source_path"] == (
        "apps/lab-console/scripts/run-audit-drop-lab.py"
    )

    notification_rules = client.get(
        "/api/resources/notification/rules", headers=headers
    )
    assert notification_rules.status_code == 200
    assert notification_rules.json()["source_path"] == (
        "agents/notification/notification/advisor/rules.md"
    )
    notification_scripts = client.get(
        "/api/resources/notification/scripts", headers=headers
    )
    assert notification_scripts.status_code == 200
    assert {
        "notification/advisor/agent.py",
        "notification/channels/email.py",
        "contracts/health_check_alert.v1.schema.json",
    } <= set(notification_scripts.json()["files"])


@pytest.mark.parametrize(
    "action",
    [
        "shell",
        "../scripts/anything",
        "DROP TABLE sakila.actor",
        "execute_sql_unrestricted",
    ],
)
def test_unknown_action_rejected(client, action):
    assert start(client, login(client), action).status_code == 422


def test_confirmation_arguments_and_independent_simulations(client):
    headers = login(client)
    assert start(client, headers, confirmation="").status_code == 422
    assert start(client, headers, sql="DROP TABLE actor").status_code == 422
    assert (
        start(client, headers, "notification.test", confirmation="").status_code == 422
    )
    assert start(client, headers, "audit.drop").status_code == 200
    assert (
        start(
            client,
            headers,
            "health.load",
            request_id="health-load-without-monitor",
        ).status_code
        == 200
    )


def test_demo_three_routes_and_artifacts(client):
    headers = login(client)
    job = start(client, headers).json()
    for _ in range(100):
        state = client.get("/api/state").json()
        if state["jobs"][0]["status"] == "succeeded":
            break
        time.sleep(0.02)
    assert state["jobs"][0]["status"] == "succeeded"
    events = state["events"]
    for kind in (
        "alert.detected",
        "mcp.validated",
        "notification.sent",
        "dba.recorded",
    ):
        assert len([e for e in events if e["type"] == kind]) == 3
    assert state["metrics"] == {
        "alerts_detected": 3,
        "deliveries_confirmed": 3,
        "delivery_failures": 0,
        "dba_records": 3,
    }
    assert {
        e["payload"].get("decision") for e in events if e["type"] == "alert.suppressed"
    } == {"cooldown_active", "duplicate"}
    assert len(state["artifacts"]) == 3
    artifact = state["artifacts"][0]
    report = client.get(
        f"/api/artifacts/{artifact['id']}/html", params={"sha": artifact["sha256"]}
    )
    assert report.status_code == 200
    assert artifact["audit_id"] in report.text
    assert "sandbox" in report.headers["content-security-policy"]
    assert "script-src 'none'" in report.headers["content-security-policy"]
    assert start(client, headers).json()["id"] == job["id"]
    assert start(client, headers, "health.lab").status_code == 409


def test_dashboard_metrics_are_not_limited_to_live_event_window(client):
    headers = login(client)
    store = client.app.state.control.store
    store.put("event", "persisted-alert", {"type": "alert.detected"})
    store.put("event", "persisted-delivery", {"type": "notification.sent"})
    store.put("event", "persisted-dba", {"type": "dba.recorded"})
    for index in range(301):
        store.put("event", f"progress-{index}", {"type": "lab.progress"})

    state = client.get("/api/state", headers=headers).json()

    assert not any(event["type"] == "alert.detected" for event in state["events"])
    assert state["metrics"] == {
        "alerts_detected": 1,
        "deliveries_confirmed": 1,
        "delivery_failures": 0,
        "dba_records": 1,
    }


def test_busy_cancel_stop_and_idempotence(client):
    headers = login(client)
    first = start(client, headers, "audit.lab").json()
    assert (
        start(client, headers, "audit.lab", request_id="other-request-1234").status_code
        == 409
    )
    assert (
        client.post(f"/api/jobs/{first['id']}/stop", headers=headers).status_code == 200
    )
    assert (
        client.post(f"/api/jobs/{first['id']}/stop", headers=headers).status_code == 200
    )
    assert client.get("/api/state").json()["jobs"][0]["status"] == "cancelled"


def test_demo_health_and_audit_stay_active_independently(client):
    headers = login(client)
    health = start(
        client,
        headers,
        "health.lab",
        request_id="health-monitor-1234",
    ).json()
    for _ in range(100):
        state = client.get("/api/state").json()
        health_state = next(job for job in state["jobs"] if job["id"] == health["id"])
        if health_state["status"] == "running":
            break
        time.sleep(0.01)
    assert health_state["status"] == "running"

    load = start(
        client,
        headers,
        "health.load",
        request_id="health-load-1234",
    ).json()
    for _ in range(100):
        state = client.get("/api/state").json()
        load_state = next(job for job in state["jobs"] if job["id"] == load["id"])
        if load_state["status"] == "succeeded":
            break
        time.sleep(0.01)
    assert load_state["status"] == "succeeded"
    assert any(
        event["type"] == "alert.detected"
        and event["payload"].get("category") == "query_latency"
        for event in state["events"]
    )

    audit = start(
        client,
        headers,
        "audit.lab",
        request_id="audit-monitor-1234",
    ).json()
    for _ in range(100):
        state = client.get("/api/state").json()
        audit_state = next(job for job in state["jobs"] if job["id"] == audit["id"])
        if audit_state["status"] == "running":
            break
        time.sleep(0.01)
    assert audit_state["status"] == "running"
    assert {
        job["action"]
        for job in state["jobs"]
        if job["status"] in {"queued", "validating", "starting", "ready", "running"}
    } >= {"health.lab", "audit.lab"}

    assert (
        client.post(f"/api/jobs/{health['id']}/stop", headers=headers).status_code
        == 200
    )
    state = client.get("/api/state").json()
    statuses = {job["action"]: job["status"] for job in state["jobs"]}
    assert statuses["health.lab"] == "cancelled"
    assert statuses["audit.lab"] == "running"
    assert (
        client.post(f"/api/jobs/{audit['id']}/stop", headers=headers).status_code == 200
    )


def test_proposal_requires_separate_approval(client):
    headers = login(client, "dba_approver")
    assert start(client, headers, "dba.actor_count").status_code == 403
    reply = client.post(
        "/api/chat",
        headers=headers,
        json={"message": "contar atores", "intent": "propose"},
    ).json()
    proposal = reply["proposal"]
    assert "sakila.actor" in proposal["sql"]
    assert client.get("/api/state").json()["jobs"] == []
    url = f"/api/approvals/{proposal['id']}"
    assert (
        client.post(
            url, headers=headers, json={"confirmation": "sakila", "password": "wrong"}
        ).status_code
        == 403
    )
    assert (
        client.post(
            url,
            headers=headers,
            json={"confirmation": "sakila", "password": "test-password"},
        ).status_code
        == 200
    )
    assert (
        client.post(
            url,
            headers=headers,
            json={"confirmation": "sakila", "password": "test-password"},
        ).status_code
        == 409
    )


@pytest.mark.parametrize(
    "message",
    [
        "DROP TABLE sakila.actor",
        "SELECT * FROM mysql.user",
        "contar atores; DROP TABLE film",
        "contar atores em airportdb",
    ],
)
def test_chat_rejects_free_sql(client, message):
    reply = client.post(
        "/api/chat",
        headers=login(client, "dba_approver"),
        json={"message": message, "intent": "propose"},
    ).json()
    assert "proposal" not in reply
    assert not client.get("/api/state").json()["jobs"]


def test_signatures_replay_expiry_and_direction():
    sender, receiver = Signer(KEY, "runner"), Signer(KEY, "runner")
    signed = sender.sign({"type": "stop"})
    assert receiver.verify(signed)["type"] == "stop"
    with pytest.raises(ValueError, match="replayed"):
        receiver.verify(signed)
    with pytest.raises(ValueError):
        Signer(KEY, "control").verify(sender.sign({}))
    signed["body"]["payload"] = {"type": "shell"}
    with pytest.raises(ValueError, match="signature"):
        receiver.verify(signed)


def test_sanitization_and_parsing():
    assert sanitize(
        {
            "password": "secret",
            "status": "ok",
            "actor": "a@b.invalid",
            "error_code": "password=bad",
            "total": 1,
        }
    ) == {"status": "ok", "total": 1}
    assert parse_line("password=secret at 10.1.1.1", "audit") == []
    result = parse_line(
        '[time] {"source_audit_id":"audit-123","publications":[{"dedupe_key":"audit-security:sakila:schema_change:sha256:alter","publication":{"accepted":true,"delivery_status":"sent","dba_status":"recorded"}}]}',
        "audit",
    )
    assert [e[1] for e in result] == [
        "alert.detected",
        "mcp.validated",
        "notification.sent",
        "dba.recorded",
    ]
    assert all(e[2]["category"] == "schema_change" for e in result)
    assert all(e[2]["audit_id"] == "audit-123" for e in result)
    assert len({e[3] for e in result}) == 1

    destructive = parse_line(
        '[time] {"source_audit_id":"audit-456","publications":[{"dedupe_key":"audit-security:sakila:destructive_ddl:sha256:drop","publication":{"accepted":true,"delivery_status":"sent","dba_status":"recorded"}}]}',
        "audit",
    )
    assert all(e[2]["category"] == "destructive_ddl" for e in destructive)
    assert all(e[2]["audit_id"] == "audit-456" for e in destructive)

    health = parse_line(
        json.dumps(
            {
                "publication_outcomes": [
                    {
                        "alert_id": "health-alert",
                        "category": "query_latency",
                        "accepted": True,
                        "delivery_status": "sent",
                        "dba_status": "recorded",
                    }
                ]
            }
        ),
        "health-check",
    )
    assert [e[1] for e in health] == [
        "alert.detected",
        "mcp.validated",
        "notification.sent",
        "dba.recorded",
    ]
    assert all(e[3] == "health-alert" for e in health)


def make_artifact(tmp_path):
    root = tmp_path / "agents/health-check/general_report/results"
    root.mkdir(parents=True)
    data = {
        "audit_id": "test-id",
        "collected_at": "2026-09-17T14:00:00Z",
        "schema_version": "test.v1",
    }
    (root / "report.json").write_text(json.dumps(data))
    (root / "report.html").write_text(
        f"<html>{data['audit_id']} {data['collected_at']}<script>alert(1)</script></html>"
    )
    return root


def test_artifact_identity_replace_and_no_paths(tmp_path):
    root = make_artifact(tmp_path)
    catalog = Catalog(tmp_path)
    items = catalog.list()
    assert len(items) == 1 and items[0]["html_available"]
    assert "test-id" in catalog.read(items[0]["id"], "html", items[0]["sha256"])
    with pytest.raises(ValueError):
        catalog.read("../../.env", "json", "")
    (root / "report.json").write_text('{"audit_id":"other-id"}')
    with pytest.raises(ValueError, match="replaced"):
        catalog.read(items[0]["id"], "json", items[0]["sha256"])


def test_artifact_accepts_mysql_user_at_host_but_blocks_email(tmp_path):
    root = make_artifact(tmp_path)
    report = json.loads((root / "report.json").read_text())
    principal = "admin@mysql.example.invalid"
    report["domains"] = {
        "active_sessions": {"metrics": {"sessions": [{"user": principal}]}}
    }
    (root / "report.json").write_text(json.dumps(report))
    (root / "report.html").write_text(
        f"<html>{report['audit_id']} {report['collected_at']} {principal}</html>"
    )

    catalog = Catalog(tmp_path)
    item = catalog.list()[0]
    assert item["html_available"]
    assert principal in catalog.read(item["id"], "html", item["sha256"])

    report["contact"] = "dba@example.invalid"
    (root / "report.json").write_text(json.dumps(report))
    assert Catalog(tmp_path).list() == []


def test_refactor_result_is_cataloged_from_its_job_directory(tmp_path):
    root = tmp_path / "agents/refactor/query_refactor/advisor/results/job"
    root.mkdir(parents=True)
    result = {
        "contract_version": "query_refactor_result.v1",
        "result_id": "11111111-1111-4111-8111-111111111111",
        "request_id": "22222222-2222-4222-8222-222222222222",
        "query_id": "correlated_running_total",
        "completed_at": "2026-10-02T13:27:24Z",
        "status": "approved_lab",
        "validation": {"equivalent": True},
    }
    content = json.dumps(result)
    (root / "result.json").write_text(content)
    (root / "request.json").write_text(content)
    catalog = Catalog(tmp_path)
    item = catalog.list()[0]
    assert len(catalog.list()) == 1
    assert item["source"] == "refactor"
    assert item["audit_id"] is None
    assert item["result_id"] == result["result_id"]
    assert item["request_id"] == result["request_id"]
    assert item["timestamp"] == result["completed_at"]
    assert item["retention"] == "historical"
    assert item["html_available"] is False
    assert catalog.read(item["id"], "json", item["sha256"]) == content
    with pytest.raises(ValueError, match="html_not_retained"):
        catalog.read(item["id"], "html", item["sha256"])
    result["password"] = "must-not-be-visible"
    (root / "result.json").write_text(json.dumps(result))
    assert catalog.list() == []


def test_artifact_symlink_sensitive_mime_size(tmp_path):
    root = make_artifact(tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_text("private")
    (root / "summary.json").symlink_to(outside)
    with pytest.raises(OSError):
        safe_read(root, root / "summary.json")
    with pytest.raises(ValueError):
        safe_read(root, root / "../outside.json")
    (root / "report.json").write_text('{"audit_id":"test", "password":"secret"}')
    assert Catalog(tmp_path).list() == []
    huge = root / "large.json"
    huge.write_bytes(b"x" * 2_000_001)
    with pytest.raises(ValueError):
        safe_read(root, huge)


def test_audit_summary_has_no_fabricated_html(tmp_path):
    root = tmp_path / "agents/dba/audit-security-alerts/runtime/inbox/event"
    root.mkdir(parents=True)
    (root / "alert-summary.json").write_text(
        '{"audit_id":"test", "detected_at":"2026-09-17", "category":"schema_change"}'
    )
    item = Catalog(tmp_path).list()[0]
    assert item["retention"] == "historical" and not item["html_available"]


def test_commands_and_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("SMTP_PASSWORD", "never-forward")
    monkeypatch.setenv("LAB_RUNNER_KEY", "never-forward")
    monkeypatch.setenv("AGENT_MONITORING_NOTIFY", "true")
    for action in (
        "health.lab",
        "audit.lab",
        "audit.drop",
        "audit.alter",
        "refactor.lab",
    ):
        argv, _ = command_for(tmp_path, action, False)
        assert "--execute" not in argv
        assert "--execute" in command_for(tmp_path, action, True)[0]
    health_monitor, _ = command_for(tmp_path, "health.lab", True)
    assert "--monitor-only" in health_monitor
    health_load, _ = command_for(tmp_path, "health.load", True)
    assert health_load[1].endswith("run-health-select-simulation.py")
    assert health_load[health_load.index("--confirm-demo") + 1] == (
        "HEALTH_SELECT_SIMULATION"
    )
    assert ACTIONS["health.load"]["timeout"] is None
    refactor_monitor, _ = command_for(tmp_path, "refactor.lab", True)
    assert refactor_monitor[1].endswith("run-refactor-lab.py")
    with pytest.raises(ValueError, match="requires_execution"):
        command_for(tmp_path, "health.load", False)
    with pytest.raises(ValueError):
        command_for(tmp_path, "shell", True)
    env = child_environment(tmp_path, tmp_path)
    assert "SMTP_PASSWORD" not in env and "LAB_RUNNER_KEY" not in env
    assert env["NOTIFICATION_DELIVERY_ENABLED"] == "false"
    assert env["AGENT_MONITORING_NOTIFY"] == "true"
    assert str(tmp_path) in env["UV_CACHE_DIR"]


def test_dba_read_commands_require_the_approved_login_reference(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "labconsole.runner.database_settings",
        lambda _: SimpleNamespace(login_file=tmp_path / "absent-profile.cnf"),
    )
    with pytest.raises(ValueError, match="approved_login_file_not_found"):
        command_for(tmp_path, "dba.actor_count", True)


def test_refactor_worker_is_started_only_by_refactor_action():
    health_launcher = (
        LAB_CONSOLE_ROOT / "scripts" / "run-health-check-lab.py"
    ).read_text(encoding="utf-8")
    refactor_launcher = (
        LAB_CONSOLE_ROOT / "scripts" / "run-refactor-lab.py"
    ).read_text(encoding="utf-8")

    assert '"health-refactor-advisor"' in health_launcher
    assert '"refactor-worker"' not in health_launcher
    assert "REFACTOR_ADVISOR" not in health_launcher
    assert '"monitor"' in refactor_launcher


@pytest.mark.asyncio
async def test_runner_denies_execution_and_unknown(tmp_path):
    async def send(value):
        pass

    runner = Runner(tmp_path, tmp_path, send)
    for action in ("shell", "health.lab"):
        with pytest.raises(ValueError):
            await runner.handle(
                {
                    "type": "start",
                    "job": {
                        "id": "1",
                        "action": action,
                        "mode": "integrated",
                        "execute": True,
                    },
                }
            )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_process_tree_stops_descendant_sessions():
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import subprocess,time; p=subprocess.Popen(['sleep','60'], start_new_session=True); print(p.pid,flush=True); time.sleep(60)",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True,
    )
    child_pid = int(await process.stdout.readline())
    tree = ProcessTree(process.pid)
    await tree.stop()
    await process.wait()
    if tree.capture_restricted:
        with contextlib.suppress(ProcessLookupError):
            os.kill(child_pid, signal.SIGKILL)
        pytest.skip("macOS process enumeration is restricted by this sandbox")
    assert (
        not psutil.pid_exists(child_pid)
        or psutil.Process(child_pid).status() == psutil.STATUS_ZOMBIE
    )


@pytest.mark.integration
def test_integrated_signed_runner_and_disconnect(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_RUNTIME", str(tmp_path))
    monkeypatch.setenv("LAB_RUNNER_RECONNECT_GRACE_SECONDS", "0.05")
    app = create_app(
        mode="integrated",
        database=f"sqlite:///{tmp_path}/real.db",
        users=USERS,
        runner_key=KEY,
    )
    sender, receiver = Signer(KEY, "control"), Signer(KEY, "runner")
    with TestClient(
        app, base_url="http://localhost:8000", client=("127.0.0.1", 50000)
    ) as client:
        headers = login(client)
        assert start(client, headers).status_code == 409
        with client.websocket_connect(
            "/runner", headers={"authorization": f"Bearer {KEY}"}
        ) as ws:
            ws.send_json(
                sender.sign(
                    {
                        "type": "heartbeat",
                        "execute_enabled": True,
                        "actions": list(ACTIONS),
                    }
                )
            )
            for _ in range(50):
                if client.get("/api/state").json()["runner"]["execute_enabled"]:
                    break
                time.sleep(0.01)
            job = start(client, headers, "audit.lab").json()
            command = receiver.verify(ws.receive_json())
            assert command["job"]["id"] == job["id"]
            ws.send_json(
                sender.sign({"type": "status", "job_id": job["id"], "status": "ready"})
            )
        for _ in range(50):
            state = client.get("/api/state").json()
            if state["jobs"][0]["status"] == "interrupted":
                break
            time.sleep(0.01)
        assert state["jobs"][0]["status"] == "interrupted"


@pytest.mark.integration
def test_integrated_runner_transient_disconnect_preserves_active_job(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LAB_RUNTIME", str(tmp_path))
    monkeypatch.setenv("LAB_RUNNER_RECONNECT_GRACE_SECONDS", "0.2")
    app = create_app(
        mode="integrated",
        database=f"sqlite:///{tmp_path}/transient.db",
        users=USERS,
        runner_key=KEY,
    )
    first_sender = Signer(KEY, "control")
    second_sender = Signer(KEY, "control")
    receiver = Signer(KEY, "runner")
    with TestClient(
        app, base_url="http://localhost:8000", client=("127.0.0.1", 50000)
    ) as client:
        headers = login(client)
        with client.websocket_connect(
            "/runner", headers={"authorization": f"Bearer {KEY}"}
        ) as first:
            first.send_json(
                first_sender.sign(
                    {
                        "type": "heartbeat",
                        "execute_enabled": True,
                        "actions": list(ACTIONS),
                    }
                )
            )
            job = start(client, headers, "audit.lab").json()
            receiver.verify(first.receive_json())
            first.send_json(
                first_sender.sign(
                    {"type": "status", "job_id": job["id"], "status": "ready"}
                )
            )

        with client.websocket_connect(
            "/runner", headers={"authorization": f"Bearer {KEY}"}
        ) as second:
            second.send_json(
                second_sender.sign(
                    {
                        "type": "heartbeat",
                        "execute_enabled": True,
                        "actions": list(ACTIONS),
                    }
                )
            )
            time.sleep(0.3)
            state = client.get("/api/state").json()
            current = next(item for item in state["jobs"] if item["id"] == job["id"])
            assert current["status"] == "ready"
            assert state["runner"]["online"] is True


def test_recovery_marks_old_jobs_interrupted(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_RUNTIME", str(tmp_path))
    db = f"sqlite:///{tmp_path}/restart.db"
    app = create_app(database=db, users=USERS)
    app.state.control.store.put(
        "job", "old-job", {"id": "old-job", "status": "running", "action": "audit.lab"}
    )
    restarted = create_app(database=db, users=USERS)
    assert (
        restarted.state.control.store.get("job", "old-job")["status"] == "interrupted"
    )


def test_artifact_parent_symlink_cannot_escape(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "report.json").write_text('{"audit_id":"outside"}')
    repository = tmp_path / "repo"
    agent = repository / "agents/health-check"
    agent.mkdir(parents=True)
    (agent / "results").mkdir()
    (agent / "results" / "health-check").symlink_to(outside, target_is_directory=True)
    assert Catalog(repository).list() == []


def test_artifacts_missing_mismatch_and_invalid_format(tmp_path):
    root = make_artifact(tmp_path)
    (root / "report.html").write_text("<html>another collection</html>")
    catalog = Catalog(tmp_path)
    item = catalog.list()[0]
    assert not item["html_available"]
    with pytest.raises(ValueError):
        catalog.read(item["id"], "html", item["sha256"])
    with pytest.raises(ValueError):
        catalog.read(item["id"], "exe", item["sha256"])
    (root / "report.json").unlink()
    with pytest.raises(ValueError):
        catalog.read(item["id"], "json", item["sha256"])


def test_notification_adapter_two_gates(tmp_path, monkeypatch):
    from labconsole.notification_probe import run

    scripts = tmp_path / "apps/lab-console/scripts"
    scripts.mkdir(parents=True)
    (scripts / "notification_config.py").write_text(
        "def load_notification_environment(_path):\n"
        '    return {"SMTP_HOST":"smtp.example.invalid"}\n'
    )
    calls = []

    class Result:
        returncode = 0

    def execute(argv, **kwargs):
        calls.append((argv, kwargs))
        return Result()

    monkeypatch.setattr("labconsole.notification_probe.subprocess.run", execute)
    monkeypatch.setenv("SMTP_PASSWORD", "not-forwarded")
    assert run(tmp_path, False) == 0
    assert "--send" not in calls[0][0]
    assert calls[0][1]["env"]["NOTIFICATION_DELIVERY_ENABLED"] == "false"
    assert run(tmp_path, True) == 0
    assert "--send" in calls[1][0]
    assert calls[1][1]["env"]["NOTIFICATION_DELIVERY_ENABLED"] == "true"
    assert "SMTP_PASSWORD" not in calls[1][1]["env"]


def test_signature_expiry(monkeypatch):
    sender = Signer(KEY, "runner")
    envelope = sender.sign({"type": "stop"})
    monkeypatch.setattr(
        "labconsole.security.time.time", lambda: envelope["body"]["expires"] + 1
    )
    with pytest.raises(ValueError, match="expired"):
        Signer(KEY, "runner").verify(envelope)


def test_operator_cannot_approve(client):
    headers = login(client, "operator")
    assert start(client, headers, "dba.actor_count").status_code == 403


def test_openapi_version_and_reject_extra_fields(client):
    schema = client.get("/openapi.json").json()
    assert schema["info"]["version"] == "0.1.0"
    assert schema["components"]["schemas"]["StartJob"]["additionalProperties"] is False
    assert start(client, login(client), argv=["shell"]).status_code == 422


def test_notification_dispatch_calls_only_existing_runtime(
    tmp_path, monkeypatch, capsys
):
    import types

    from labconsole.notification_dispatch import main

    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    (fixtures / "connection-warning.json").write_text('{"severity":"warning"}')
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["notification_dispatch", "--send"])
    monkeypatch.setenv("NOTIFICATION_DELIVERY_ENABLED", "false")
    assert main() == 2
    calls = []

    class Dispatcher:
        def dispatch(self, alert):
            calls.append(alert)
            return {"status": "sent", "delivered": True}

    fake = types.ModuleType("notification.runtime")
    fake.build_dispatcher = lambda: Dispatcher()
    monkeypatch.setitem(sys.modules, "notification.runtime", fake)
    monkeypatch.setenv("NOTIFICATION_DELIVERY_ENABLED", "true")
    assert main() == 0
    assert calls == [{"severity": "warning"}]
    assert '"status": "sent"' in capsys.readouterr().out
