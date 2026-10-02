"""Real HTTP + signed WebSocket + runner subprocesses, exclusively fake scripts."""

import asyncio
import contextlib
import json
import socket
import sys
import time

import httpx
import psutil
import pytest
import uvicorn

from labconsole.app import create_app
from labconsole.models import ACTIONS
from labconsole.runner import Runner, serve
from labconsole.security import password_hash

pytestmark = pytest.mark.integration

KEY = "integration-test-key-not-production-123456789"
ORIGIN = "http://localhost:3000"
FAKE = """import json, pathlib, sys, time
root = pathlib.Path(__file__).parent
name = pathlib.Path(__file__).name
def outcome(category, key):
    publication = {"accepted":True,"delivery_status":"sent","dba_status":"recorded"}
    if category == "query_latency":
        print(json.dumps({"audit_id":"fake-health","publication_outcomes":[{"category":category,"alert_id":key,**publication}]}),flush=True)
    else:
        print(json.dumps({"audit_id":"fake-audit","publications":[{"dedupe_key":key,"publication":publication}]}),flush=True)
if "--execute" not in sys.argv:
    print(json.dumps({"status":"dry_run"}),flush=True)
elif name == "run-health-check-lab.py":
    print("health-monitor está pronto",flush=True)
    time.sleep(60)
elif name == "run-health-select-simulation.py":
    outcome("query_latency","fake-health-alert")
    time.sleep(60)
elif name == "run-audit-lab.py":
    print("audit-advisor está pronto",flush=True)
    emitted=set()
    while True:
        for category, marker in [("destructive_ddl","blocked-drop"),("schema_change","blocked-alter")]:
            if (root/marker).exists() and marker not in emitted:
                emitted.add(marker)
                outcome(category,marker)
        time.sleep(.02)
else:
    marker = "blocked-drop" if "drop" in name else "blocked-alter"
    (root/marker).touch()
    print("expected_permission_denied",flush=True)
"""


