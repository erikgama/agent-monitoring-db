import argparse
import asyncio
import contextlib
import json
import logging
import os
import signal
import sys
import time
from collections import deque
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import psutil
from agent_monitoring.config import database_settings
from websockets.asyncio.client import connect

from .artifacts import Catalog
from .models import ACTIONS, Event
from .security import Signer, digest, sanitize

LOGGER = logging.getLogger("labconsole.runner")


class ReliableOutbox:
    """Keep runner messages ordered until a live control socket accepts them."""

    def __init__(self) -> None:
        self.items: deque[dict[str, Any]] = deque()
        self.changed = asyncio.Condition()

    async def send(self, payload: dict[str, Any]) -> None:
        async with self.changed:
            self.items.append(payload)
            self.changed.notify()

    async def relay(self, socket: Any, sender: Signer, lock: asyncio.Lock) -> None:
        while True:
            async with self.changed:
                await self.changed.wait_for(lambda: bool(self.items))
                payload = self.items[0]
            envelope = json.dumps(sender.sign(payload))
            async with lock:
                await socket.send(envelope)
            async with self.changed:
                if self.items and self.items[0] is payload:
                    self.items.popleft()

    async def clear(self) -> None:
        async with self.changed:
            self.items.clear()


def command_for(repository: Path, action: str, execute: bool) -> tuple[list[str], Path]:
    if action not in ACTIONS or action == "lab.three":
        raise ValueError("action_not_allowlisted")
    spec = ACTIONS[action]
    if action == "health.lab":
        return [
            sys.executable,
            str(
                repository
                / "apps"
                / "lab-console"
                / "scripts"
                / "run-health-check-lab.py"
            ),
            "--monitor-only",
            *(["--execute"] if execute else []),
        ], repository
    if action == "health.load":
        if not execute:
            raise ValueError("health_load_requires_execution")
        return [
            sys.executable,
            str(
                repository
                / "apps"
                / "lab-console"
                / "scripts"
                / "run-health-select-simulation.py"
            ),
            "--execute",
            "--confirm-target",
            "sakila",
            "--confirm-demo",
            "HEALTH_SELECT_SIMULATION",
        ], repository
    if "script" in spec:
        return [
            sys.executable,
            str(repository / "apps" / "lab-console" / "scripts" / spec["script"]),
            *(["--execute"] if execute else []),
        ], repository
    if action == "health.collect":
        root = repository / "agents/health-check"
        return [
            str(root / ".venv/bin/mysql-health-check"),
            "collect" if execute else "read-latest",
        ], root
    if action == "notification.test":
        return [
            sys.executable,
            "-m",
            "labconsole.notification_probe",
            "--repository",
            str(repository),
            *(["--execute"] if execute else []),
        ], repository
    if action.startswith("dba."):
        if not execute:
            raise ValueError("query_requires_approval")
        settings = database_settings("dba")
        if not settings.login_file.is_file():
            raise ValueError("approved_login_file_not_found")
        profile = settings.login_path
        if not profile or not profile.replace("-", "").replace("_", "").isalnum():
            raise ValueError("readonly_login_path_required")
        return [
            settings.mysql_binary,
            f"--login-path={profile}",
            *settings.tls_flags(),
            "--database=sakila",
            "--connect-timeout=5",
            "--batch",
            "--skip-column-names",
            "--execute",
            spec["sql"],
        ], repository
    raise ValueError("unsupported_action")


