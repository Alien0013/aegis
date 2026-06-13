from __future__ import annotations

import asyncio

import httpx


def _app(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    monkeypatch.setenv("AEGIS_DASHBOARD_TOKEN", "t")
    from aegis.config import Config
    from aegis.dashboard_fastapi import create_app

    return create_app(Config.load())


async def _request(app, method: str, path: str, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


def test_fastapi_dashboard_auth_and_cookie(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)

    res = asyncio.run(_request(app, "GET", "/api/status"))
    assert res.status_code == 401

    res = asyncio.run(_request(app, "GET", "/"))
    assert res.status_code == 200
    assert "aegis_dashboard_token" in res.headers.get("set-cookie", "")

    res = asyncio.run(_request(app, "GET", "/api/status", headers={"X-Aegis-Token": "t"}))
    assert res.status_code == 200


def test_fastapi_files_upload_and_mkdir(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)

    res = asyncio.run(_request(
        app,
        "POST",
        "/api/files/mkdir",
        json={"path": str(tmp_path), "name": "created", "exist_ok": True},
        headers={"X-Aegis-Token": "t"},
    ))
    assert res.status_code == 200
    assert res.json()["ok"] is True
    assert (tmp_path / "created").is_dir()

    res = asyncio.run(_request(
        app,
        "POST",
        "/api/files/upload",
        data={"path": str(tmp_path / "created")},
        files={"file": ("hello.txt", b"uploaded", "text/plain")},
        headers={"X-Aegis-Token": "t"},
    ))
    assert res.status_code == 200
    assert res.json()["ok"] is True
    assert (tmp_path / "created" / "hello.txt").read_text() == "uploaded"


def test_fastapi_registers_live_and_pty_websockets(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)

    routes = {getattr(route, "path", ""): type(route).__name__ for route in app.routes}
    assert routes["/api/ws"] == "APIWebSocketRoute"
    assert routes["/api/pty"] == "APIWebSocketRoute"
    for path in (
        "/api/auth/me",
        "/api/config/schema",
        "/api/config/raw",
        "/api/env",
        "/api/sessions/search",
        "/api/sessions/stats",
    ):
        assert routes[path] == "APIRoute"


def test_fastapi_config_and_env_control_plane(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    headers = {"X-Aegis-Token": "t"}

    res = asyncio.run(_request(app, "GET", "/api/auth/me", headers=headers))
    assert res.status_code == 200
    assert res.json()["authenticated"] is True

    defaults = asyncio.run(_request(app, "GET", "/api/config/defaults", headers=headers))
    assert defaults.status_code == 200
    assert defaults.json()["model"]["provider"]

    schema = asyncio.run(_request(app, "GET", "/api/config/schema", headers=headers))
    assert schema.status_code == 200
    assert any(f["path"] == "model.provider" for f in schema.json()["fields"])

    set_key = asyncio.run(_request(
        app,
        "POST",
        "/api/env",
        json={"key": "OPENAI_API_KEY", "value": "sk-test-secret"},
        headers=headers,
    ))
    assert set_key.status_code == 200
    assert set_key.json()["ok"] is True

    env_list = asyncio.run(_request(app, "GET", "/api/env", headers=headers))
    row = next(k for k in env_list.json()["keys"] if k["key"] == "OPENAI_API_KEY")
    assert row["set"] is True
    assert row["preview"] == "****"
    assert row["length"] == len("sk-test-secret")

    reveal = asyncio.run(_request(app, "GET", "/api/env/OPENAI_API_KEY/reveal", headers=headers))
    assert reveal.status_code == 200
    assert reveal.json()["value"] == "sk-test-secret"

    deleted = asyncio.run(_request(app, "DELETE", "/api/env/OPENAI_API_KEY", headers=headers))
    assert deleted.status_code == 200
    assert deleted.json()["ok"] is True
    missing = asyncio.run(_request(app, "GET", "/api/env/OPENAI_API_KEY/reveal", headers=headers))
    assert missing.status_code == 404


def test_fastapi_sessions_control_plane(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    headers = {"X-Aegis-Token": "t"}

    from aegis.session import Session, SessionStore
    from aegis.types import Message

    store = SessionStore()
    session = Session.create("dashboard typed session")
    session.messages = [
        Message.user("remember the typed route migration"),
        Message.assistant("typed session routes are wired"),
    ]
    store.save(session)

    listed = asyncio.run(_request(app, "GET", "/api/sessions", headers=headers))
    assert listed.status_code == 200
    assert any(row["id"] == session.id for row in listed.json())

    stats = asyncio.run(_request(app, "GET", "/api/sessions/stats", headers=headers))
    assert stats.status_code == 200
    assert stats.json()["session_count"] >= 1
    assert stats.json()["message_count"] >= 2

    found = asyncio.run(_request(
        app,
        "GET",
        "/api/sessions/search?query=typed%20route&limit=3",
        headers=headers,
    ))
    assert found.status_code == 200
    assert found.json()["mode"] == "discover"
    assert found.json()["results"][0]["session_id"] == session.id

    detail = asyncio.run(_request(app, "GET", f"/api/sessions/{session.id}", headers=headers))
    assert detail.status_code == 200
    assert detail.json()["found"] is True
    assert detail.json()["messages"][0]["content"] == "remember the typed route migration"

    export = asyncio.run(_request(app, "GET", f"/api/sessions/{session.id}/export", headers=headers))
    assert export.status_code == 200
    assert export.json()["messages"][0]["content"] == "remember the typed route migration"
    assert "attachment" in export.headers["content-disposition"]

    deleted = asyncio.run(_request(app, "DELETE", f"/api/sessions/{session.id}", headers=headers))
    assert deleted.status_code == 200
    assert deleted.json()["ok"] is True


def test_fastapi_websocket_auth_and_resize_protocol(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    monkeypatch.setenv("AEGIS_DASHBOARD_TOKEN", "t")
    from types import SimpleNamespace

    from aegis.config import Config
    from aegis.dashboard_fastapi import _RESIZE_RE, _websocket_authorized

    cfg = Config.load()
    assert _websocket_authorized(
        SimpleNamespace(query_params={"token": "t"}, headers={}, cookies={}),
        cfg,
    )
    assert _websocket_authorized(
        SimpleNamespace(query_params={}, headers={"Authorization": "Bearer t"}, cookies={}),
        cfg,
    )
    assert not _websocket_authorized(
        SimpleNamespace(query_params={}, headers={}, cookies={}),
        cfg,
    )

    match = _RESIZE_RE.match(b"\x1b]1337;Resize=cols=120;rows=40\x07")
    assert match
    assert (int(match.group(1)), int(match.group(2))) == (120, 40)


def test_dashboard_pty_argv_uses_aegis_binary(monkeypatch):
    from aegis.dashboard_pty import dashboard_tui_argv

    monkeypatch.setenv("AEGIS_BIN", "/tmp/aegis-test")
    assert dashboard_tui_argv("sess") == ["/tmp/aegis-test", "tui", "--resume", "sess"]