async def until(predicate, timeout=20):
    async with asyncio.timeout(timeout):
        while True:
            value = await predicate()
            if value:
                return value
            await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_http_signed_runner_three_alerts_artifacts_stop_and_reconnect(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LAB_RUNTIME", str(tmp_path / "control"))
    monkeypatch.setenv("LAB_RUNNER_RECONNECT_GRACE_SECONDS", "2")
    repository = tmp_path / "fake-repository"
    monkeypatch.setenv("LAB_REPOSITORY_ROOT", str(repository))
    scripts = repository / "apps/lab-console/scripts"
    scripts.mkdir(parents=True)
    for name in (
        "run-health-check-lab.py",
        "run-health-select-simulation.py",
        "run-audit-lab.py",
        "run-audit-drop-lab.py",
        "run-audit-alter-lab.py",
    ):
        (scripts / name).write_text(FAKE)
    artifacts = repository / "agents/health-check/general_report/results"
    artifacts.mkdir(parents=True)
    (artifacts / "report.json").write_text(
        json.dumps({"audit_id": "fixture", "collected_at": "2026-09-17T10:00:00Z"})
    )
    (artifacts / "report.html").write_text("<html>fixture 2026-09-17T10:00:00Z</html>")
    app = create_app(
        mode="integrated",
        database=f"sqlite:///{tmp_path}/control.db",
        users={
            "operator": {"hash": password_hash("fixture-password"), "role": "operator"}
        },
        runner_key=KEY,
    )
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(app, log_level="error", ws="websockets-sansio")
    )
    server_task = asyncio.create_task(server.serve(sockets=[sock]))
    runner_task = None
    try:
        while not server.started:
            await asyncio.sleep(0.01)
        runner_task = asyncio.create_task(
            serve(
                f"ws://127.0.0.1:{port}/runner",
                repository,
                tmp_path / "runner",
                KEY,
                True,
                set(ACTIONS),
            )
        )
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}", headers={"origin": ORIGIN}
        ) as client:
            response = await client.post(
                "/api/login",
                json={"username": "operator", "password": "fixture-password"},
            )
            assert response.status_code == 200
            client.headers["x-csrf-token"] = response.json()["csrf"]

            async def online():
                return (await client.get("/api/state")).json()["runner"][
                    "execute_enabled"
                ]

            await until(online)
            job = (
                await client.post(
                    "/api/jobs",
                    json={
                        "action": "lab.three",
                        "execute": True,
                        "confirmation": "sakila",
                        "request_id": "three-integration",
                    },
                )
            ).json()

            async def done():
                state = (await client.get("/api/state")).json()
                return (
                    state
                    if next(j for j in state["jobs"] if j["id"] == job["id"])["status"]
                    == "succeeded"
                    else None
                )

            state = await until(done)
            for event in (
                "alert.detected",
                "mcp.validated",
                "notification.sent",
                "dba.recorded",
            ):
                assert len([e for e in state["events"] if e["type"] == event]) == 3
            items = (await client.get("/api/artifacts")).json()
            assert len(items) == 1
            report = await client.get(
                f"/api/artifacts/{items[0]['id']}/html",
                params={"sha": items[0]["sha256"]},
            )
            assert report.status_code == 200 and "fixture" in report.text
            monitor = (
                await client.post(
                    "/api/jobs",
                    json={
                        "action": "audit.lab",
                        "execute": True,
                        "confirmation": "sakila",
                        "request_id": "monitor-integration",
                    },
                )
            ).json()

            async def ready():
                return (
                    next(
                        j
                        for j in (await client.get("/api/state")).json()["jobs"]
                        if j["id"] == monitor["id"]
                    )["status"]
                    == "ready"
                )

            await until(ready)
            generation = app.state.control.runner_generation
            await app.state.control.runner.close()

            async def reconnected():
                state = (await client.get("/api/state")).json()
                return (
                    state
                    if state["runner"]["online"]
                    and app.state.control.runner_generation > generation
                    else None
                )

            reconnected_state = await until(reconnected)
            current_monitor = next(
                item
                for item in reconnected_state["jobs"]
                if item["id"] == monitor["id"]
            )
            assert current_monitor["status"] == "ready"
            await client.post(f"/api/jobs/{monitor['id']}/stop")

            async def stopped():
                return (
                    next(
                        j
                        for j in (await client.get("/api/state")).json()["jobs"]
                        if j["id"] == monitor["id"]
                    )["status"]
                    == "cancelled"
                )

            await until(stopped)
            runner_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await runner_task

            async def offline():
                return not (await client.get("/api/state")).json()["runner"]["online"]

            await until(offline)

            # Reading an already published current report is local and remains
            # available while the operational runner is disconnected.
            console_token = client.cookies.get("lab_session")
            app.state.control.sessions[next(iter(app.state.control.sessions))][
                "expires"
            ] = time.time() - 1
            latest = await client.get("/api/artifacts/current/health/html")
            assert latest.status_code == 200 and "fixture" in latest.text
            assert client.cookies.get("lab_session") == console_token
            assert client.cookies.get("lab_report_access")
            assert (await client.get("/api/state")).status_code == 401
            renewed = await client.post("/api/access")
            assert renewed.status_code == 200
            client.headers["x-csrf-token"] = renewed.json()["csrf"]

            runner_task = asyncio.create_task(
                serve(
                    f"ws://127.0.0.1:{port}/runner",
                    repository,
                    tmp_path / "runner",
                    KEY,
                    True,
                    set(ACTIONS),
                )
            )
            await until(online)
            assert len((await client.get("/api/state")).json()["jobs"]) == 2
    finally:
        if runner_task:
            runner_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await runner_task
        server.should_exit = True
        await server_task
        sock.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "behavior,expected",
    [("raise SystemExit(7)", "failed"), ("import time; time.sleep(30)", "timed_out")],
)
async def test_runner_failure_timeout_cleanup(
    tmp_path, monkeypatch, behavior, expected
):
    messages = []

    async def send(message):
        messages.append(message)

    monkeypatch.setattr(
        "labconsole.runner.command_for",
        lambda *_: ([sys.executable, "-c", behavior], tmp_path),
    )
    monkeypatch.setitem(ACTIONS["health.lab"], "timeout", 0.2)
    runner = Runner(tmp_path, tmp_path / "runtime", send, True)
    await runner.run(
        {
            "id": "fake-job",
            "action": "health.lab",
            "execute": True,
            "mode": "integrated",
        }
    )
    assert messages[-1]["status"] == expected