def child_environment(repository: Path, runtime: Path) -> dict[str, str]:
    # Only references needed by official executors; no control-plane secrets.
    keys = {
        "PATH",
        "HOME",
        "USER",
        "LANG",
        "LC_ALL",
        "TMPDIR",
        "AGENT_MONITORING_CONFIG",
        "AGENT_MONITORING_LLM_PROVIDER",
    }
    env = {k: v for k, v in os.environ.items() if k in keys}
    env.update(
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONUNBUFFERED="1",
        UV_CACHE_DIR=str(runtime / "uv-cache"),
        HEALTHCHECK_ALERTING_ENABLED="false",
        AUDIT_SECURITY_ALERTING_ENABLED="false",
        MCP_NOTIFICATION_ENABLED="false",
        MCP_DBA_ENABLED="false",
        NOTIFICATION_DELIVERY_ENABLED="false",
        NOTIFICATION_EMAIL_FROM="lab@example.invalid",
        NOTIFICATION_EMAIL_RECIPIENTS_WARNING="demo@example.invalid",
        NOTIFICATION_EMAIL_RECIPIENTS_CRITICAL="demo@example.invalid",
    )
    env = database_settings("dba").environment(env)
    return env


class ProcessTree:
    """Track descendants by PID plus creation time, including child sessions."""

    def __init__(self, pid: int):
        # A very short child may already have been reaped by asyncio on Linux.
        try:
            self.root: psutil.Process | None = psutil.Process(pid)
        except psutil.NoSuchProcess:
            self.root = None
        self.known: dict[int, psutil.Process] = (
            {pid: self.root} if self.root is not None else {}
        )
        self.capture_restricted = False
        self.process_group: int | None = None
        with contextlib.suppress(ProcessLookupError, PermissionError):
            self.process_group = os.getpgid(pid)

    def capture(self) -> None:
        for process in list(self.known.values()):
            try:
                for child in process.children(recursive=True):
                    self.known[child.pid] = child
            except (psutil.NoSuchProcess, psutil.AccessDenied, PermissionError):
                self.capture_restricted = True

    async def capture_async(self, lock: asyncio.Lock | None = None) -> None:
        """Enumerate descendants off the runner event loop.

        On macOS, ``psutil.Process.children(recursive=True)`` may scan the full
        process table. Running several scans synchronously used to starve the
        WebSocket heartbeat when multiple agents were active.
        """

        async def run_capture() -> None:
            operation = asyncio.create_task(asyncio.to_thread(self.capture))
            try:
                await asyncio.shield(operation)
            except asyncio.CancelledError:
                # ``to_thread`` keeps running after its awaiting task is
                # cancelled. Wait for that scan before releasing the shared
                # lock so cleanup never races a still-mutating ``known`` map.
                with contextlib.suppress(asyncio.CancelledError):
                    await operation
                raise

        if lock is None:
            await run_capture()
            return
        async with lock:
            await run_capture()

    def alive(self) -> bool:
        for process in self.known.values():
            with contextlib.suppress(
                psutil.NoSuchProcess, psutil.AccessDenied, PermissionError
            ):
                if process.is_running() and process.status() != psutil.STATUS_ZOMBIE:
                    return True
        return False

    async def stop(self, capture_lock: asyncio.Lock | None = None) -> None:
        await self.capture_async(capture_lock)
        for sig, pause in (
            (signal.SIGINT, 1),
            (signal.SIGTERM, 1),
            (signal.SIGKILL, 0.5),
        ):
            if self.process_group is not None:
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.killpg(self.process_group, sig)
            for process in reversed(list(self.known.values())):
                with contextlib.suppress(
                    psutil.NoSuchProcess, psutil.AccessDenied, PermissionError
                ):
                    process.send_signal(sig)
            await asyncio.sleep(pause)
            await self.capture_async(capture_lock)
            if not self.alive():
                return
        if self.alive():
            raise RuntimeError("process_cleanup_failed")


