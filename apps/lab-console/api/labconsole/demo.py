import asyncio
import hashlib
import html
import json
from typing import Any

from .models import ACTIONS, now, uid

CATEGORIES = [
    ("health-check", "query_latency"),
    ("audit", "destructive_ddl"),
    ("audit", "schema_change"),
]

# The interactive demo follows the official latency measurement window.  Tests
# shorten the simulated second through LAB_DEMO_DELAY; no database workload is
# ever executed by this module.
HEALTH_LOAD_MEASUREMENT_SECONDS = 300


def artifact(
    source: str, category: str, correlation: str
) -> tuple[dict[str, Any], dict[str, str]]:
    timestamp, audit_id = now(), uid()
    identifier = uid()
    data = {
        "schema_version": "lab_demo.v1",
        "audit_id": audit_id,
        "collected_at": timestamp,
        "category": category,
        "scope": "sakila",
        "demo": True,
        "observed": {"p99_seconds": 2.84, "window_seconds": 30, "coverage": 99.2}
        if source == "health-check"
        else {"mysql_errno": 1142, "outcome": "expected_permission_denied"},
    }
    raw = json.dumps(data, indent=2)
    title = {
        "query_latency": "Latência sob observação",
        "destructive_ddl": "Tentativa de DROP bloqueada",
        "schema_change": "Tentativa de ALTER bloqueada",
    }[category]
    document = f"""<!doctype html><html lang="pt-BR"><meta charset="utf-8"><title>{title}</title><style>body{{background:#101720;color:#dde8ee;font:15px system-ui;padding:36px;line-height:1.7}}small{{color:#79cfba;letter-spacing:2px}}h1{{font-size:30px}}section{{padding:22px;border:1px solid #334453;border-radius:14px;margin:22px 0}}pre{{white-space:pre-wrap;color:#acd9da}}p{{color:#b6c8d0}}</style><small>MYSQLCONF / EVIDÊNCIA DE DEMONSTRAÇÃO</small><h1>{title}</h1><p>Relatório fictício para validar a interface. Nenhuma consulta ou entrega real foi executada.</p><section><b>Schema sakila · Severidade critical</b><p>{"P99 de 2,84 segundos; limite do agente Luna de 2,0 segundos; janela de 30 s." if source == "health-check" else "MySQL negou a tentativa por permissão. Código 1142. Nenhuma alteração aplicada."}</p></section><section><b>Identidade da coleta</b><p>{audit_id}<br>{timestamp}</p><pre>{html.escape(raw)}</pre></section></html>"""
    metadata = {
        "id": identifier,
        "source": source,
        "location": "demo-inbox",
        "name": title,
        "audit_id": audit_id,
        "alert_id": correlation,
        "category": category,
        "severity": "critical",
        "timestamp": timestamp,
        "retention": "demo",
        "html_available": True,
        "sha256": hashlib.sha256(raw.encode()).hexdigest(),
        "size": len(raw),
        "schema_version": "lab_demo.v1",
    }
    return metadata, {"json": raw, "html": document}


async def run_demo(
    job: dict[str, Any],
    emit: Any,
    set_status: Any,
    add_artifact: Any,
    delay: float = 0.5,
) -> None:
    source = ACTIONS[job["action"]]["agent"]
    for status in ("validating", "starting", "ready", "running"):
        await set_status(job["id"], status)
        await asyncio.sleep(delay)
    if job["action"] == "health.load" and job["execute"]:
        for _ in range(HEALTH_LOAD_MEASUREMENT_SECONDS):
            await asyncio.sleep(delay)
    if not job["execute"]:
        await emit(job, source, "lab.progress", {"status": "dry_run"})
    elif job["action"].startswith("dba."):
        await emit(
            job,
            "dba",
            "query.completed",
            {"total": 200 if "actor" in job["action"] else 1000, "status": "simulated"},
        )
    elif job["action"] in {"health.lab", "audit.lab"}:
        await emit(job, source, "agent.ready", {"status": "ready"})
        await asyncio.Event().wait()
    else:
        selected = (
            CATEGORIES
            if job["action"] == "lab.three"
            else [
                CATEGORIES[1]
                if job["action"] == "audit.drop"
                else CATEGORIES[2]
                if job["action"] == "audit.alter"
                else CATEGORIES[0]
            ]
        )
        if job["action"] == "notification.test":
            await emit(
                job,
                "notification",
                "notification.sent",
                {"delivery_status": "sent", "status": "simulated"},
            )
            selected = []
        for origin, category in selected:
            correlation = uid()
            payload = {
                "category": category,
                "severity": "critical",
                "alert_id": correlation,
            }
            if origin == "audit":
                await emit(
                    job,
                    origin,
                    "audit.attempt.denied",
                    {**payload, "mysql_errno": 1142},
                    correlation,
                )
            stages: list[tuple[str, str, dict[str, Any]]] = [
                (origin, "alert.detected", {}),
                ("mcp", "mcp.validated", {"accepted": True}),
                (
                    "notification",
                    "notification.sent",
                    {"delivery_status": "sent", "status": "simulated"},
                ),
                (
                    "dba",
                    "dba.recorded",
                    {"dba_status": "recorded", "status": "simulated"},
                ),
            ]
            for actor, event, extra in stages:
                await emit(job, actor, event, {**payload, **extra}, correlation)
                await asyncio.sleep(delay)
            metadata, contents = artifact(origin, category, correlation)
            add_artifact(metadata, contents)
            await emit(
                job,
                origin,
                "artifact.available",
                {**payload, "artifact_id": metadata["id"]},
                correlation,
            )
            await emit(
                job,
                origin,
                "alert.suppressed",
                {
                    **payload,
                    "decision": "cooldown_active"
                    if origin == "health-check"
                    else "duplicate",
                },
                correlation,
            )
    await set_status(job["id"], "succeeded")