@pytest.mark.asyncio
async def test_runner_accepts_a_successful_child_already_reaped_by_asyncio(
    tmp_path, monkeypatch
):
    messages = []

    async def send(message):
        messages.append(message)

    def already_gone(pid):
        raise psutil.NoSuchProcess(pid)

    monkeypatch.setattr("labconsole.runner.psutil.Process", already_gone)
    monkeypatch.setattr(
        "labconsole.runner.command_for",
        lambda *_: (
            [sys.executable, "-c", "pass"],
            tmp_path,
        ),
    )
    runner = Runner(tmp_path, tmp_path / "runtime", send, True)
    await runner.run(
        {
            "id": "fast-child",
            "action": "audit.drop",
            "execute": True,
            "mode": "integrated",
        }
    )
    assert messages[-1]["status"] == "succeeded"


@pytest.mark.asyncio
async def test_runner_rejects_extra_arguments(tmp_path):
    async def send(_):
        pass

    runner = Runner(tmp_path, tmp_path, send, True)
    with pytest.raises(ValueError, match="arguments_denied"):
        await runner.handle(
            {
                "type": "start",
                "job": {
                    "id": "bad",
                    "action": "audit.lab",
                    "execute": False,
                    "mode": "integrated",
                    "argv": ["rm"],
                },
            }
        )


@pytest.mark.asyncio
async def test_slow_process_scan_does_not_block_runner_heartbeat_loop(
    tmp_path, monkeypatch
):
    messages = []
    ticks = 0

    async def send(message):
        messages.append(message)

    def slow_capture(_self):
        time.sleep(0.2)

    monkeypatch.setattr("labconsole.runner.ProcessTree.capture", slow_capture)
    monkeypatch.setattr(
        "labconsole.runner.command_for",
        lambda *_: ([sys.executable, "-c", "import time; time.sleep(.45)"], tmp_path),
    )
    monkeypatch.setitem(ACTIONS["health.lab"], "timeout", 2)
    runner = Runner(tmp_path, tmp_path / "runtime", send, True)

    async def heartbeat_probe():
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0.02)

    probe = asyncio.create_task(heartbeat_probe())
    try:
        await runner.run(
            {
                "id": "responsive-runner",
                "action": "health.lab",
                "execute": True,
                "mode": "integrated",
            }
        )
    finally:
        probe.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await probe

    assert ticks >= 15
    assert messages[-1]["status"] == "succeeded"


@pytest.mark.asyncio
async def test_all_six_demo_jobs_run_in_parallel_without_starving_runner(
    tmp_path, monkeypatch
):
    messages = []
    ticks = 0

    async def send(message):
        messages.append(message)

    def slow_capture(_self):
        time.sleep(0.15)

    def fake_command(_repository, action, _execute):
        if action == "health.lab":
            body = "print('health-monitor está pronto', flush=True); import time; time.sleep(30)"
        elif action == "audit.lab":
            body = "print('audit-advisor está pronto', flush=True); import time; time.sleep(30)"
        elif action in {"audit.drop", "audit.alter"}:
            body = "print('expected_permission_denied', flush=True)"
        else:
            body = "import time; time.sleep(30)"
        return [sys.executable, "-c", body], tmp_path

    monkeypatch.setattr("labconsole.runner.ProcessTree.capture", slow_capture)
    monkeypatch.setattr("labconsole.runner.command_for", fake_command)
    runner = Runner(tmp_path, tmp_path / "runtime", send, True)

    async def heartbeat_probe():
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0.02)

    actions = (
        "health.lab",
        "health.load",
        "refactor.lab",
        "audit.lab",
        "audit.drop",
        "audit.alter",
    )
    probe = asyncio.create_task(heartbeat_probe())
    try:
        for index, action in enumerate(actions):
            await runner.handle(
                {
                    "type": "start",
                    "job": {
                        "id": f"parallel-{index}",
                        "action": action,
                        "execute": True,
                        "mode": "integrated",
                    },
                }
            )

        async with asyncio.timeout(10):
            await asyncio.gather(
                runner.jobs["parallel-4"],
                runner.jobs["parallel-5"],
            )

        assert ticks >= 20
        assert all(not runner.jobs[f"parallel-{index}"].done() for index in range(4))
        terminal = {
            (message.get("job_id"), message.get("status"))
            for message in messages
            if message.get("type") == "status"
        }
        assert ("parallel-4", "succeeded") in terminal
        assert ("parallel-5", "succeeded") in terminal
    finally:
        probe.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await probe
        await runner.shutdown()