def parse_line(
    line: str, source: str
) -> list[tuple[str, str, dict[str, Any], str | None]]:
    """Convert only recognized structured fields; never forward raw stdout."""
    result: list[tuple[str, str, dict[str, Any], str | None]] = []
    if "unsafe_unexpected_success" in line:
        return [
            (
                source,
                "audit.attempt.unsafe_success",
                {"error_code": "unsafe_unexpected_success"},
                None,
            )
        ]
    if "expected_permission_denied" in line:
        return [
            (
                source,
                "audit.attempt.denied",
                {"status": "expected_permission_denied"},
                None,
            )
        ]
    if "audit-advisor está pronto" in line or "health-monitor está pronto" in line:
        result.append((source, "agent.ready", {"status": "ready"}, None))
    try:
        data = json.loads(line[line.index("{") :])
        if not isinstance(data, dict):
            return result
    except (ValueError, TypeError):
        return result
    if data.get("status"):
        result.append(
            (
                source,
                "lab.progress",
                sanitize({"status": data["status"], "audit_id": data.get("audit_id")}),
                None,
            )
        )
    if source == "notification" and data.get("status") in {
        "sent",
        "failed",
        "dry_run",
        "skipped",
    }:
        status = data["status"]
        result.append(
            (
                source,
                f"notification.{status}",
                sanitize({**data, "delivery_status": status}),
                None,
            )
        )
    outcomes = data.get("publication_outcomes", data.get("publications", [])) or []
    if not isinstance(outcomes, list):
        return result
    for item in outcomes:
        if not isinstance(item, dict):
            continue
        dedupe_key = str(item.get("dedupe_key") or "")
        category = item.get("category")
        if not category:
            if ":schema_change:" in dedupe_key or "blocked-alter" in dedupe_key:
                category = "schema_change"
            elif ":destructive_ddl:" in dedupe_key or "blocked-drop" in dedupe_key:
                category = "destructive_ddl"
            else:
                category = "unknown"
        correlation = item.get("alert_id") or digest(
            str(dedupe_key or data.get("audit_id") or "unknown")
        )
        payload = {
            **sanitize(item),
            "category": category,
            "audit_id": data.get("audit_id") or data.get("source_audit_id"),
        }
        if item.get("decision") == "cooldown_active" or item.get("status") in {
            "duplicate",
            "historical",
        }:
            result.append(
                (
                    source,
                    "alert.suppressed",
                    {**payload, "decision": item.get("decision", item.get("status"))},
                    correlation,
                )
            )
            continue
        result.append((source, "alert.detected", payload, correlation))
        publication = item.get("publication", item)
        if publication.get("accepted") is not None:
            result.append(
                (
                    "mcp",
                    "mcp.validated" if publication.get("accepted") else "mcp.rejected",
                    {**payload, **sanitize(publication)},
                    correlation,
                )
            )
        if publication.get("delivery_status"):
            status = publication["delivery_status"]
            result.append(
                (
                    "notification",
                    "notification.sent"
                    if status == "sent"
                    else "notification.failed"
                    if status == "failed"
                    else "notification.skipped",
                    {**payload, "delivery_status": status},
                    correlation,
                )
            )
        if publication.get("dba_status"):
            status = publication["dba_status"]
            result.append(
                (
                    "dba",
                    "dba.recorded"
                    if status in {"recorded", "duplicate"}
                    else "dba.failed",
                    {**payload, "dba_status": status},
                    correlation,
                )
            )
    return result


