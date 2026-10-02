from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

Role = Literal["viewer", "operator", "dba_approver"]
Mode = Literal["demo", "integrated"]
JobStatus = Literal[
    "queued",
    "validating",
    "starting",
    "ready",
    "running",
    "stopping",
    "succeeded",
    "failed",
    "cancelled",
    "interrupted",
    "timed_out",
]
ACTIVE = {"queued", "validating", "starting", "ready", "running", "stopping"}


def now() -> str:
    return datetime.now(UTC).isoformat()


def uid() -> str:
    return str(uuid4())


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Login(Strict):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class StartJob(Strict):
    action: str = Field(max_length=64)
    execute: bool = False
    confirmation: str = Field(default="", max_length=64)
    request_id: str = Field(min_length=8, max_length=80, pattern=r"^[a-zA-Z0-9-]+$")
    approval_id: str | None = None


class ChatRequest(Strict):
    message: str = Field(min_length=1, max_length=2000)
    intent: Literal["ask", "propose"] = "ask"


class IncidentMessage(Strict):
    role: Literal["user", "assistant"]
    text: str = Field(min_length=1, max_length=4000)


class IncidentQuestion(Strict):
    message: str = Field(min_length=1, max_length=2000)
    history: list[IncidentMessage] = Field(default_factory=list, max_length=12)


class ApprovalRequest(Strict):
    confirmation: str = Field(max_length=64)
    password: str = Field(default="", max_length=256)


class ResourceWrite(Strict):
    path: str = Field(default="", max_length=512)
    content: str = Field(max_length=1_000_000)


class Event(Strict):
    version: str = "lab_event.v1"
    event_id: str = Field(default_factory=uid)
    correlation_id: str
    job_id: str
    occurred_at: str = Field(default_factory=now)
    source: str
    type: str
    severity: str = "info"
    payload: dict[str, Any] = Field(default_factory=dict)


# Commands are fixed. Neither the API nor the model can supply argv or SQL.
ACTIONS: dict[str, dict[str, Any]] = {
    "health.lab": {
        "agent": "health-check",
        "label": "Ativar Health Check",
        "script": "run-health-check-lab.py",
        "timeout": 3600,
        "resources": ["health"],
    },
    "health.load": {
        "agent": "health-check",
        "label": "Iniciar consultas no banco",
        "timeout": None,
        "resources": ["health.load"],
    },
    "health.collect": {
        "agent": "health-check",
        "label": "Coleta atual",
        "timeout": 120,
        "resources": ["health"],
    },
    "audit.lab": {
        "agent": "audit",
        "label": "Ativar Audit",
        "script": "run-audit-lab.py",
        "timeout": 3600,
        "resources": ["audit"],
    },
    "audit.drop": {
        "agent": "audit",
        "label": "Executar comando DROP",
        "script": "run-audit-drop-lab.py",
        "timeout": 330,
        "resources": ["audit.drop"],
    },
    "audit.alter": {
        "agent": "audit",
        "label": "Executar comando ALTER",
        "script": "run-audit-alter-lab.py",
        "timeout": 330,
        "resources": ["audit.alter"],
    },
    "refactor.lab": {
        "agent": "refactor",
        "label": "Ativar Refactor",
        "script": "run-refactor-lab.py",
        "timeout": 3600,
        "resources": ["refactor"],
    },
    "lab.three": {
        "agent": "dba",
        "label": "Demonstração dos 3 alertas",
        "timeout": 900,
        "resources": [
            "health",
            "health.load",
            "audit",
            "audit.drop",
            "audit.alter",
        ],
    },
    "notification.test": {
        "agent": "notification",
        "label": "Teste manual do Notification",
        "timeout": 30,
        "resources": ["notification"],
    },
    "dba.actor_count": {
        "agent": "dba",
        "label": "Contar atores em sakila",
        "timeout": 15,
        "resources": ["query"],
        "sql": "SELECT /*+ MAX_EXECUTION_TIME(5000) */ COUNT(*) AS total FROM sakila.actor LIMIT 1",
    },
    "dba.film_count": {
        "agent": "dba",
        "label": "Contar filmes em sakila",
        "timeout": 15,
        "resources": ["query"],
        "sql": "SELECT /*+ MAX_EXECUTION_TIME(5000) */ COUNT(*) AS total FROM sakila.film LIMIT 1",
    },
}
