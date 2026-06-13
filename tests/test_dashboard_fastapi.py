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
