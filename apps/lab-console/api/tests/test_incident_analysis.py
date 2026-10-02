import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from labconsole.app import create_app
from labconsole.incident_analysis import (
    CHAT_MODEL,
    CHAT_REASONING_EFFORT,
    SUMMARY_MODEL,
    SUMMARY_REASONING_EFFORT,
    CodexAnalyzer,
    IncidentAnalysis,
    IncidentAnalysisError,
)

ORIGIN = {"origin": "http://localhost:3000"}
KEY = "incident-test-key-not-a-secret-1234567890"
RECORD_ID = (
    "2026-09-18T12-24-44.567000Z__query_latency__217041be-3c7b-4db4-b473-549b27d8f4f8"
)
NEW_RECORD_ID = (
    "2026-09-18T12-25-44.567000Z__query_latency__317041be-3c7b-4db4-b473-549b27d8f4f8"
)


class FakeLuna:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def analyze(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return (
            "## Resumo do alerta\nP99 acima do limite registrado.\n\n"
            "## Evidências registradas\nP99 de 2.29 segundos.\n\n"
            "## Dados do relatório\nEscopo sakila.\n\n"
            "## Informações ausentes\nO motivo não está informado nos arquivos.\n"
        )


class SensitiveLookingLuna:
    def analyze(self, prompt: str) -> str:
        return "## Resumo do alerta\nOrigem observada: admin@example.com.\n"


def test_only_dba_chat_uses_sol_medium(tmp_path):
    service = IncidentAnalysis(tmp_path)

    assert isinstance(service.analyzer, CodexAnalyzer)
    assert service.analyzer.model == SUMMARY_MODEL == "gpt-5.6-luna"
    assert service.analyzer.reasoning_effort == SUMMARY_REASONING_EFFORT == "low"
    assert isinstance(service.chat_analyzer, CodexAnalyzer)
    assert service.chat_analyzer.model == CHAT_MODEL == "gpt-5.6-sol"
    assert service.chat_analyzer.reasoning_effort == CHAT_REASONING_EFFORT == "medium"


def prepare_repository(root: Path, record_id: str = RECORD_ID) -> Path:
    health = root / "agents/dba/analise-ocorrencia-health-check"
    audit = root / "agents/dba/analise-ocorrencia-audit"
    for directory in (health, audit):
        (directory / "results").mkdir(parents=True, exist_ok=True)
        (directory / "prompt.md").write_text(
            "Resuma fatos. Não recomende melhorias e não investigue o motivo.\n"
        )
    chat = root / "agents/dba/chat"
    chat.mkdir(parents=True, exist_ok=True)
    (chat / "prompt.md").write_text(
        "Você é um DBA sênior de MySQL. Use o contexto e faça perguntas específicas.\n"
    )
    record = root / "agents/dba/health-check-alerts/runtime/inbox" / record_id
    record.mkdir(parents=True)
    wrapper = {
        "received_at": "2026-09-18T12:24:49.674Z",
        "alert": {
            "alert_id": "217041be-3c7b-4db4-b473-549b27d8f4f8",
            "audit_id": "f45b374f-1460-4531-bcb9-e9e7b84a5270",
            "detected_at": "2026-09-18T12:24:44.567Z",
            "title": "P99 above 2 seconds",
            "summary": "P99 exceeded the recorded threshold.",
            "severity": "critical",
            "category": "query_latency",
            "findings": [
                {
                    "metric": "p99_seconds",
                    "observed_value": 2.29,
                    "threshold": 0.002,
                }
            ],
        },
    }
    (record / "alert.json").write_text(json.dumps(wrapper))
    (record / "latest.json").write_text(
        json.dumps(
            {
                "audit_id": "f45b374f-1460-4531-bcb9-e9e7b84a5270",
                "scope": {"schemas": ["sakila"]},
                "p99_seconds": 2.29,
            }
        )
    )
    (record / "latest.html").write_text(
        "<html><body>P99 2.29 seconds; schema sakila</body></html>"
    )
    return record


def test_incident_summary_is_persisted_and_reused(tmp_path):
    prepare_repository(tmp_path)
    luna = FakeLuna()
    service = IncidentAnalysis(tmp_path, luna)

    incidents = service.list()
    assert len(incidents) == 1
    assert incidents[0]["analysis_status"] == "historical"
    assert incidents[0]["source"] == "health-check"
    assert (
        incidents[0]["title"]
        == "P99 acima de 2 segundos na SELECT monitorada do Sakila"
    )
    assert "2.0 segundos" in incidents[0]["summary"]
    assert incidents[0]["rule_correction"]

    detail = service.detail("health-check", RECORD_ID)
    assert detail["analysis_status"] == "historical"
    assert detail["analysis"] is None
    assert not luna.prompts

    assert service.summarize("health-check", RECORD_ID) == "ready"
    detail = service.detail("health-check", RECORD_ID)
    assert detail["analysis_status"] == "ready"
    assert "O motivo não está informado" in detail["analysis"]
    assert len(luna.prompts) == 1
    assert "latest.html" not in luna.prompts[0]
    assert "latest.json" in luna.prompts[0]
    assert "Não recomende melhorias" in luna.prompts[0]

    service.detail("health-check", RECORD_ID)
    assert len(luna.prompts) == 1
    result = (
        tmp_path
        / "agents/dba/analise-ocorrencia-health-check/results"
        / RECORD_ID
        / "summary.md"
    )
    assert result.is_file()
    assert service.list()[0]["analysis_status"] == "ready"

    original = service.evidence("health-check", RECORD_ID, "alert.json").decode()
    assert '"threshold": 0.002' in original


def test_incident_summary_redacts_sensitive_looking_output(tmp_path):
    prepare_repository(tmp_path)
    service = IncidentAnalysis(tmp_path, SensitiveLookingLuna())

    assert service.summarize("health-check", RECORD_ID) == "ready"
    detail = service.detail("health-check", RECORD_ID)
    assert detail["analysis_status"] == "ready"
    assert "admin@example.com" not in detail["analysis"]
    assert "[redacted]" in detail["analysis"]


def test_delete_removes_inbox_record_and_generated_summary(tmp_path):
    record = prepare_repository(tmp_path)
    service = IncidentAnalysis(tmp_path, FakeLuna())
    assert service.summarize("health-check", RECORD_ID) == "ready"
    summary_directory = (
        tmp_path / "agents/dba/analise-ocorrencia-health-check/results" / RECORD_ID
    )
    assert summary_directory.is_dir()

    result = service.delete("health-check", RECORD_ID)

    assert result == {
        "deleted": True,
        "source": "health-check",
        "record_id": RECORD_ID,
    }
    assert not record.exists()
    assert not summary_directory.exists()
    assert service.list() == []
    with pytest.raises(IncidentAnalysisError, match="incident_not_found"):
        service.delete("health-check", RECORD_ID)


def test_incident_questions_use_only_selected_evidence(tmp_path):
    prepare_repository(tmp_path)
    luna = FakeLuna()
    service = IncidentAnalysis(tmp_path, luna)

    service.summarize("health-check", RECORD_ID)
    luna.prompts.clear()
    answer = service.answer(
        "health-check",
        RECORD_ID,
        "Qual P99 foi registrado?",
        [{"role": "user", "text": "Estamos olhando apenas esta ocorrência."}],
    )
    assert "P99" in answer
    assert "Qual P99 foi registrado?" in luna.prompts[0]
    assert "DBA sênior de MySQL" in luna.prompts[0]
    assert "generated_summary_markdown" in luna.prompts[0]
    assert "Estamos olhando apenas esta ocorrência." in luna.prompts[0]
    assert '"source": "health-check"' in luna.prompts[0]
    with pytest.raises(IncidentAnalysisError, match="incident_question_invalid"):
        service.answer("health-check", RECORD_ID, " ")
    with pytest.raises(IncidentAnalysisError, match="incident_evidence_not_allowed"):
        service.evidence("health-check", RECORD_ID, "../../MEMORY.md")


def test_integrated_api_summarizes_only_new_incidents_and_opens_evidence(
    tmp_path, monkeypatch
):
    repository = tmp_path / "repository"
    prepare_repository(repository)
    monkeypatch.setenv("LAB_REPOSITORY_ROOT", str(repository))
    monkeypatch.setenv("LAB_RUNTIME", str(tmp_path / "runtime"))
    monkeypatch.setenv("LAB_INCIDENT_POLL_SECONDS", "0.01")
    luna = FakeLuna()
    app = create_app(
        mode="integrated",
        database=f"sqlite:///{tmp_path}/console.db",
        users={},
        runner_key=KEY,
        incident_analyzer=luna,
    )

    with TestClient(
        app, base_url="http://localhost:8000", client=("127.0.0.1", 50000)
    ) as client:
        access = client.post("/api/access", headers=ORIGIN)
        assert access.status_code == 200
        csrf = access.json()["csrf"]
        incidents = client.get("/api/dba/incidents").json()
        assert len(incidents) == 1
        assert client.get("/api/state").json()["metrics"] == {
            "alerts_detected": 1,
            "deliveries_confirmed": 0,
            "delivery_failures": 0,
            "dba_records": 1,
        }
        detail = client.get(f"/api/dba/incidents/health-check/{RECORD_ID}").json()
        assert detail["analysis_status"] == "historical"
        assert not luna.prompts

        prepare_repository(repository, NEW_RECORD_ID)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            new_detail = client.get(
                f"/api/dba/incidents/health-check/{NEW_RECORD_ID}"
            ).json()
            if new_detail["analysis_status"] == "ready":
                break
            time.sleep(0.02)
        assert new_detail["analysis_status"] == "ready"
        assert len(luna.prompts) == 1
        store = client.app.state.control.store
        store.put(
            "event",
            "matching-delivery",
            {
                "type": "notification.sent",
                "payload": {"alert_id": "217041be-3c7b-4db4-b473-549b27d8f4f8"},
            },
        )
        assert client.get("/api/state").json()["metrics"]["deliveries_confirmed"] == 1
        evidence = client.get(
            f"/api/dba/incidents/health-check/{RECORD_ID}/evidence/latest.html"
        )
        assert evidence.status_code == 200
        assert "schema sakila" in evidence.text
        assert "sandbox" in evidence.headers["content-security-policy"]
        question = client.post(
            f"/api/dba/incidents/health-check/{RECORD_ID}/question",
            headers={**ORIGIN, "x-csrf-token": csrf},
            json={"message": "Qual P99 foi registrado?"},
        )
        assert question.status_code == 200
        assert "P99" in question.json()["answer"]
        conversation = client.get(
            f"/api/dba/incidents/health-check/{RECORD_ID}/conversation"
        )
        assert conversation.status_code == 200
        assert [item["role"] for item in conversation.json()["messages"]] == [
            "user",
            "assistant",
        ]
        assert conversation.json()["messages"][0]["text"] == (
            "Qual P99 foi registrado?"
        )
        other_conversation = client.get(
            f"/api/dba/incidents/health-check/{NEW_RECORD_ID}/conversation"
        )
        assert other_conversation.status_code == 200
        assert other_conversation.json()["messages"] == []

        denied = client.delete(
            f"/api/dba/incidents/health-check/{RECORD_ID}",
            headers=ORIGIN,
        )
        assert denied.status_code == 403
        assert (
            repository / "agents/dba/health-check-alerts/runtime/inbox" / RECORD_ID
        ).is_dir()

        deleted = client.delete(
            f"/api/dba/incidents/health-check/{RECORD_ID}",
            headers={**ORIGIN, "x-csrf-token": csrf},
        )
        assert deleted.status_code == 200
        assert deleted.json()["deleted"] is True
        assert store.get("incident_chat", f"health-check:{RECORD_ID}") is None
        assert not (
            repository / "agents/dba/health-check-alerts/runtime/inbox" / RECORD_ID
        ).exists()
        assert all(
            item["record_id"] != RECORD_ID
            for item in client.get("/api/dba/incidents").json()
        )

        second_deleted = client.delete(
            f"/api/dba/incidents/health-check/{NEW_RECORD_ID}",
            headers={**ORIGIN, "x-csrf-token": csrf},
        )
        assert second_deleted.status_code == 200
        store.put("event", "historical-alert", {"type": "alert.detected"})
        store.put(
            "event",
            "historical-delivery",
            {
                "type": "notification.sent",
                "payload": {"alert_id": "deleted-alert-id"},
            },
        )
        store.put("event", "historical-dba", {"type": "dba.recorded"})
        assert client.get("/api/state").json()["metrics"] == {
            "alerts_detected": 0,
            "deliveries_confirmed": 0,
            "delivery_failures": 0,
            "dba_records": 0,
        }