class Runner:
    def __init__(
        self,
        repository: Path,
        runtime: Path,
        send: Any,
        allow_execute: bool = False,
        scope: set[str] | None = None,
    ):
        self.repository, self.runtime, self.send = (
            repository.resolve(),
            runtime.resolve(),
            send,
        )
        self.allow_execute = allow_execute
        self.scope = scope if scope is not None else set(ACTIONS)
        self.runtime.mkdir(parents=True, exist_ok=True)
        self.catalog = Catalog(self.repository)
        self.jobs: dict[str, asyncio.Task[Any]] = {}
        self.job_specs: dict[str, dict[str, Any]] = {}
        self.seen: set[str] = set()
        self.health_ready = asyncio.Event()
        self.audit_ready = asyncio.Event()
        self.process_scan_lock = asyncio.Lock()

    def has_active_jobs(self) -> bool:
        return any(not task.done() for task in self.jobs.values())

    async def emit(
        self,
        job: dict[str, Any],
        source: str,
        event: str,
        payload: dict[str, Any],
        correlation: str | None = None,
    ) -> None:
        await self.send(
            {
                "type": "event",
                "event": Event(
                    job_id=job["id"],
                    correlation_id=correlation or job["id"],
                    source=source,
                    type=event,
                    payload=sanitize(payload),
                ).model_dump(),
            }
        )

    async def status(
        self, job: dict[str, Any], status: str, reason: str | None = None
    ) -> None:
        await self.send(
            {"type": "status", "job_id": job["id"], "status": status, "reason": reason}
        )

    async def execute(
        self, job: dict[str, Any], action: str, listener: Any = None
    ) -> int:
        argv, cwd = command_for(self.repository, action, job["execute"])
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            env=child_environment(self.repository, self.runtime),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
            limit=262144,
        )
        tree = ProcessTree(process.pid)
        # Independent watchdog cleans children if the runner is killed abruptly.
        guardian = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "labconsole.guardian",
            str(os.getpid()),
            str(process.pid),
            str(self.runtime),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
        source = ACTIONS[action]["agent"]

        async def track():
            while process.returncode is None:
                await tree.capture_async(self.process_scan_lock)
                await asyncio.sleep(1)

        tracker = asyncio.create_task(track())
        try:
            assert process.stdout
            await self.emit(
                job, source, "agent.starting", {"action": action, "pid": process.pid}
            )
            async with asyncio.timeout(ACTIONS[action]["timeout"]):
                async for raw in process.stdout:
                    line = raw.decode(errors="replace")
                    if action.startswith("dba."):
                        if len(raw) > 4096 or not line.strip().isdigit():
                            raise ValueError("query_result_rejected")
                        await self.emit(
                            job, "dba", "query.completed", {"total": int(line.strip())}
                        )
                    for actor, event, payload, correlation in parse_line(line, source):
                        await self.emit(job, actor, event, payload, correlation)
                        if event == "agent.ready" and action in {
                            "health.lab",
                            "audit.lab",
                        }:
                            (
                                self.health_ready
                                if action == "health.lab"
                                else self.audit_ready
                            ).set()
                            await self.status(job, "ready")
                        if listener:
                            listener(actor, event, payload, correlation)
                        if event == "audit.attempt.unsafe_success":
                            raise ValueError("unsafe_unexpected_success")
                    if listener:
                        # Flush all receipts from one structured stdout record
                        # before allowing orchestration to cancel its producer.
                        listener(source, "runner.line_processed", {}, None)
                return await process.wait()
        finally:
            tracker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await tracker
            await tree.stop(self.process_scan_lock)
            await process.wait()
            # Watchdog exits when every recorded child is gone.
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(guardian.wait(), 5)

    async def three(self, job: dict[str, Any]) -> None:
        if not job["execute"]:
            for action in ("health.lab", "audit.lab", "audit.drop", "audit.alter"):
                if await self.execute(job, action):
                    raise ValueError("preflight_failed")
            return
        self.health_ready.clear()
        self.audit_ready.clear()
        receipts: dict[str, set[str]] = {}
        all_done = asyncio.Event()

        def listener(
            actor: str, event: str, payload: dict[str, Any], correlation: str | None
        ):
            category = payload.get("category")
            if category not in {"query_latency", "destructive_ddl", "schema_change"}:
                return
            key = f"{category}:{correlation}"
            receipts.setdefault(key, set()).add(event)
            complete = {
                k.split(":")[0]
                for k, v in receipts.items()
                if {"mcp.validated", "notification.sent", "dba.recorded"} <= v
            }
            if len(complete) == 3:
                all_done.set()

        health = asyncio.create_task(self.execute(job, "health.lab", listener))
        audit = asyncio.create_task(self.execute(job, "audit.lab", listener))

        async def complete():
            await asyncio.gather(
                asyncio.wait_for(self.health_ready.wait(), 90),
                asyncio.wait_for(self.audit_ready.wait(), 90),
            )
            load = asyncio.create_task(self.execute(job, "health.load", listener))
            confirmation: asyncio.Task[Any] | None = None
            try:
                results = await asyncio.gather(
                    self.execute(job, "audit.drop", listener),
                    self.execute(job, "audit.alter", listener),
                )
                if any(results):
                    raise ValueError("audit_attempt_failed")
                confirmation = asyncio.create_task(
                    asyncio.wait_for(all_done.wait(), 600)
                )
                done, _ = await asyncio.wait(
                    {load, confirmation}, return_when=asyncio.FIRST_COMPLETED
                )
                if load in done:
                    load_code = await load
                    if load_code != 0:
                        raise ValueError("health_load_failed")
                await confirmation
            finally:
                if confirmation is not None:
                    confirmation.cancel()
                load.cancel()
                await asyncio.gather(
                    load,
                    *([confirmation] if confirmation is not None else []),
                    return_exceptions=True,
                )

        async def guard():
            while True:
                if health.done() or audit.done():
                    raise ValueError("monitor_stopped_before_confirmation")
                await asyncio.sleep(0.1)

        orchestration = asyncio.create_task(complete())
        watchdog = asyncio.create_task(guard())
        try:
            done, _ = await asyncio.wait(
                {orchestration, watchdog}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                await task
        finally:
            for task in (health, audit, orchestration, watchdog):
                task.cancel()
            await asyncio.gather(
                health, audit, orchestration, watchdog, return_exceptions=True
            )
            self.health_ready.clear()
            self.audit_ready.clear()

    async def run(self, job: dict[str, Any]) -> None:
        try:
            await self.status(job, "validating")
            await self.status(job, "starting")
            if job["action"] == "lab.three":
                await self.three(job)
                code = 0
            else:
                if (
                    job["action"] not in {"health.lab", "audit.lab"}
                    or not job["execute"]
                ):
                    await self.status(job, "running")
                code = await self.execute(job, job["action"])
            await self.status(
                job,
                "succeeded" if code == 0 else "failed",
                None if code == 0 else "process_failed",
            )
        except asyncio.CancelledError:
            await self.status(job, "cancelled")
            raise
        except TimeoutError:
            await self.status(job, "timed_out", "deadline_exceeded")
        except Exception:
            await self.status(job, "failed", "execution_failed")
        finally:
            if job["action"] == "health.lab":
                self.health_ready.clear()
            if job["action"] == "audit.lab":
                self.audit_ready.clear()
            self.job_specs.pop(job["id"], None)

    async def handle(self, payload: dict[str, Any]) -> None:
        kind = payload.get("type")
        if kind == "start":
            job = payload["job"]
            if set(job) - {
                "id",
                "action",
                "execute",
                "status",
                "mode",
                "created_at",
                "updated_at",
                "actor",
            }:
                raise ValueError("runner_arguments_denied")
            action = job.get("action")
            if (
                action not in self.scope
                or action not in ACTIONS
                or job.get("mode") != "integrated"
                or job.get("execute")
                and not self.allow_execute
            ):
                raise ValueError("runner_action_denied")
            if job["id"] in self.seen:
                return
            requested = set(ACTIONS[action]["resources"])
            if any(
                requested.intersection(ACTIONS[j["action"]]["resources"])
                for j in self.job_specs.values()
            ):
                await self.status(job, "failed", "resource_busy")
                return
            self.seen.add(job["id"])
            self.job_specs[job["id"]] = job
            self.jobs[job["id"]] = asyncio.create_task(self.run(job))
        elif kind == "stop":
            task = self.jobs.get(payload["job_id"])
            if task:
                task.cancel()
        elif kind in {"catalog", "artifact"}:
            try:
                result = (
                    {"items": await asyncio.to_thread(self.catalog.list)}
                    if kind == "catalog"
                    else {
                        "content": await asyncio.to_thread(
                            self.catalog.read,
                            payload["id"],
                            payload["format"],
                            payload["sha"],
                        )
                    }
                )
            except (ValueError, OSError):
                result = {"error": "artifact_unavailable_or_replaced"}
            await self.send(
                {
                    "type": "result",
                    "request_id": payload["request_id"],
                    "result": result,
                }
            )
        else:
            raise ValueError("unknown_runner_message")

    async def shutdown(self) -> None:
        for task in self.jobs.values():
            task.cancel()
        await asyncio.gather(*self.jobs.values(), return_exceptions=True)


async def serve(
    url: str,
    repository: Path,
    runtime: Path,
    key: str,
    allow_execute: bool,
    scope: set[str],
    reconnect_grace_seconds: float | None = None,
) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "wss" and not (
        parsed.scheme == "ws" and parsed.hostname in {"localhost", "127.0.0.1"}
    ):
        raise ValueError("wss_required")
    grace = (
        float(os.environ.get("LAB_RUNNER_RECONNECT_GRACE_SECONDS", "15"))
        if reconnect_grace_seconds is None
        else reconnect_grace_seconds
    )
    if not 0 <= grace <= 60:
        raise ValueError("runner_reconnect_grace_out_of_range")
    receiver = Signer(key, "runner")
    outbox = ReliableOutbox()
    runner = Runner(repository, runtime, outbox.send, allow_execute, scope)
    disconnected_since: float | None = None
    reconnect_delay = min(1.0, max(0.05, grace / 3 if grace else 0.05))
    try:
        while True:
            try:
                async with connect(
                    url,
                    additional_headers={"Authorization": f"Bearer {key}"},
                    max_size=4_000_000,
                ) as socket:
                    sender = Signer(key, "control")
                    lock = asyncio.Lock()
                    disconnected_since = None

                    async def socket_send(payload: dict[str, Any]) -> None:
                        async with lock:
                            await socket.send(json.dumps(sender.sign(payload)))

                    async def heartbeat() -> None:
                        while True:
                            await socket_send(
                                {
                                    "type": "heartbeat",
                                    "execute_enabled": allow_execute,
                                    "actions": sorted(scope),
                                }
                            )
                            await asyncio.sleep(5)

                    async def receive() -> None:
                        async for message in socket:
                            await runner.handle(receiver.verify(json.loads(message)))

                    tasks = {
                        asyncio.create_task(heartbeat()),
                        asyncio.create_task(outbox.relay(socket, sender, lock)),
                        asyncio.create_task(receive()),
                    }
                    LOGGER.info("runner_connected")
                    try:
                        done, _ = await asyncio.wait(
                            tasks, return_when=asyncio.FIRST_COMPLETED
                        )
                        for task in done:
                            await task
                        raise ConnectionError("runner_socket_closed")
                    finally:
                        for task in tasks:
                            task.cancel()
                        await asyncio.gather(*tasks, return_exceptions=True)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                if disconnected_since is None:
                    disconnected_since = time.monotonic()
                LOGGER.warning(
                    "runner_disconnected_reconnecting error_type=%s grace_seconds=%s",
                    type(error).__name__,
                    grace,
                )

            if (
                disconnected_since is not None
                and runner.has_active_jobs()
                and time.monotonic() - disconnected_since >= grace
            ):
                LOGGER.error("runner_reconnect_grace_expired")
                await runner.shutdown()
                await outbox.clear()
                runner = Runner(repository, runtime, outbox.send, allow_execute, scope)
                disconnected_since = None
            await asyncio.sleep(reconnect_delay)
    finally:
        await runner.shutdown()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--url", default="ws://127.0.0.1:8000/runner")
    parser.add_argument("--runtime", type=Path, default=Path("runtime/runner"))
    parser.add_argument("--allow-execute", action="store_true")
    parser.add_argument("--actions", default=",".join(ACTIONS))
    args = parser.parse_args()
    scope = set(args.actions.split(","))
    if not scope <= ACTIONS.keys():
        parser.error("unknown action")
    try:
        asyncio.run(
            serve(
                args.url,
                args.repository,
                args.runtime,
                os.environ.get("LAB_RUNNER_KEY", ""),
                args.allow_execute,
                scope,
            )
        )
    except KeyboardInterrupt:
        print("runner_stopped")


if __name__ == "__main__":
    main()
