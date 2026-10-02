import asyncio
import contextlib
import html
import json
import logging
import os
import secrets
import time
from collections import defaultdict, deque
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import JSONResponse, Response, StreamingResponse

from .artifacts import Catalog
from .demo import run_demo
from .incident_analysis import (
    IncidentAnalysis,
    IncidentAnalysisError,
    TextAnalyzer,
    sanitize_chat_text,
    watch_new_incidents,
)
from .models import (
    ACTIONS,
    ACTIVE,
    ApprovalRequest,
    ChatRequest,
    Event,
    IncidentQuestion,
    Login,
    ResourceWrite,
    StartJob,
    now,
    uid,
)
from .refactor_deliveries import RefactorDeliveries
from .resources import ResourceStore
from .security import Signer, digest, sanitize, verify_password
from .store import Store

HTML_CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src 'none'; script-src 'none'; form-action 'none'; base-uri 'none'; sandbox"
LOGGER = logging.getLogger("labconsole.control")


def _render_console_report(location: str, json_content: str) -> str:
    """Render a compact, technical view of a validated report pair."""
    report = json.loads(json_content)
    if not isinstance(report, dict):
        raise ValueError("artifact_json_not_object")

    def text(value: Any) -> str:
        return html.escape(str(value if value is not None else "—"), quote=True)

    titles = {
        "health": "Health Check",
        "latency": "Latência de SELECTs",
        "audit": "Audit Security",
    }
    title = titles[location]
    collected_at = report.get("collected_at") or report.get("finished_at") or "—"
    audit_id = report.get("audit_id", "—")
    status = report.get("status") or report.get("overall_status") or "available"
    raw_domains = report.get("domains")
    domains: dict[str, Any] = raw_domains if isinstance(raw_domains, dict) else {}
    cards: list[str] = []
    for name, domain in domains.items():
        value = domain if isinstance(domain, dict) else {}
        domain_status = value.get("status", "available")
        rows = value.get("row_count_returned")
        duration = value.get("duration_ms")
        details = []
        if rows is not None:
            details.append(f"{text(rows)} linhas")
        if duration is not None:
            details.append(f"{text(duration)} ms")
        cards.append(
            "<article class='domain-card'>"
            f"<h3>{text(name)}</h3><span class='status'>{text(domain_status)}</span>"
            f"<p>{' · '.join(details) or 'Evidência disponível'}</p></article>"
        )

    if location == "latency":
        session = report.get("activity_session") or {}
        digest_count = report.get("select_digest_count", 0)
        activity = session.get("status", status)
        cards = [
            "<article class='metric'><span>Estado</span>"
            f"<strong>{text(activity)}</strong></article>",
            "<article class='metric'><span>Digests</span>"
            f"<strong>{text(digest_count)}</strong></article>",
            "<article class='metric'><span>Janela</span>"
            f"<strong>{text(report.get('window_target_seconds'))} s</strong></article>",
        ]
    summary = (
        "Monitorando somente evidências de execução no schema sakila."
        if location == "latency"
        else "Relatório técnico derivado da última coleta validada."
    )
    return f"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{text(title)}</title><style>
