import time

import pytest
from fastapi.testclient import TestClient

from labconsole.app import create_app

ORIGIN = {"origin": "http://localhost:3000"}
KEY = "test-local-runner-key-not-a-secret-1234567890"


def application(tmp_path, monkeypatch, mode="integrated"):
    monkeypatch.setenv("LAB_RUNTIME", str(tmp_path))
    monkeypatch.setenv("LAB_DEMO_DELAY", "0.001")
    return create_app(
        mode=mode, users={}, database=f"sqlite:///{tmp_path}/test.db", runner_key=KEY
    )


def test_local_integrated_access_without_credentials_preserves_gates(
    tmp_path, monkeypatch
):
    app = application(tmp_path, monkeypatch)
    with TestClient(
        app, base_url="http://localhost:8000", client=("127.0.0.1", 50000)
    ) as client:
        assert client.get("/api/state").status_code == 401
        assert client.post("/api/access").status_code == 403
        access = client.post("/api/access", headers=ORIGIN)
        assert access.status_code == 200
        assert access.json()["role"] == "dba_approver"
        assert access.json()["username"] == "local-dba"
        headers = {**ORIGIN, "x-csrf-token": access.json()["csrf"]}
        cookie = client.cookies.get("lab_session")
        assert "HttpOnly" in access.headers["set-cookie"]
        assert client.get("/api/state").status_code == 200
        assert (
            client.post("/api/access", headers=ORIGIN).json()["csrf"]
            == access.json()["csrf"]
        )
        assert client.cookies.get("lab_session") == cookie
        command = {
            "action": "health.lab",
            "execute": True,
            "request_id": "local-request-123",
            "confirmation": "sakila",
        }
        assert client.post("/api/jobs", headers=ORIGIN, json=command).status_code == 403
        assert (
            client.post(
                "/api/jobs", headers=headers, json={**command, "confirmation": ""}
            ).status_code
            == 422
        )
        user = next(iter(app.state.control.sessions.values()))
        user["reauth_at"] = 0
        assert (
            client.post("/api/jobs", headers=headers, json=command).json()["detail"]
            == "runner_offline"
        )
        user["expires"] = time.time() - 1
        assert (
            client.post("/api/access", headers=ORIGIN).json()["csrf"]
            != headers["x-csrf-token"]
        )


def test_local_read_requests_do_not_consume_operator_rate_budget(tmp_path, monkeypatch):
    app = application(tmp_path, monkeypatch)
    with TestClient(
        app, base_url="http://localhost:8000", client=("127.0.0.1", 50000)
    ) as client:
        client.post("/api/access", headers=ORIGIN)
        app.state.control.limits["local-dba"].extend([time.time()] * 120)

        # Reloading the live console or a latest.html report is a safe read and
        # must never be rejected because the user refreshed the browser.
        assert client.get("/api/state").status_code == 200


@pytest.mark.parametrize(
    "peer,host,extra",
    [
        ("203.0.113.20", "localhost", {}),
        ("127.0.0.1", "example.invalid", {}),
        ("127.0.0.1", "localhost", {"x-forwarded-host": "public.example.invalid"}),
    ],
)
def test_integrated_rejects_nonlocal_http(tmp_path, monkeypatch, peer, host, extra):
    with TestClient(
        application(tmp_path, monkeypatch),
        base_url=f"http://{host}:8000",
        client=(peer, 50000),
    ) as client:
        assert (
            client.post("/api/access", headers={**ORIGIN, **extra}).status_code == 403
        )
        assert client.get("/api/state", headers=extra).status_code == 403


def test_integrated_rejects_public_origin(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_ORIGIN", "https://public.example.invalid")
    with pytest.raises(ValueError, match="requires_local_origin"):
        application(tmp_path, monkeypatch)


def test_local_approval_without_password_still_requires_typed_target(
    tmp_path, monkeypatch
):
    with TestClient(application(tmp_path, monkeypatch, "demo")) as client:
        session = client.post("/api/access", headers=ORIGIN).json()
        headers = {**ORIGIN, "x-csrf-token": session["csrf"]}
        plan = client.post(
            "/api/chat",
            headers=headers,
            json={"intent": "propose", "message": "contar atores"},
        ).json()["proposal"]
        assert client.get("/api/state").json()["jobs"] == []
        url = f"/api/approvals/{plan['id']}"
        assert (
            client.post(
                url, headers=headers, json={"confirmation": "wrong"}
            ).status_code
            == 403
        )
        assert (
            client.post(
                url, headers=headers, json={"confirmation": "sakila"}
            ).status_code
            == 200
        )
        assert (
            client.post(
                url, headers=headers, json={"confirmation": "sakila"}
            ).status_code
            == 409
        )