:root {{ color-scheme: dark; }} body {{ margin:0; background:#0b1117; color:#dbe6ee; font:15px/1.5 ui-sans-serif,system-ui,sans-serif; }}
main {{ max-width:1080px; margin:auto; padding:40px 24px 64px; }} header {{ border-bottom:1px solid #28404f; padding-bottom:24px; }}
.eyebrow {{ color:#78d6ea; font-size:12px; font-weight:700; letter-spacing:.14em; text-transform:uppercase; }} h1 {{ margin:6px 0; font-size:32px; }}
.meta,.grid {{ display:grid; gap:12px; grid-template-columns:repeat(auto-fit,minmax(190px,1fr)); }} .meta {{ margin-top:20px; }}
.meta div,.metric,.domain-card {{ background:#101b24; border:1px solid #28404f; border-radius:10px; padding:14px; }}
.label,.metric span {{ color:#91a7b6; display:block; font-size:12px; text-transform:uppercase; letter-spacing:.08em; }} strong {{ font-size:18px; }}
.summary {{ color:#aabcc8; margin:22px 0; }} .grid {{ margin-top:14px; }} .domain-card h3 {{ font-size:15px; margin:0 0 8px; }} .domain-card p {{ color:#91a7b6; margin:8px 0 0; }}
.status {{ color:#89ddba; font-size:12px; font-weight:700; }} details {{ margin-top:28px; border:1px solid #28404f; border-radius:10px; background:#101b24; }} summary {{ cursor:pointer; padding:14px; font-weight:700; }} pre {{ margin:0; border-top:1px solid #28404f; max-height:560px; overflow:auto; padding:16px; color:#c8d7e1; font:12px/1.45 ui-monospace,SFMono-Regular,monospace; white-space:pre-wrap; }}
</style></head><body><main><header><div class="eyebrow">Agent Monitoring DB · Evidência técnica</div><h1>{text(title)}</h1>
<div class="meta"><div><span class="label">Status</span><strong>{text(status)}</strong></div><div><span class="label">Coletado em</span>{text(collected_at)}</div><div><span class="label">Audit ID</span><code>{text(audit_id)}</code></div></div></header>
<p class="summary">{summary}</p><section class="grid">{"".join(cards)}</section>
<details><summary>JSON técnico completo</summary><pre>{text(json_content)}</pre></details></main></body></html>"""


class Control:
    def __init__(
        self,
        mode: str,
        database: str,
        users: dict[str, Any],
        runner_key: str,
        origin: str,
        incident_alert_ids: Callable[[], set[str]] | None = None,
    ):
        self.mode, self.users, self.runner_key, self.origin = (
            mode,
            users,
            runner_key,
            origin,
        )
        self.store = Store(database)
        self.sessions: dict[str, Any] = {}
        self.tasks: dict[str, asyncio.Task[Any]] = {}
        self.runner: WebSocket | None = None
        self.runner_seen = 0.0
        self.runner_execute = False
        self.runner_scope: list[str] = []
        self.runner_catalog: list[dict[str, Any]] = []
        self.runner_generation = 0
        self.runner_disconnect_task: asyncio.Task[Any] | None = None
        self.runner_reconnect_grace = float(
            os.environ.get("LAB_RUNNER_RECONNECT_GRACE_SECONDS", "15")
        )
        if not 0 <= self.runner_reconnect_grace <= 60:
            raise ValueError("runner_reconnect_grace_out_of_range")
        self.pending: dict[str, asyncio.Future[Any]] = {}
        self.demo_artifacts: dict[str, Any] = {}
        self.subscribers: set[asyncio.Queue[Any]] = set()
        self.limits: dict[str, deque[float]] = defaultdict(deque)
        self.command_lock = asyncio.Lock()
        self.send_lock = asyncio.Lock()
        self.sender = Signer(runner_key, "runner") if runner_key else None
        self.receiver = Signer(runner_key, "control") if runner_key else None
        self.incident_alert_ids = incident_alert_ids or set
        for job in self.store.list("job", 10000):
            if job["status"] in ACTIVE:
                job.update(
                    status="interrupted", ended_at=now(), reason="control_restarted"
                )
                self.store.put("job", job["id"], job)
        self.store.prune(int(os.environ.get("LAB_RETENTION_DAYS", "14")))

    def runner_connected(self, ws: WebSocket) -> int:
        """Attach one runner and cancel any pending transient-disconnect expiry."""
        self.runner_generation += 1
        generation = self.runner_generation
        self.runner = ws
        task = self.runner_disconnect_task
        self.runner_disconnect_task = None
        if task:
            task.cancel()
        LOGGER.info("runner_connected generation=%s", generation)
        return generation

    def runner_disconnected(self, ws: WebSocket, generation: int, reason: str) -> None:
        """Allow a short reconnect without terminating otherwise healthy jobs."""
        if self.runner is not ws or self.runner_generation != generation:
            return
        self.runner = None
        self.runner_execute = False
        LOGGER.warning(
            "runner_connection_lost generation=%s reason=%s grace_seconds=%s",
            generation,
            reason,
            self.runner_reconnect_grace,
        )
        task = self.runner_disconnect_task
        if task:
            task.cancel()
        self.runner_disconnect_task = asyncio.create_task(
            self._expire_runner_disconnect(generation)
        )

    async def _expire_runner_disconnect(self, generation: int) -> None:
        try:
            await asyncio.sleep(self.runner_reconnect_grace)
            if self.runner is not None or self.runner_generation != generation:
                return
            LOGGER.error("runner_reconnect_grace_expired generation=%s", generation)
            for job in self.store.list("job", 10000):
                if job["status"] in ACTIVE:
                    await self.status(job["id"], "interrupted", "runner_disconnected")
            for future in self.pending.values():
                if not future.done():
                    future.set_result({"error": "runner_disconnected"})
        except asyncio.CancelledError:
            raise
        finally:
            if self.runner_disconnect_task is asyncio.current_task():
                self.runner_disconnect_task = None

    def rate(self, key: str, limit: int = 120) -> None:
        queue = self.limits[key]
        while queue and queue[0] < time.time() - 60:
            queue.popleft()
        if len(queue) >= limit:
            raise HTTPException(429, "rate_limited")
        queue.append(time.time())

    async def emit(
        self,
        job: dict[str, Any],
        source: str,
        event_type: str,
        payload: dict[str, Any],
        correlation: str | None = None,
    ) -> None:
        event = Event(
            correlation_id=correlation or job["id"],
            job_id=job["id"],
            source=source,
            type=event_type,
            severity=str(payload.get("severity", "info")),
            payload=sanitize({**payload, "mode": self.mode}),
        ).model_dump()
        self.store.put("event", event["event_id"], event)
        for queue in list(self.subscribers):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(event)

    async def status(
        self, identifier: str, status: str, reason: str | None = None
    ) -> None:
        job = self.store.get("job", identifier)
        if job is None or job["status"] not in ACTIVE:
            return
        job.update(status=status, updated_at=now())
        if reason:
            job["reason"] = reason
        if status not in ACTIVE:
            job["ended_at"] = now()
        self.store.put("job", identifier, job)
        await self.emit(
            job,
            ACTIONS[job["action"]]["agent"],
            f"job.{status}",
            {"status": status, "reason": reason},
        )

    def add_artifact(self, metadata: dict[str, Any], contents: dict[str, str]) -> None:
        self.demo_artifacts[metadata["id"]] = {
            "metadata": metadata,
            "contents": contents,
        }

    async def demo_job(self, job: dict[str, Any]) -> None:
        try:
            await run_demo(
                job,
                self.emit,
                self.status,
                self.add_artifact,
                # Keep the local demo observable: the canvas must have time to
                # show each transition and its active connection.
                float(os.environ.get("LAB_DEMO_DELAY", "1")),
            )
        except asyncio.CancelledError:
            await self.status(job["id"], "cancelled")
            raise
        except Exception:
            await self.status(job["id"], "failed", "demo_failed")

    async def send(self, payload: dict[str, Any]) -> None:
        if self.runner is None or self.sender is None:
            raise HTTPException(409, "runner_offline")
        async with self.send_lock:
            await self.runner.send_json(self.sender.sign(payload))

    async def rpc(self, payload: dict[str, Any]) -> Any:
        request_id = uid()
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        try:
            await self.send({**payload, "request_id": request_id})
            return await asyncio.wait_for(future, 15)
        except TimeoutError as exc:
            raise HTTPException(504, "runner_timeout") from exc
        finally:
            self.pending.pop(request_id, None)

    def snapshot(self) -> dict[str, Any]:
        online = self.runner is not None and time.time() - self.runner_seen < 20
        jobs = self.store.list("job")
        events = self.store.list("event")
        event_counts = self.store.event_counts()
        persisted_alert_ids = (
            self.incident_alert_ids() if self.mode == "integrated" else set()
        )
        persisted_incidents = len(persisted_alert_ids)
        visible_alerts = (
            persisted_incidents
            if self.mode == "integrated"
            else event_counts.get("alert.detected", 0)
        )
        visible_dba_records = (
            persisted_incidents
            if self.mode == "integrated"
            else event_counts.get("dba.recorded", 0)
        )
        return {
            "mode": self.mode,
            "runner": {
                "online": online if self.mode == "integrated" else True,
                "simulated": self.mode == "demo",
                "last_seen": self.runner_seen,
                "execute_enabled": self.runner_execute,
                "actions": self.runner_scope,
            },
            "jobs": jobs,
            "events": events,
            "metrics": {
                "alerts_detected": visible_alerts,
                "deliveries_confirmed": self.store.event_count_for_alerts(
                    "notification.sent", persisted_alert_ids
                )
                if self.mode == "integrated"
                else event_counts.get("notification.sent", 0),
                "delivery_failures": event_counts.get("notification.failed", 0),
                "dba_records": visible_dba_records,
            },
            "actions": ACTIONS,
            "artifacts": [v["metadata"] for v in self.demo_artifacts.values()]
            if self.mode == "demo"
            else self.runner_catalog,
            "contracts": ["health_check_alert.v1", "audit_security_alert.v1"],
            "timestamp": now(),
        }

    async def start(self, command: StartJob, user: dict[str, Any]) -> dict[str, Any]:
        async with self.command_lock:
            if command.action not in ACTIONS:
                raise HTTPException(422, "action_not_allowed")
            if user["role"] == "viewer":
                raise HTTPException(403, "operator_required")
            identifier = digest(f"{user['username']}:{command.request_id}")
            existing = self.store.get("job", identifier)
            if existing:
                if (
                    existing["action"] != command.action
                    or existing["execute"] != command.execute
                ):
                    raise HTTPException(409, "idempotency_conflict")
                return existing
            if command.execute and command.confirmation != "sakila":
                raise HTTPException(422, "confirm_sakila_required")
            if (
                command.execute
                and not user.get("local_access")
                and time.time() - user["reauth_at"] > 300
            ):
                raise HTTPException(403, "reauth_required")
            approval = None
            if command.action.startswith("dba.") and command.execute:
                approval = self.store.get("approval", command.approval_id or "")
                if (
                    user["role"] != "dba_approver"
                    or not approval
                    or approval["action"] != command.action
                    or approval["username"] != user["username"]
                    or approval["status"] != "approved"
                    or approval["expires"] < time.time()
                ):
                    raise HTTPException(403, "approved_proposal_required")
            active = [j for j in self.store.list("job", 10000) if j["status"] in ACTIVE]
            resources = set(ACTIONS[command.action]["resources"])
            if any(
                resources.intersection(ACTIONS[j["action"]]["resources"])
                for j in active
            ):
                raise HTTPException(409, "resource_busy")
            if self.mode == "integrated":
                if not self.snapshot()["runner"]["online"]:
                    raise HTTPException(409, "runner_offline")
                if (
                    command.action not in self.runner_scope
                    or command.execute
                    and not self.runner_execute
                ):
                    raise HTTPException(403, "runner_scope_denied")
            job = {
                "id": identifier,
                "action": command.action,
                "execute": command.execute,
                "status": "queued",
                "mode": self.mode,
                "created_at": now(),
                "updated_at": now(),
                "actor": user["username"],
            }
            if approval:
                approval["status"] = "consumed"
                self.store.put("approval", approval["id"], approval)
            self.store.put("job", identifier, job)
            await self.emit(
                job,
                "control",
                "lab.started",
                {"action": command.action, "actor": user["username"]},
            )
            if self.mode == "demo":
                self.tasks[identifier] = asyncio.create_task(self.demo_job(job))
            else:
                try:
                    await self.send({"type": "start", "job": job})
                except Exception:
                    await self.status(identifier, "failed", "runner_disconnected")
                    raise HTTPException(409, "runner_disconnected") from None
            return job

    async def stop(self, identifier: str) -> None:
        job = self.store.get("job", identifier)
        if not job:
            raise HTTPException(404, "job_not_found")
        if job["status"] not in ACTIVE:
            return
        await self.status(identifier, "stopping")
        if self.mode == "demo":
            task = self.tasks.get(identifier)
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            await self.status(identifier, "cancelled")
        else:
            await self.send({"type": "stop", "job_id": identifier})


def create_app(
    *,
    mode: str | None = None,
    database: str | None = None,
    users: dict[str, Any] | None = None,
    runner_key: str | None = None,
    incident_analyzer: TextAnalyzer | None = None,
) -> FastAPI:
    mode = mode or os.environ.get("LAB_MODE", "demo")
    if mode not in {"demo", "integrated"}:
        raise ValueError("invalid_mode")
    runtime = Path(os.environ.get("LAB_RUNTIME", "runtime"))
    runtime.mkdir(parents=True, exist_ok=True)
    repository = Path(
        os.environ.get("LAB_REPOSITORY_ROOT", Path(__file__).resolve().parents[4])
    )
    resources = ResourceStore(repository)
    incidents = IncidentAnalysis(repository, incident_analyzer)
    refactor_deliveries = RefactorDeliveries(repository)
    if users is None:
        users = json.loads(os.environ.get("LAB_USERS_JSON", "{}"))
        if not users:
            users = {}
    key = runner_key if runner_key is not None else os.environ.get("LAB_RUNNER_KEY", "")
    for settings in users.values():
        if settings.get("role") not in {
            "viewer",
            "operator",
            "dba_approver",
        } or not str(settings.get("hash", "")).startswith("pbkdf2$"):
            raise ValueError("invalid_user_configuration")
    if mode == "integrated" and len(key) < 32:
        raise ValueError("integrated_requires_runner_key")
    origin = os.environ.get("LAB_ORIGIN", "http://localhost:3000")
    local_hosts = {"localhost", "127.0.0.1", "::1"}
    if mode == "integrated" and urlparse(origin).hostname not in local_hosts:
        raise ValueError("passwordless_integrated_requires_local_origin")
    control = Control(
        mode,
        database
        or os.environ.get("LAB_DATABASE_URL", f"sqlite:///{runtime / 'console.db'}"),
        users,
        key,
        origin,
        incidents.alert_ids,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        incident_task: asyncio.Task[Any] | None = None
        if control.mode == "integrated":
            known_incidents = await asyncio.to_thread(incidents.keys)
            incident_task = asyncio.create_task(
                watch_new_incidents(
                    incidents,
                    known_incidents,
                    float(os.environ.get("LAB_INCIDENT_POLL_SECONDS", "3")),
                )
            )
        try:
            yield
        finally:
            if incident_task:
                incident_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await incident_task
            for identifier in list(control.tasks):
                await control.stop(identifier)
            if control.runner_disconnect_task:
                control.runner_disconnect_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await control.runner_disconnect_task
            if control.runner:
                await control.runner.close()

    app = FastAPI(
        title="Agent Monitoring DB Lab Console", version="0.1.0", lifespan=lifespan
    )
    app.state.control = control
    app.state.incidents = incidents

    @app.middleware("http")
    async def guard(request: Request, call_next: Any) -> Response:
        if control.mode == "integrated":
            hostname = urlparse(
                "//"
                + request.headers.get(
                    "x-forwarded-host", request.headers.get("host", "")
                )
            ).hostname
            if (
                not request.client
                or request.client.host not in {"127.0.0.1", "::1"}
                or hostname not in local_hosts
            ):
                return JSONResponse({"detail": "local_access_required"}, 403)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            if request.headers.get("origin") != control.origin:
                return JSONResponse({"detail": "origin_denied"}, 403)
        limit = 1_000_000 if request.url.path.startswith("/api/resources/") else 16000
        if int(request.headers.get("content-length", "0")) > limit:
            return JSONResponse({"detail": "request_too_large"}, 413)
        response = await call_next(request)
        response.headers.update(
            {
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "Cache-Control": "no-store",
                "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
            }
        )
        return response

    def session(request: Request) -> dict[str, Any]:
        token = request.cookies.get("lab_session", "")
        value = control.sessions.get(digest(token))
        if not value or value["expires"] < time.time():
            raise HTTPException(401, "session_expired")
        if request.method not in {"GET", "HEAD"} and not secrets.compare_digest(
            request.headers.get("x-csrf-token", ""), value["csrf"]
        ):
            control.rate(value["username"])
            raise HTTPException(403, "csrf_denied")
        if request.method not in {"GET", "HEAD"}:
            control.rate(value["username"])
        return value

    def renew_local_access(request: Request) -> tuple[dict[str, Any], str]:
        """Issue a new local-only console session."""
        token = secrets.token_urlsafe(32)
        user = {
            "username": "local-dba",
            "role": "dba_approver",
            "csrf": secrets.token_urlsafe(32),
            "local_access": True,
            "expires": time.time() + 1800,
            "reauth_at": time.time(),
        }
        control.sessions[digest(token)] = user
        return user, token

    def set_local_session_cookie(response: Response, token: str) -> None:
        response.set_cookie(
            "lab_session",
            token,
            httponly=True,
            secure=control.origin.startswith("https"),
            samesite="strict",
            max_age=1800,
            path="/api",
        )

    def report_access(request: Request) -> tuple[dict[str, Any], str | None]:
        """Authorize a direct report without replacing the console session.

        A report can stay open longer than the console's normal local session.
        Its recovery token is deliberately isolated so opening ``latest.html``
        never invalidates the CSRF token currently held by the console.
        """
        try:
            return session(request), None
        except HTTPException as error:
            if error.status_code != 401 or error.detail != "session_expired":
                raise
        token = request.cookies.get("lab_report_access", "")
        value = control.sessions.get(digest(token))
        if value and value.get("report_access") and value["expires"] > time.time():
            return value, None
        user, token = renew_local_access(request)
        user["report_access"] = True
        return user, token

    def set_report_access_cookie(response: Response, token: str) -> None:
        response.set_cookie(
            "lab_report_access",
            token,
            httponly=True,
            secure=control.origin.startswith("https"),
            samesite="strict",
            max_age=1800,
            path="/api/artifacts/current",
        )

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "mode": control.mode}

    @app.post("/api/access")
    async def access(request: Request) -> Response:
        # Explicitly authorized single-operator local access. This is not
        # identity authentication; cookie + CSRF still protect browser actions.
        control.rate(
            f"access:{request.client.host if request.client else 'unknown'}", 30
        )
        old = control.sessions.get(digest(request.cookies.get("lab_session", "")))
        token = None
        if old and old.get("local_access") and old["expires"] > time.time():
            user = old
        else:
            if old:
                old["expires"] = 0
            user, token = renew_local_access(request)
        if token is None:
            user.update(expires=time.time() + 1800, reauth_at=time.time())
        response = JSONResponse({k: user[k] for k in ("username", "role", "csrf")})
        set_local_session_cookie(response, token or request.cookies["lab_session"])
        return response

    @app.post("/api/login")
    async def login(body: Login, request: Request) -> Response:
        control.rate(
            f"login:{request.client.host if request.client else 'unknown'}", 10
        )
        user = control.users.get(body.username)
        if not user or not verify_password(body.password, user["hash"]):
            raise HTTPException(401, "invalid_credentials")
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        control.sessions[digest(token)] = {
            "username": body.username,
            "role": user["role"],
            "expires": time.time() + 1800,
            "reauth_at": time.time(),
            "csrf": csrf,
        }
        response = JSONResponse(
            {
                "username": body.username,
                "role": user["role"],
                "csrf": csrf,
                "mode": control.mode,
            }
        )
        response.set_cookie(
            "lab_session",
            token,
            httponly=True,
            secure=control.origin.startswith("https"),
            samesite="strict",
            max_age=1800,
            path="/api",
        )
        return response

    @app.get("/api/session")
    async def me(user: dict[str, Any] = Depends(session)) -> dict[str, Any]:
        return {k: user[k] for k in ("username", "role", "csrf")}

    @app.post("/api/logout")
    async def logout(
        request: Request, user: dict[str, Any] = Depends(session)
    ) -> Response:
        user["expires"] = 0
        control.sessions.pop(digest(request.cookies.get("lab_session", "")), None)
        response = JSONResponse({"status": "signed_out"})
        response.delete_cookie("lab_session", path="/api")
        return response

    @app.post("/api/reauth")
    async def reauth(
        body: ApprovalRequest, user: dict[str, Any] = Depends(session)
    ) -> dict[str, str]:
        control.rate(f"reauth:{user['username']}", 5)
        if not verify_password(body.password, control.users[user["username"]]["hash"]):
            raise HTTPException(403, "invalid_credentials")
        user["reauth_at"] = time.time()
        return {"status": "authenticated"}

    @app.get("/api/state")
    async def state(user: dict[str, Any] = Depends(session)) -> dict[str, Any]:
        return control.snapshot()

    @app.get("/api/resources/{agent}/{resource}")
    async def resource_view(
        agent: str,
        resource: str,
        path: str = "",
        user: dict[str, Any] = Depends(session),
    ) -> dict[str, Any]:
        try:
            return resources.view(agent, resource, path)
        except ValueError as error:
            raise HTTPException(404, str(error)) from error

    @app.put("/api/resources/{agent}/{resource}")
    async def resource_write(
        agent: str,
        resource: str,
        body: ResourceWrite,
        user: dict[str, Any] = Depends(session),
    ) -> dict[str, str]:
        try:
            return resources.write(agent, resource, body.path, body.content)
        except PermissionError as error:
            raise HTTPException(403, str(error)) from error
        except ValueError as error:
            raise HTTPException(404, str(error)) from error

    @app.get("/api/events")
    async def events(
        request: Request, user: dict[str, Any] = Depends(session)
    ) -> StreamingResponse:
        async def stream():
            queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=100)
            control.subscribers.add(queue)
            try:
                yield "event: connected\ndata: {}\n\n"
                while (
                    time.time() < user["expires"]
                    and not await request.is_disconnected()
                ):
                    try:
                        item = await asyncio.wait_for(queue.get(), 10)
                        if time.time() >= user["expires"]:
                            break
                        yield f"id: {item['event_id']}\ndata: {json.dumps(item)}\n\n"
                    except TimeoutError:
                        yield ": heartbeat\n\n"
            finally:
                control.subscribers.discard(queue)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"X-Accel-Buffering": "no"},
        )

    @app.post("/api/jobs")
    async def start(
        body: StartJob, user: dict[str, Any] = Depends(session)
    ) -> dict[str, Any]:
        return await control.start(body, user)

    @app.post("/api/jobs/{identifier}/stop")
    async def stop(
        identifier: str, user: dict[str, Any] = Depends(session)
    ) -> dict[str, str]:
        if user["role"] == "viewer":
            raise HTTPException(403, "operator_required")
        await control.stop(identifier)
        return {"status": "stopping"}

    @app.post("/api/emergency-stop")
    async def stop_all(user: dict[str, Any] = Depends(session)) -> dict[str, str]:
        if user["role"] == "viewer":
            raise HTTPException(403, "operator_required")
        for job in control.store.list("job", 10000):
            if job["status"] in ACTIVE:
                await control.stop(job["id"])
        return {"status": "stopping"}

    @app.get("/api/artifacts")
    async def artifacts(
        user: dict[str, Any] = Depends(session),
    ) -> list[dict[str, Any]]:
        if control.mode == "integrated":
            result = await control.rpc({"type": "catalog"})
            control.runner_catalog = result.get("items", [])
        return list(control.snapshot()["artifacts"])

    @app.get("/api/artifacts/current/{location}/html")
    async def current_artifact_html(
        location: str, request: Request, live: bool = False
    ) -> Response:
        user, report_token = report_access(request)
        if (
            location not in {"health", "latency", "audit"}
            or control.mode != "integrated"
        ):
            raise HTTPException(404, "latest_artifact_unavailable")
        current_json = {
            "health": "report.json",
            "latency": "latest.json",
            "audit": "latest.json",
        }[location]

        def read_current() -> tuple[dict[str, Any], str]:
            # Current reports are local, allowlisted, read-only artifacts. Keep
            # their visualization independent from the operational runner so a
            # busy/reconnecting runner cannot make an already published report
            # disappear. A fresh Catalog per attempt also avoids shared indexes
            # across concurrent browser refreshes.
            for _ in range(3):
                local_catalog = Catalog(repository)
                items = local_catalog.list()
                artifact = next(
                    (
                        item
                        for item in items
                        if item.get("location") == location
                        and item.get("name") == f"{location} · {current_json}"
                        and item.get("html_available")
                    ),
                    None,
                )
                if artifact is None:
                    raise ValueError("latest_artifact_unavailable")
                try:
                    content = local_catalog.read(
                        artifact["id"], "html", artifact["sha256"]
                    )
                except ValueError as error:
                    if str(error) == "artifact_replaced":
                        continue
                    raise
                return artifact, content
            raise ValueError("artifact_replaced")

        try:
            artifact, content = await asyncio.to_thread(read_current)
        except (OSError, ValueError) as error:
            detail = str(error)
            status = 404 if detail == "latest_artifact_unavailable" else 409
            raise HTTPException(status, detail) from error
        await control.emit(
            {"id": "artifact-view"},
            "dba",
            "artifact.viewed",
            {"artifact_id": artifact["id"], "actor": user["username"]},
        )
        response = Response(
            content,
            media_type="text/html",
            headers={
                "Content-Security-Policy": HTML_CSP,
                "Content-Disposition": 'inline; filename="latest.html"',
                "Cache-Control": "no-store",
            },
        )
        if report_token is not None:
            set_report_access_cookie(response, report_token)
        return response

    @app.get("/api/artifacts/{identifier}/{format}")
    async def artifact_content(
        identifier: str, format: str, sha: str, user: dict[str, Any] = Depends(session)
    ) -> Response:
        if format not in {"html", "json"}:
            raise HTTPException(422, "invalid_format")
        if control.mode == "demo":
            record = control.demo_artifacts.get(identifier)
            if not record or record["metadata"]["sha256"] != sha:
                raise HTTPException(404, "artifact_unavailable")
            content = record["contents"][format]
        else:
            result = await control.rpc(
                {"type": "artifact", "id": identifier, "format": format, "sha": sha}
            )
            if result.get("error"):
                raise HTTPException(409, result["error"])
            content = result["content"]
        await control.emit(
            {"id": "artifact-view"},
            "dba",
            "artifact.viewed",
            {"artifact_id": identifier, "actor": user["username"]},
        )
        return Response(
            content,
            media_type="text/html" if format == "html" else "application/json",
            headers={
                "Content-Security-Policy": HTML_CSP,
                "Content-Disposition": f'inline; filename="report.{format}"',
            },
        )

    @app.get("/api/dba/incidents")
    async def dba_incidents(
        user: dict[str, Any] = Depends(session),
    ) -> list[dict[str, Any]]:
        if control.mode != "integrated":
            return []
        return await asyncio.to_thread(incidents.list)

    @app.get("/api/dba/refactor-deliveries")
    async def dba_refactor_deliveries(
        user: dict[str, Any] = Depends(session),
    ) -> list[dict[str, Any]]:
        if control.mode != "integrated":
            return []
        return await asyncio.to_thread(refactor_deliveries.list)

    @app.delete("/api/dba/refactor-deliveries/{delivery_id}")
    async def delete_dba_refactor_delivery(
        delivery_id: str,
        user: dict[str, Any] = Depends(session),
    ) -> dict[str, Any]:
        if control.mode != "integrated":
            raise HTTPException(404, "refactor_delivery_not_available_in_demo")
        if user["role"] != "dba_approver":
            raise HTTPException(403, "dba_approver_required")
        try:
            return await asyncio.to_thread(refactor_deliveries.delete, delivery_id)
        except ValueError as error:
            status = 404 if str(error) == "refactor_delivery_not_found" else 409
            raise HTTPException(status, str(error)) from error

    @app.get("/api/dba/incidents/{source}/{record_id}")
    async def dba_incident(
        source: str,
        record_id: str,
        user: dict[str, Any] = Depends(session),
    ) -> dict[str, Any]:
        if control.mode != "integrated":
            raise HTTPException(404, "incident_not_available_in_demo")
        try:
            return await asyncio.to_thread(incidents.detail, source, record_id)
        except IncidentAnalysisError as error:
            raise HTTPException(404, str(error)) from error

    @app.delete("/api/dba/incidents/{source}/{record_id}")
    async def delete_dba_incident(
        source: str,
        record_id: str,
        user: dict[str, Any] = Depends(session),
    ) -> dict[str, Any]:
        if control.mode != "integrated":
            raise HTTPException(404, "incident_not_available_in_demo")
        if user["role"] != "dba_approver":
            raise HTTPException(403, "dba_approver_required")
        try:
            result = await asyncio.to_thread(incidents.delete, source, record_id)
            control.store.remove("incident_chat", f"{source}:{record_id}")
            return result
        except IncidentAnalysisError as error:
            status = 404 if str(error) == "incident_not_found" else 409
            raise HTTPException(status, str(error)) from error

    @app.get("/api/dba/incidents/{source}/{record_id}/evidence/{filename}")
    async def dba_incident_evidence(
        source: str,
        record_id: str,
        filename: str,
        user: dict[str, Any] = Depends(session),
    ) -> Response:
        if control.mode != "integrated":
            raise HTTPException(404, "incident_not_available_in_demo")
        try:
            content = await asyncio.to_thread(
                incidents.evidence, source, record_id, filename
            )
        except IncidentAnalysisError as error:
            raise HTTPException(404, str(error)) from error
        is_html = filename.endswith(".html")
        return Response(
            content,
            media_type="text/html" if is_html else "application/json",
            headers={
                "Content-Security-Policy": HTML_CSP
                if is_html
                else "default-src 'none'",
                "Content-Disposition": f'inline; filename="{filename}"',
            },
        )

    @app.post("/api/dba/incidents/{source}/{record_id}/question")
    async def dba_incident_question(
        source: str,
        record_id: str,
        body: IncidentQuestion,
        user: dict[str, Any] = Depends(session),
    ) -> dict[str, Any]:
        if control.mode != "integrated":
            raise HTTPException(404, "incident_not_available_in_demo")
        control.rate(f"incident-question:{user['username']}", 10)
        chat_id = f"{source}:{record_id}"
        stored = control.store.get("incident_chat", chat_id) or {}
        history = stored.get("messages", [])
        if not isinstance(history, list):
            history = []
        try:
            answer = await asyncio.to_thread(
                incidents.answer,
                source,
                record_id,
                body.message,
                history[-12:],
            )
        except IncidentAnalysisError as error:
            raise HTTPException(409, str(error)) from error
        messages = [
            *history,
            {"role": "user", "text": sanitize_chat_text(body.message)},
            {"role": "assistant", "text": answer.strip()},
        ][-80:]
        control.store.put(
            "incident_chat",
            chat_id,
            {
                "source": source,
                "record_id": record_id,
                "updated_at": now(),
                "messages": messages,
            },
        )
        return {"answer": answer, "messages": messages}

    @app.get("/api/dba/incidents/{source}/{record_id}/conversation")
    async def dba_incident_conversation(
        source: str,
        record_id: str,
        user: dict[str, Any] = Depends(session),
    ) -> dict[str, Any]:
        if control.mode != "integrated":
            raise HTTPException(404, "incident_not_available_in_demo")
        try:
            await asyncio.to_thread(incidents.detail, source, record_id)
        except IncidentAnalysisError as error:
            raise HTTPException(404, str(error)) from error
        stored = control.store.get("incident_chat", f"{source}:{record_id}") or {}
        messages = stored.get("messages", [])
        return {"messages": messages if isinstance(messages, list) else []}

    @app.post("/api/chat")
    async def chat(
        body: ChatRequest, user: dict[str, Any] = Depends(session)
    ) -> dict[str, Any]:
        message = body.message.lower()
        if body.intent == "propose":
            if user["role"] != "dba_approver":
                raise HTTPException(403, "dba_approver_required")
            action = (
                "dba.actor_count"
                if message.strip() in {"contar atores", "actor_count", "quantos atores"}
                else "dba.film_count"
                if message.strip() in {"contar filmes", "film_count", "quantos filmes"}
                else None
            )
            if action is None:
                return {
                    "answer": "O catálogo permite ‘contar atores’ ou ‘contar filmes’ em sakila. SQL livre e mudanças no banco estão bloqueados.",
                    "citations": [],
                }
            proposal = {
                "id": uid(),
                "action": action,
                "username": user["username"],
                "status": "pending",
                "expires": time.time() + 300,
                "sql": ACTIONS[action]["sql"],
                "schema": "sakila",
                "timeout_seconds": 5,
                "row_limit": 1,
                "byte_limit": 4096,
                "impact": "Leitura agregada; sem alteração de dados.",
                "rollback": "Não aplicável: somente leitura.",
                "profile": "login-path configurado localmente no runner",
            }
            control.store.put("approval", proposal["id"], proposal)
            await control.emit(
                {"id": proposal["id"]},
                "dba",
                "approval.requested",
                {"approval_id": proposal["id"], "sql_hash": digest(proposal["sql"])},
            )
            return {
                "answer": "Plano preparado. Confira o SQL e confirme a execução abaixo.",
                "proposal": proposal,
                "citations": [],
            }
        snapshot = control.snapshot()
        if any(w in message for w in ("email", "e-mail", "alerta", "inbox")):
            sent = sum(e["type"] == "notification.sent" for e in snapshot["events"])
            recorded = sum(e["type"] == "dba.recorded" for e in snapshot["events"])
            answer = (
                f"Na janela de eventos disponível: {sent} envios confirmados e {recorded} registros DBA. "
                + (
                    "São eventos fictícios do modo demo."
                    if control.mode == "demo"
                    else "Confirmação SMTP não comprova leitura humana."
                )
            )
        elif any(w in message for w in ("regra", "recorr", "cooldown")):
            answer = "Health: o Luna lê o HTML e decide alerta para P99 > 2,0 segundos; cooldown crítico padrão de 120 s. Audit: uma publicação por evento; novas tentativas de DROP/ALTER são independentes. Notification não agenda recorrência."
        else:
            active = sum(j["status"] in ACTIVE for j in snapshot["jobs"])
            answer = f"Há {active} jobs ativos. O runner está {'conectado' if snapshot['runner']['online'] else 'desconectado'}. O fluxo é agentes → MCP → Notification e inbox do DBA. Consulte os relatórios para evidência; este assistente responde a partir do estado observado e do catálogo, sem inferir o estado do banco."
        return {
            "answer": answer,
            "citations": [
                {"label": a["name"], "artifact_id": a["id"]}
                for a in snapshot["artifacts"][:3]
            ],
        }

    @app.post("/api/approvals/{identifier}")
    async def approve(
        identifier: str, body: ApprovalRequest, user: dict[str, Any] = Depends(session)
    ) -> dict[str, Any]:
        control.rate(f"approval:{user['username']}", 5)
        proposal = control.store.get("approval", identifier)
        if (
            user["role"] != "dba_approver"
            or not proposal
            or proposal["username"] != user["username"]
        ):
            raise HTTPException(403, "proposal_denied")
        if proposal["status"] != "pending" or proposal["expires"] < time.time():
            raise HTTPException(409, "proposal_expired_or_consumed")
        if body.confirmation != "sakila" or (
            not user.get("local_access")
            and not verify_password(
                body.password, control.users[user["username"]]["hash"]
            )
        ):
            raise HTTPException(403, "approval_confirmation_failed")
        user["reauth_at"] = time.time()
        proposal["status"] = "approved"
        control.store.put("approval", identifier, proposal)
        await control.emit(
            {"id": identifier},
            "dba",
            "approval.approved",
            {"approval_id": identifier, "actor": user["username"]},
        )
        return await control.start(
            StartJob(
                action=proposal["action"],
                execute=True,
                confirmation="sakila",
                request_id=identifier,
                approval_id=identifier,
            ),
            user,
        )

    @app.websocket("/runner")
    async def runner(ws: WebSocket) -> None:
        if (
            control.mode != "integrated"
            or control.runner is not None
            or not secrets.compare_digest(
                ws.headers.get("authorization", ""), f"Bearer {control.runner_key}"
            )
        ):
            await ws.close(code=1008)
            return
        await ws.accept()
        generation = control.runner_connected(ws)
        receiver = control.receiver
        assert receiver is not None
        disconnect_reason = "websocket_closed"
        try:
            while True:
                envelope = await asyncio.wait_for(ws.receive_json(), 20)
                payload = receiver.verify(envelope)
                control.runner_seen = time.time()
                if payload["type"] == "heartbeat":
                    control.runner_execute = payload.get("execute_enabled") is True
                    control.runner_scope = [
                        a for a in payload.get("actions", []) if a in ACTIONS
                    ]
                elif payload["type"] == "status":
                    if payload["status"] not in ACTIVE | {
                        "succeeded",
                        "failed",
                        "cancelled",
                        "interrupted",
                        "timed_out",
                    }:
                        raise ValueError("invalid_status")
                    await control.status(
                        payload["job_id"],
                        payload["status"],
                        sanitize({"reason": payload.get("reason")}).get("reason"),
                    )
                elif payload["type"] == "event":
                    event = Event.model_validate(payload["event"])
                    if not control.store.get("job", event.job_id):
                        raise ValueError("unknown_job")
                    await control.emit(
                        {"id": event.job_id},
                        event.source,
                        event.type,
                        event.payload,
                        event.correlation_id,
                    )
                elif payload["type"] == "result":
                    future = control.pending.get(payload.get("request_id", ""))
                    if future and not future.done():
                        future.set_result(payload["result"])
        except WebSocketDisconnect as error:
            disconnect_reason = f"websocket_disconnect_{error.code}"
        except TimeoutError:
            disconnect_reason = "heartbeat_timeout"
        except (ValueError, KeyError) as error:
            detail = str(error)
            disconnect_reason = (
                detail
                if detail
                in {
                    "bad_signature",
                    "expired_envelope",
                    "replayed_envelope",
                    "invalid_status",
                    "unknown_job",
                }
                else f"protocol_{type(error).__name__}"
            )
        except Exception as error:
            disconnect_reason = f"unexpected_{type(error).__name__}"
        finally:
            control.runner_disconnected(ws, generation, disconnect_reason)
            with contextlib.suppress(Exception):
                await ws.close()

    return app
