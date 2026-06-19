from __future__ import annotations

import asyncio
import base64
import copy
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
import types
import time

import httpx


def _app(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    monkeypatch.setenv("AEGIS_DASHBOARD_TOKEN", "t")
    from aegis.config import Config
    from aegis.dashboard_fastapi import create_app

    return create_app(Config.load())


def _basic_app(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    monkeypatch.delenv("AEGIS_DASHBOARD_TOKEN", raising=False)
    monkeypatch.setenv("AEGIS_DASHBOARD_BASIC_AUTH_USERNAME", "admin")
    monkeypatch.setenv("AEGIS_DASHBOARD_BASIC_AUTH_PASSWORD", "pw-secret")
    monkeypatch.setenv("AEGIS_DASHBOARD_BASIC_AUTH_SECRET", "session-secret")
    from aegis.config import Config
    from aegis.dashboard_fastapi import create_app

    return create_app(Config.load())


def test_desktop_mode_starts_dashboard_cron_ticker(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    monkeypatch.setenv("AEGIS_DESKTOP", "1")
    from aegis.config import Config
    import aegis.cron as cron
    import aegis.dashboard_fastapi as dash_api

    dash_api._DESKTOP_CRON_STARTED = False
    ticks = []
    monkeypatch.setattr(cron, "build_delivery_sink", lambda *_args, **_kwargs: None)

    def tick_once(*_args, **_kwargs):
        ticks.append(True)
        raise RuntimeError("stop after first test tick")

    monkeypatch.setattr(cron, "tick", tick_once)

    assert dash_api._start_desktop_cron_ticker(Config.load()) is True
    for _ in range(50):
        if ticks:
            break
        time.sleep(0.02)
    assert ticks


def test_dashboard_ready_announcement_waits_for_health(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    monkeypatch.setenv("AEGIS_DASHBOARD_TOKEN", "ready-token")
    from aegis.config import Config
    from aegis.dashboard_fastapi import (
        _announce_dashboard_ready_when_live,
        _dashboard_ready_probe_url,
    )

    requests = []

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit=512):
            return b'{"ok": true}'

    def fake_urlopen(req, timeout):
        requests.append((req, timeout))
        return FakeResponse()

    thread = _announce_dashboard_ready_when_live(
        Config.load(),
        "0.0.0.0",
        9123,
        attempts=1,
        interval=0.01,
        timeout=0.2,
        urlopen=fake_urlopen,
    )
    thread.join(1)

    assert not thread.is_alive()
    assert _dashboard_ready_probe_url("::", 9124) == "http://[::1]:9124/api/health"
    assert requests[0][0].full_url == "http://127.0.0.1:9123/api/health"
    assert requests[0][0].get_header("X-aegis-token") == "ready-token"
    assert requests[0][1] == 0.2
    assert "AEGIS_DASHBOARD_READY port=9123" in capsys.readouterr().out


async def _request(app, method: str, path: str, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    # Cookies belong on the client instance (per-request cookies= is deprecated in httpx).
    cookies = kwargs.pop("cookies", None)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", cookies=cookies
    ) as client:
        return await client.request(method, path, **kwargs)


def test_fastapi_dashboard_auth_and_cookie(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)

    providers = asyncio.run(_request(app, "GET", "/api/auth/providers"))
    assert providers.status_code == 200
    assert providers.json()["token_configured"] is True
    token_row = next(row for row in providers.json()["providers"] if row["id"] == "token")
    assert "value" not in token_row
    assert "secret" not in token_row

    res = asyncio.run(_request(app, "GET", "/api/status"))
    assert res.status_code == 401

    res = asyncio.run(_request(app, "GET", "/"))
    assert res.status_code == 200
    assert "aegis_dashboard_token" in res.headers.get("set-cookie", "")

    res = asyncio.run(_request(app, "GET", "/api/status", headers={"X-Aegis-Token": "t"}))
    assert res.status_code == 200
    body = res.json()
    assert body["api_adapter"]["ok"] is True
    assert body["api_adapter"]["transport"] == "aiohttp"
    assert body["api_adapter"]["endpoints"]["responses"] is True
    assert body["api_adapter"]["endpoints"]["jobs"] is True
    assert body["api_adapter"]["features"]["responses_persistence"] is True
    assert body["api_adapter"]["features"]["run_approvals"] is True
    assert "responses" in body["api_adapter"]["stores"]
    assert "runs" in body["api_adapter"]["stores"]
    assert "jobs" in body["api_adapter"]["stores"]


def test_fastapi_basic_login_session_and_logout(tmp_path, monkeypatch):
    app = _basic_app(tmp_path, monkeypatch)

    providers = asyncio.run(_request(app, "GET", "/api/auth/providers"))
    assert providers.status_code == 200
    assert providers.json()["basic_configured"] is True
    assert providers.json()["login_url"] == "/login"
    assert any(row["id"] == "basic" for row in providers.json()["providers"])

    res = asyncio.run(_request(app, "GET", "/api/status"))
    assert res.status_code == 401

    login_page = asyncio.run(_request(app, "GET", "/login"))
    assert login_page.status_code == 200
    assert "AEGIS" in login_page.text

    deep_link = asyncio.run(_request(app, "GET", "/sessions"))
    assert deep_link.status_code == 200
    assert "<form method='post' action='/auth/login'>" in deep_link.text
    assert "window.__AEGIS_SESSION_TOKEN__" not in deep_link.text

    bad = asyncio.run(_request(
        app,
        "POST",
        "/api/auth/login",
        json={"username": "admin", "password": "wrong"},
    ))
    assert bad.status_code == 401

    good = asyncio.run(_request(
        app,
        "POST",
        "/api/auth/login",
        json={"username": "admin", "password": "pw-secret"},
    ))
    assert good.status_code == 200
    session_cookie = good.cookies["aegis_dashboard_session"]

    authed = asyncio.run(_request(
        app,
        "GET",
        "/api/status",
        cookies={"aegis_dashboard_session": session_cookie},
    ))
    assert authed.status_code == 200
    authed_deep_link = asyncio.run(_request(
        app,
        "GET",
        "/sessions",
        cookies={"aegis_dashboard_session": session_cookie},
    ))
    assert authed_deep_link.status_code == 200
    assert "window.__AEGIS_SESSION_TOKEN__" in authed_deep_link.text
    assert "<form method='post' action='/auth/login'>" not in authed_deep_link.text

    raw = base64.b64encode(b"admin:pw-secret").decode()
    basic = asyncio.run(_request(app, "GET", "/api/status", headers={"Authorization": f"Basic {raw}"}))
    assert basic.status_code == 200

    logout = asyncio.run(_request(app, "POST", "/api/auth/logout"))
    assert logout.status_code == 200
    assert "aegis_dashboard_session" in logout.headers.get("set-cookie", "")


def test_fastapi_remote_bind_fails_closed_without_auth(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    monkeypatch.delenv("AEGIS_DASHBOARD_TOKEN", raising=False)
    monkeypatch.delenv("AEGIS_DASHBOARD_BASIC_AUTH_USERNAME", raising=False)
    monkeypatch.delenv("AEGIS_DASHBOARD_BASIC_AUTH_PASSWORD", raising=False)
    from pytest import raises

    from aegis.config import Config
    from aegis.dashboard_fastapi import create_app

    cfg = Config.load()
    cfg.data.setdefault("server", {})["dashboard_host"] = "0.0.0.0"
    with raises(RuntimeError, match="non-loopback host without auth"):
        create_app(cfg)


def test_dashboard_peer_guard_helpers(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    from aegis.config import Config
    from aegis.dashboard_fastapi import _peer_allowed

    cfg = Config.load()
    cfg.data.setdefault("server", {})["dashboard_host"] = "127.0.0.1"
    assert _peer_allowed("127.0.0.1", "localhost:9119", cfg)
    assert not _peer_allowed("10.0.0.5", "localhost:9119", cfg)

    monkeypatch.setenv("AEGIS_DASHBOARD_TOKEN", "remote-token")
    cfg.data["server"]["dashboard_host"] = "aegis.local"
    assert _peer_allowed("10.0.0.5", "aegis.local:9119", cfg)
    assert not _peer_allowed("10.0.0.5", "evil.local:9119", cfg)


def test_fastapi_files_upload_and_mkdir(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    headers = {"X-Aegis-Token": "t"}

    note = tmp_path / "note.txt"
    note.write_text("hello files\n", encoding="utf-8")

    default_cwd = asyncio.run(_request(app, "GET", "/api/fs/default-cwd", headers=headers))
    assert default_cwd.status_code == 200
    assert default_cwd.json()["ok"] is True
    assert default_cwd.json()["cwd"] == default_cwd.json()["path"]

    listed = asyncio.run(_request(
        app,
        "GET",
        f"/api/fs/list?path={str(tmp_path)}",
        headers=headers,
    ))
    assert listed.status_code == 200
    assert any(row["name"] == "note.txt" for row in listed.json()["entries"])

    read_text = asyncio.run(_request(
        app,
        "GET",
        f"/api/fs/read-text?path={str(note)}",
        headers=headers,
    ))
    assert read_text.status_code == 200
    assert read_text.json()["content"] == "hello files\n"

    data_url = asyncio.run(_request(
        app,
        "GET",
        f"/api/fs/read-data-url?path={str(note)}",
        headers=headers,
    ))
    assert data_url.status_code == 200
    assert data_url.json()["ok"] is True
    assert data_url.json()["data_url"].startswith("data:text/plain;base64,")
    assert data_url.json()["dataUrl"] == data_url.json()["data_url"]

    image = tmp_path / "pixel.png"
    image.write_bytes(b"png")
    media = asyncio.run(_request(
        app,
        "GET",
        f"/api/media?path={str(image)}",
        headers=headers,
    ))
    assert media.status_code == 200
    assert media.json()["ok"] is True
    assert media.json()["dataUrl"].startswith("data:image/png;base64,")

    download = asyncio.run(_request(
        app,
        "GET",
        f"/api/files/download?path={str(note)}",
        headers=headers,
    ))
    assert download.status_code == 200
    assert download.content == b"hello files\n"
    assert "attachment" in download.headers.get("content-disposition", "")

    secret = tmp_path / ".env"
    secret.write_text("TOKEN=secret\n", encoding="utf-8")
    blocked = asyncio.run(_request(
        app,
        "GET",
        f"/api/files/download?path={str(secret)}",
        headers=headers,
    ))
    assert blocked.status_code == 403

    res = asyncio.run(_request(
        app,
        "POST",
        "/api/files/mkdir",
        json={"path": str(tmp_path), "name": "created", "exist_ok": True},
        headers=headers,
    ))
    assert res.status_code == 200
    assert res.json()["ok"] is True
    assert (tmp_path / "created").is_dir()

    full_dir = tmp_path / "full-created"
    res = asyncio.run(_request(
        app,
        "POST",
        "/api/files/mkdir",
        json={"path": str(full_dir), "parents": True, "exist_ok": True},
        headers=headers,
    ))
    assert res.status_code == 200
    assert res.json()["ok"] is True
    assert full_dir.is_dir()

    res = asyncio.run(_request(
        app,
        "POST",
        "/api/files/upload",
        data={"path": str(tmp_path / "created")},
        files={"file": ("hello.txt", b"uploaded", "text/plain")},
        headers=headers,
    ))
    assert res.status_code == 200
    assert res.json()["ok"] is True
    assert (tmp_path / "created" / "hello.txt").read_text() == "uploaded"

    encoded = base64.b64encode(b"json uploaded").decode("ascii")
    res = asyncio.run(_request(
        app,
        "POST",
        "/api/files/upload",
        json={"path": str(full_dir), "name": "json.txt", "data_url": f"data:text/plain;base64,{encoded}"},
        headers=headers,
    ))
    assert res.status_code == 200
    assert res.json()["ok"] is True
    json_upload = full_dir / "json.txt"
    assert json_upload.read_text(encoding="utf-8") == "json uploaded"

    deleted_alias = asyncio.run(_request(
        app,
        "DELETE",
        "/api/files",
        json={"path": str(json_upload)},
        headers=headers,
    ))
    assert deleted_alias.status_code == 200
    assert deleted_alias.json()["ok"] is True
    assert not json_upload.exists()

    deleted = asyncio.run(_request(
        app,
        "POST",
        "/api/files/delete",
        json={"path": str(note)},
        headers=headers,
    ))
    assert deleted.status_code == 200
    assert deleted.json()["ok"] is True
    assert not note.exists()

    desktop_log = asyncio.run(_request(app, "GET", "/api/logs?name=desktop", headers=headers))
    assert desktop_log.status_code == 200
    assert desktop_log.json()["path"].endswith("desktop.log")

    pairing = asyncio.run(_request(app, "GET", "/api/pairing", headers=headers))
    assert pairing.status_code == 200
    assert set(pairing.json()) >= {"approved", "pending"}

    analytics = asyncio.run(_request(app, "GET", "/api/analytics/usage?days=7", headers=headers))
    assert analytics.status_code == 200
    analytics_body = analytics.json()
    assert "series" in analytics_body
    assert "balance" in analytics_body


def test_fastapi_registers_live_and_pty_websockets(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)

    routes = {getattr(route, "path", ""): type(route).__name__ for route in app.routes}
    assert routes["/api/ws"] == "APIWebSocketRoute"
    assert routes["/api/pty"] == "APIWebSocketRoute"
    for path in (
        "/api/auth/me",
        "/api/auth/providers",
        "/api/auth/ws-ticket",
        "/api/events",
        "/api/pub",
        "/api/credentials/pools",
        "/api/credential-pools/status",
        "/api/update/check",
        "/api/portal",
        "/api/actions/status",
        "/api/admin/status",
        "/api/observability/contract",
        "/api/hooks/test",
        "/api/config/schema",
        "/api/config/raw",
        "/api/env",
        "/api/browser/manage",
        "/api/sessions/search",
        "/api/sessions/stats",
        "/api/sessions/empty/count",
        "/api/sessions/bulk-delete",
        "/api/cron/jobs",
        "/api/cron/service",
        "/api/gateway/status",
        "/api/messaging/platforms",
        "/api/platforms",
        "/api/platforms/registry",
    ):
        assert routes[path] == "APIRoute"


def test_fastapi_websocket_jsonrpc_helper(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    from aegis.config import Config
    from aegis.dashboard_fastapi import _dashboard_ws_rpc_response

    cfg = Config.load()
    assert _dashboard_ws_rpc_response("ping", cfg) == {"type": "pong"}

    capabilities = _dashboard_ws_rpc_response(
        '{"jsonrpc":"2.0","id":1,"method":"dashboard.capabilities"}',
        cfg,
    )
    assert capabilities["jsonrpc"] == "2.0"
    assert capabilities["id"] == 1
    assert capabilities["result"]["transport"]["jsonrpc"] == "2.0"
    assert "/api/ws" == capabilities["result"]["routes"]["events"]
    assert "/api/events" == capabilities["result"]["routes"]["sse"]
    assert "/api/pub" == capabilities["result"]["routes"]["publish"]

    status = _dashboard_ws_rpc_response(
        '{"jsonrpc":"2.0","id":"s","method":"dashboard.get","params":{"path":"/api/status"}}',
        cfg,
    )
    assert status["id"] == "s"
    assert status["result"]["version"]
    assert status["result"]["model"] == cfg.get("model.default")

    analytics = _dashboard_ws_rpc_response(
        '{"jsonrpc":"2.0","id":"a","method":"dashboard.get","params":{"path":"/api/analytics/usage?days=7"}}',
        cfg,
    )
    assert analytics["id"] == "a"
    assert "series" in analytics["result"]
    assert "balance" in analytics["result"]

    plugins = _dashboard_ws_rpc_response(
        '{"jsonrpc":"2.0","id":"p","method":"dashboard.get","params":{"path":"/api/plugins"}}',
        cfg,
    )
    assert plugins["id"] == "p"
    assert "dashboard_plugins" in plugins["result"]
    assert "dashboard_api_mounts" in plugins["result"]
    assert "manifests" in plugins["result"]

    blocked = _dashboard_ws_rpc_response(
        '{"jsonrpc":"2.0","id":"bad","method":"dashboard.get","params":{"path":"/etc/passwd"}}',
        cfg,
    )
    assert blocked["error"]["code"] == -32602


def test_fastapi_event_alias_and_publish_route(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    headers = {"X-Aegis-Token": "t"}

    from aegis.eventbus import BUS

    sub = BUS.subscribe()
    try:
        denied = asyncio.run(_request(app, "POST", "/api/pub", json={"type": "blocked"}))
        assert denied.status_code == 401

        published = asyncio.run(_request(
            app,
            "POST",
            "/api/pub",
            json={"type": "dashboard_probe", "payload": {"token": "secret-token", "ok": True}},
            headers=headers,
        ))
        assert published.status_code == 200
        body = published.json()
        assert body["ok"] is True
        assert body["event"]["type"] == "dashboard_probe"
        assert body["event"]["payload"]["token"] == "[redacted]"

        event = sub.get(timeout=1)
        assert event == body["event"]
    finally:
        BUS.unsubscribe(sub)


def test_fastapi_portal_admin_and_credential_aliases(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    monkeypatch.setenv("AEGIS_DASHBOARD_TOKEN", "t")

    from aegis.config import Config
    from aegis.credentials import reset as reset_credential_pools
    from aegis.dashboard_fastapi import create_app

    cfg = Config.load()
    cfg.set("credential_pools.anthropic.keys", [
        "sk-ant-test-111111111111",
        "sk-ant-test-222222222222",
    ])
    cfg.set("credential_pools.anthropic.strategy", "round_robin")
    reset_credential_pools()
    app = create_app(cfg)
    headers = {"X-Aegis-Token": "t"}

    pools = asyncio.run(_request(app, "GET", "/api/credentials/pools", headers=headers))
    assert pools.status_code == 200
    anthropic = next(row for row in pools.json()["pools"] if row["provider"] == "anthropic")
    assert anthropic["keys"] == 2
    assert anthropic["strategy"] == "round_robin"
    assert "sk-ant-test" not in json.dumps(pools.json())

    detail = asyncio.run(_request(app, "GET", "/api/credentials/pools/anthropic", headers=headers))
    assert detail.status_code == 200
    assert detail.json()["pool"]["provider"] == "anthropic"

    status_alias = asyncio.run(_request(app, "GET", "/api/credential-pools/status", headers=headers))
    assert status_alias.status_code == 200
    assert status_alias.json()["count"] >= 1

    update = asyncio.run(_request(app, "GET", "/api/update/check", headers=headers))
    assert update.status_code == 200
    assert "version" in update.json()

    update_post = asyncio.run(_request(app, "POST", "/api/portal/update/check", headers=headers))
    assert update_post.status_code == 200
    assert update_post.json()["version"] == update.json()["version"]

    portal = asyncio.run(_request(app, "GET", "/api/portal", headers=headers))
    assert portal.status_code == 200
    assert portal.json()["ok"] is True
    assert "system" in portal.json()
    assert "actions" in portal.json()

    actions = asyncio.run(_request(app, "GET", "/api/actions/status", headers=headers))
    assert actions.status_code == 200
    assert any(row["id"] == "update_check" for row in actions.json()["actions"])

    run_action = asyncio.run(_request(
        app,
        "POST",
        "/api/actions/run",
        json={"action": "update_check"},
        headers=headers,
    ))
    assert run_action.status_code == 200
    assert run_action.json()["version"] == update.json()["version"]

    admin = asyncio.run(_request(app, "GET", "/api/admin/status", headers=headers))
    assert admin.status_code == 200
    assert admin.json()["ok"] is True
    assert admin.json()["auth"]["token_configured"] is True
    reset_credential_pools()


def test_fastapi_observability_contract_and_hook_test(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    monkeypatch.setenv("AEGIS_DASHBOARD_TOKEN", "t")

    import shlex
    import sys

    from aegis.config import Config
    from aegis.dashboard_fastapi import create_app

    cfg = Config.load()
    command = f"{shlex.quote(sys.executable)} -c \"print('hook-ok')\""
    cfg.set("hooks.user_prompt", command)
    app = create_app(cfg)
    headers = {"X-Aegis-Token": "t"}

    contract = asyncio.run(_request(app, "GET", "/api/observability/contract", headers=headers))
    assert contract.status_code == 200
    body = contract.json()
    assert "assistant_delta" in body["agent_event_types"]
    assert body["configured_hooks"]["user_prompt"] == [command]
    assert body["routes"]["events_sse"] == "/api/events"
    user_prompt_hook = next(row for row in body["hooks"] if row["event"] == "user_prompt")
    assert user_prompt_hook["configured"] is True

    hook = asyncio.run(_request(
        app,
        "POST",
        "/api/hooks/test",
        json={"event": "user_prompt", "context": {"session_id": "sess_test"}},
        headers=headers,
    ))
    assert hook.status_code == 200
    assert hook.json()["ok"] is True
    assert hook.json()["count"] == 1
    assert hook.json()["results"][0]["stdout"].strip() == "hook-ok"

    unknown = asyncio.run(_request(
        app,
        "POST",
        "/api/observability/hooks/test",
        json={"event": "not_real"},
        headers=headers,
    ))
    assert unknown.status_code == 200
    assert unknown.json()["ok"] is False
    assert "user_prompt" in unknown.json()["known_events"]


def test_fastapi_browser_manage_route(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    headers = {"X-Aegis-Token": "t"}

    from aegis import browser_connect

    monkeypatch.setattr(browser_connect, "is_browser_debug_ready", lambda *_args, **_kwargs: True)

    connected = asyncio.run(_request(
        app,
        "POST",
        "/api/browser/manage",
        json={"action": "connect", "url": "127.0.0.1:9222"},
        headers=headers,
    ))
    assert connected.status_code == 200
    assert connected.json()["connected"] is True
    assert connected.json()["url"] == "http://127.0.0.1:9222"

    status = asyncio.run(_request(app, "GET", "/api/browser/manage", headers=headers))
    assert status.status_code == 200
    assert status.json()["url"] == "http://127.0.0.1:9222"

    bad = asyncio.run(_request(
        app,
        "POST",
        "/api/browser/manage",
        json={"action": "connect", "url": "file:///tmp/nope"},
        headers=headers,
    ))
    assert bad.status_code == 400
    assert "unsupported browser url scheme" in bad.json()["error"]


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
    fields = {f["path"]: f for f in schema.json()["fields"]}
    assert fields["learn.auto_apply_skills"]["label"] == "Auto-write skills"
    assert "after substantial turns" in fields["learn.auto_apply_skills"]["description"]
    assert fields["skills.auto_load"]["label"] == "Auto-load skills"
    assert "before matching turns" in fields["skills.auto_load"]["description"]
    assert fields["skills.allowlist"]["label"] == "Skill allowlist"
    assert fields["skills.bundles"]["label"] == "Skill bundles"
    assert fields["agent.service_tier"]["label"] == "Fast mode"
    assert fields["agent.service_tier"]["enum"] == ["", "normal", "priority"]
    assert fields["display.tool_progress_grouping"]["enum"] == ["accumulate", "separate"]
    assert fields["display.memory_notifications"]["enum"] == ["off", "on", "verbose"]

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

    put_key = asyncio.run(_request(
        app,
        "PUT",
        "/api/env",
        json={"key": "ANTHROPIC_API_KEY", "value": "sk-ant-test"},
        headers=headers,
    ))
    assert put_key.status_code == 200
    assert put_key.json()["ok"] is True

    reveal_body = asyncio.run(_request(
        app,
        "POST",
        "/api/env/reveal",
        json={"key": "ANTHROPIC_API_KEY"},
        headers=headers,
    ))
    assert reveal_body.status_code == 200
    assert reveal_body.json()["value"] == "sk-ant-test"

    delete_body = asyncio.run(_request(
        app,
        "DELETE",
        "/api/env",
        json={"key": "ANTHROPIC_API_KEY"},
        headers=headers,
    ))
    assert delete_body.status_code == 200
    assert delete_body.json()["ok"] is True


def test_fastapi_env_rejects_invalid_keys_and_values(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    headers = {"X-Aegis-Token": "t"}

    bad_key = asyncio.run(_request(
        app,
        "POST",
        "/api/env",
        json={"key": "openai-api-key", "value": "sk-test"},
        headers=headers,
    ))
    assert bad_key.status_code == 400
    assert bad_key.json()["ok"] is False
    assert "uppercase env var name" in bad_key.json()["error"]

    empty = asyncio.run(_request(
        app,
        "POST",
        "/api/env",
        json={"key": "OPENAI_API_KEY", "value": ""},
        headers=headers,
    ))
    assert empty.status_code == 400
    assert empty.json()["error"] == "value must not be empty"

    multiline = asyncio.run(_request(
        app,
        "POST",
        "/api/env",
        json={"key": "OPENAI_API_KEY", "value": "sk-test\nINJECTED_KEY=x"},
        headers=headers,
    ))
    assert multiline.status_code == 400
    assert multiline.json()["error"] == "value must fit on one .env line"

    bad_delete = asyncio.run(_request(
        app,
        "DELETE",
        "/api/env/not-valid",
        headers=headers,
    ))
    assert bad_delete.status_code == 400


def test_fastapi_provider_and_gateway_control_plane_routes(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    headers = {"X-Aegis-Token": "t"}

    schema = asyncio.run(_request(app, "GET", "/api/config/schema", headers=headers)).json()
    exec_mode = next(f for f in schema["fields"] if f["path"] == "tools.exec_mode")
    assert exec_mode["enum"] == ["auto", "ask", "smart", "allowlist", "deny", "full"]
    assert exec_mode["description"]

    providers = asyncio.run(_request(app, "GET", "/api/providers", headers=headers))
    assert providers.status_code == 200
    body = providers.json()
    assert body["provider_catalog"]
    assert body["active"]

    import aegis.doctor as doctor

    def fake_probe(config):
        return True, f"{config.get('model.provider')}/{config.get('model.default')} ok"

    monkeypatch.setattr(doctor, "probe_provider", fake_probe)
    probe = asyncio.run(_request(
        app,
        "POST",
        "/api/providers/test",
        json={"provider": "openai", "model": "gpt-test"},
        headers=headers,
    ))
    assert probe.status_code == 200
    assert probe.json() == {
        "ok": True,
        "provider": "openai",
        "model": "gpt-test",
        "detail": "openai/gpt-test ok",
    }

    monkeypatch.setitem(doctor.CHANNEL_PROBES, "telegram", lambda: (True, "bot @aegis"))
    channel = asyncio.run(_request(
        app,
        "POST",
        "/api/gateway/probe",
        json={"channel": "telegram"},
        headers=headers,
    ))
    assert channel.status_code == 200
    assert channel.json() == {"ok": True, "channel": "telegram", "detail": "bot @aegis"}


def test_fastapi_model_route_aliases(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    headers = {"X-Aegis-Token": "t"}

    info = asyncio.run(_request(app, "GET", "/api/model/info", headers=headers))
    assert info.status_code == 200
    assert info.json()["provider"]
    assert info.json()["model"]
    assert "effective_context_length" in info.json()

    options = asyncio.run(_request(app, "GET", "/api/model/options", headers=headers))
    assert options.status_code == 200
    option_body = options.json()
    assert option_body["provider"] == info.json()["provider"]
    assert any(row["slug"] == info.json()["provider"] for row in option_body["providers"])
    assert "model_inventory" in option_body

    recommended = asyncio.run(_request(
        app,
        "GET",
        f"/api/model/recommended-default?provider={info.json()['provider']}",
        headers=headers,
    ))
    assert recommended.status_code == 200
    assert recommended.json()["provider"] == info.json()["provider"]
    assert "model" in recommended.json()
    assert recommended.json()["free_tier"] is None

    set_main = asyncio.run(_request(
        app,
        "POST",
        "/api/model/set",
        json={"scope": "main", "provider": info.json()["provider"], "model": info.json()["model"]},
        headers=headers,
    ))
    assert set_main.status_code == 200
    assert set_main.json()["ok"] is True
    assert set_main.json()["scope"] == "main"
    assert set_main.json()["provider"] == info.json()["provider"]
    assert set_main.json()["model"] == info.json()["model"]

    aux = asyncio.run(_request(app, "GET", "/api/model/auxiliary", headers=headers))
    assert aux.status_code == 200
    assert aux.json()["main"]["provider"] == info.json()["provider"]
    assert any(row["task"] == "vision" for row in aux.json()["tasks"])

    set_aux = asyncio.run(_request(
        app,
        "POST",
        "/api/model/set",
        json={"scope": "auxiliary", "task": "vision", "provider": "auto", "model": "small-helper"},
        headers=headers,
    ))
    assert set_aux.status_code == 200
    assert set_aux.json()["ok"] is True
    assert set_aux.json()["scope"] == "auxiliary"
    assert set_aux.json()["tasks"] == ["vision"]

    aux_after = asyncio.run(_request(app, "GET", "/api/model/auxiliary", headers=headers))
    vision = next(row for row in aux_after.json()["tasks"] if row["task"] == "vision")
    assert vision["provider"] == "auto"
    assert vision["model"] == "small-helper"


def test_fastapi_main_model_set_persists_and_clears_base_url(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    headers = {"X-Aegis-Token": "t"}

    info = asyncio.run(_request(app, "GET", "/api/model/info", headers=headers))
    original_provider = info.json()["provider"]
    original_model = info.json()["model"]

    set_custom = asyncio.run(_request(
        app,
        "POST",
        "/api/model/set",
        json={
            "scope": "main",
            "provider": "local-proxy",
            "model": "local-model",
            "base_url": "http://127.0.0.1:11434/v1",
        },
        headers=headers,
    ))
    assert set_custom.status_code == 200
    custom_body = set_custom.json()
    assert custom_body["ok"] is True
    assert custom_body["provider"] == "local-proxy"
    assert custom_body["model"] == "local-model"
    assert custom_body["base_url"] == "http://127.0.0.1:11434/v1"

    from aegis.config import Config

    saved = Config.load()
    assert saved.get("model.provider") == "local-proxy"
    assert saved.get("model.default") == "local-model"
    assert saved.get("model.base_url") == "http://127.0.0.1:11434/v1"

    clear_custom = asyncio.run(_request(
        app,
        "POST",
        "/api/model/set",
        json={
            "scope": "main",
            "provider": original_provider,
            "model": original_model,
            "base_url": "",
        },
        headers=headers,
    ))
    assert clear_custom.status_code == 200
    assert clear_custom.json()["ok"] is True
    assert clear_custom.json()["base_url"] == ""
    assert Config.load().get("model.base_url", "") == ""


def test_fastapi_messaging_platform_aliases(tmp_path, monkeypatch):
    for key in (
        "TELEGRAM_BOT_TOKEN",
        "MATTERMOST_URL",
        "MATTERMOST_BOT_TOKEN",
        "MATTERMOST_WEBHOOK_SECRET",
        "WEBHOOK_CHANNEL_SECRET",
        "WEBHOOK_CHANNEL_RATE_LIMIT_PER_MINUTE",
    ):
        monkeypatch.delenv(key, raising=False)
    app = _app(tmp_path, monkeypatch)
    headers = {"X-Aegis-Token": "t"}

    platforms = asyncio.run(_request(app, "GET", "/api/messaging/platforms", headers=headers))
    assert platforms.status_code == 200
    rows = platforms.json()["platforms"]
    telegram = next(row for row in rows if row["id"] == "telegram")
    assert telegram["name"] == "Telegram"
    assert telegram["enabled"] is False
    assert telegram["state"] == "disabled"
    assert any(field["key"] == "TELEGRAM_BOT_TOKEN" and field["required"] for field in telegram["env_vars"])
    assert any(
        field["key"] == "TELEGRAM_ALLOWED_CHATS" and not field["required"]
        for field in telegram["env_vars"]
    )
    assert "TELEGRAM_ALLOWED_CHATS" in telegram["optional_env_vars"]
    assert "TELEGRAM_ALLOWED_CHATS" in telegram["metadata"]["optional_env"]
    assert "TELEGRAM_ALLOWED_CHATS" not in telegram["missing_env_vars"]
    assert telegram["auth_type"] == "bot_token"
    assert telegram["transport"] == "long_poll"
    assert "media" in telegram["capabilities"]
    assert telegram["metadata"]["adapter_class"].endswith("TelegramAdapter")
    mattermost = next(row for row in rows if row["id"] == "mattermost")
    assert mattermost["transport"] == "http_webhook"
    assert mattermost["auth_type"] == "bearer_and_webhook_secret"
    assert "MATTERMOST_WEBHOOK_SECRET" in mattermost["optional_env_vars"]
    assert "threads" in mattermost["capabilities"]
    assert mattermost["metadata"]["security"]["auth_type"] == "bearer"
    webhook = next(row for row in rows if row["id"] == "webhook")
    assert webhook["transport"] == "http"
    assert "WEBHOOK_CHANNEL_RATE_LIMIT_PER_MINUTE" in webhook["optional_env_vars"]
    assert "idempotency" in webhook["capabilities"]
    assert "thread" in webhook["delivery_modes"]
    assert "X-Webhook-Signature" in webhook["metadata"]["security"]["signature_schemes"]
    whatsapp = next(row for row in rows if row["id"] == "whatsapp")
    assert whatsapp["transport"] == "http_bridge"
    assert whatsapp["auth_type"] == "local_http_bridge"
    assert "WHATSAPP_CHANNEL_SECRET" in whatsapp["optional_env_vars"]
    assert "whatsapp_bridge_aliases" in whatsapp["capabilities"]
    assert whatsapp["metadata"]["security"]["bridge"] == "webhook"

    registry = asyncio.run(_request(app, "GET", "/api/platforms/registry", headers=headers))
    assert registry.status_code == 200
    assert registry.json()["count"] >= len(rows)
    reg_telegram = next(row for row in registry.json()["registry"] if row["id"] == "telegram")
    assert reg_telegram["metadata"]["auth_type"] == "bot_token"
    reg_mattermost = next(row for row in registry.json()["registry"] if row["id"] == "mattermost")
    assert reg_mattermost["metadata"]["adapter_class"].endswith("MattermostAdapter")
    reg_whatsapp = next(row for row in registry.json()["registry"] if row["id"] == "whatsapp")
    assert reg_whatsapp["metadata"]["transport"] == "http_bridge"

    detail = asyncio.run(_request(app, "GET", "/api/platforms/telegram", headers=headers))
    assert detail.status_code == 200
    assert detail.json()["platform"]["metadata"]["transport"] == "long_poll"

    mattermost_detail = asyncio.run(_request(app, "GET", "/api/platforms/mattermost", headers=headers))
    assert mattermost_detail.status_code == 200
    assert mattermost_detail.json()["platform"]["state"] == "disabled"
    assert "MATTERMOST_URL" in mattermost_detail.json()["platform"]["missing_env_vars"]

    whatsapp_detail = asyncio.run(_request(app, "GET", "/api/platforms/whatsapp", headers=headers))
    assert whatsapp_detail.status_code == 200
    assert whatsapp_detail.json()["platform"]["state"] == "disabled"
    assert whatsapp_detail.json()["platform"]["missing_env_vars"] == []

    invalid = asyncio.run(_request(
        app,
        "PUT",
        "/api/messaging/platforms/telegram",
        json={"env": {"DISCORD_BOT_TOKEN": "wrong-platform"}},
        headers=headers,
    ))
    assert invalid.status_code == 400
    assert "not configurable" in invalid.json()["error"]

    updated = asyncio.run(_request(
        app,
        "PUT",
        "/api/messaging/platforms/telegram",
        json={
            "enabled": True,
            "env": {
                "TELEGRAM_BOT_TOKEN": "test-token",
                "TELEGRAM_ALLOWED_CHATS": "42,99",
                "TELEGRAM_GROUP_TRIGGER_MODE": "addressed",
            },
        },
        headers=headers,
    ))
    assert updated.status_code == 200
    assert updated.json()["platform"]["enabled"] is True
    assert updated.json()["platform"]["configured"] is True
    updated_fields = {field["key"]: field for field in updated.json()["platform"]["env_vars"]}
    assert updated_fields["TELEGRAM_ALLOWED_CHATS"]["set"] is True
    assert updated_fields["TELEGRAM_GROUP_TRIGGER_MODE"]["set"] is True

    import aegis.doctor as doctor

    monkeypatch.setitem(doctor.CHANNEL_PROBES, "telegram", lambda: (True, "bot ready"))
    probe = asyncio.run(_request(app, "POST", "/api/messaging/platforms/telegram/test", headers=headers))
    assert probe.status_code == 200
    assert probe.json()["ok"] is True
    assert probe.json()["message"] == "bot ready"

    cleared = asyncio.run(_request(
        app,
        "PUT",
        "/api/messaging/platforms/telegram",
        json={"enabled": False, "clear_env": ["TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_CHATS"]},
        headers=headers,
    ))
    assert cleared.status_code == 200
    assert cleared.json()["platform"]["state"] == "disabled"
    assert "TELEGRAM_BOT_TOKEN" in cleared.json()["platform"]["missing_env_vars"]
    cleared_fields = {field["key"]: field for field in cleared.json()["platform"]["env_vars"]}
    assert cleared_fields["TELEGRAM_ALLOWED_CHATS"]["set"] is False


def test_fastapi_messaging_platform_optional_controls(tmp_path, monkeypatch):
    for key in (
        "DISCORD_BOT_TOKEN",
        "DISCORD_ALLOWED_GUILDS",
        "DISCORD_TRIGGER_MODE",
        "SLACK_BOT_TOKEN",
        "SLACK_APP_TOKEN",
        "SLACK_ALLOWED_CHANNELS",
    ):
        monkeypatch.delenv(key, raising=False)
    app = _app(tmp_path, monkeypatch)
    headers = {"X-Aegis-Token": "t"}

    discord = asyncio.run(_request(app, "GET", "/api/platforms/discord", headers=headers))
    assert discord.status_code == 200
    discord_fields = {field["key"]: field for field in discord.json()["platform"]["env_vars"]}
    assert discord_fields["DISCORD_BOT_TOKEN"]["required"] is True
    assert discord_fields["DISCORD_ALLOWED_GUILDS"]["required"] is False
    assert discord_fields["DISCORD_TRIGGER_MODE"]["required"] is False
    assert "DISCORD_ALLOWED_GUILDS" not in discord.json()["platform"]["missing_env_vars"]

    updated = asyncio.run(_request(
        app,
        "PUT",
        "/api/messaging/platforms/discord",
        json={
            "env": {
                "DISCORD_ALLOWED_GUILDS": "G1,dm",
                "DISCORD_TRIGGER_MODE": "addressed",
            },
        },
        headers=headers,
    ))
    assert updated.status_code == 200
    updated_fields = {field["key"]: field for field in updated.json()["platform"]["env_vars"]}
    assert updated_fields["DISCORD_ALLOWED_GUILDS"]["set"] is True
    assert updated_fields["DISCORD_TRIGGER_MODE"]["set"] is True
    assert "DISCORD_BOT_TOKEN" in updated.json()["platform"]["missing_env_vars"]

    invalid = asyncio.run(_request(
        app,
        "PUT",
        "/api/messaging/platforms/slack",
        json={"env": {"DISCORD_ALLOWED_GUILDS": "wrong-platform"}},
        headers=headers,
    ))
    assert invalid.status_code == 400

    slack = asyncio.run(_request(
        app,
        "PUT",
        "/api/messaging/platforms/slack",
        json={"env": {"SLACK_ALLOWED_CHANNELS": "C1,C2"}},
        headers=headers,
    ))
    assert slack.status_code == 200
    slack_fields = {field["key"]: field for field in slack.json()["platform"]["env_vars"]}
    assert slack_fields["SLACK_ALLOWED_CHANNELS"]["required"] is False
    assert slack_fields["SLACK_ALLOWED_CHANNELS"]["set"] is True
    assert slack_fields["SLACK_TRIGGER_MODE"]["required"] is False


def test_fastapi_typed_config_profile_gateway_and_plugin_routes(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    headers = {"X-Aegis-Token": "t"}

    changed = asyncio.run(_request(
        app,
        "PATCH",
        "/api/config/fields",
        json={"updates": {
            "tools.exec_mode": "smart",
            "agent.compression.max_tool_tokens": 12000,
            "agent.service_tier": "priority",
        }},
        headers=headers,
    ))
    assert changed.status_code == 200
    body = changed.json()
    assert body["ok"] is True
    assert body["changed"]["tools.exec_mode"] == "smart"
    assert body["changed"]["agent.service_tier"] == "priority"
    raw_config = asyncio.run(_request(app, "GET", "/api/config/raw", headers=headers)).json()["config"]
    assert raw_config["agent"]["compression"]["max_tool_tokens"] == 12000
    assert raw_config["agent"]["service_tier"] == "priority"

    bad_raw = copy.deepcopy(raw_config)
    bad_raw["agent"]["max_iterations"] = "not-number"
    rejected_raw = asyncio.run(_request(
        app,
        "PUT",
        "/api/config/raw",
        json={"config": bad_raw},
        headers=headers,
    ))
    assert rejected_raw.status_code == 400
    assert "agent.max_iterations" in rejected_raw.json()["errors"][0]

    bad = asyncio.run(_request(
        app,
        "PATCH",
        "/api/config/fields",
        json={"updates": {"tools.exec_mode": "root", "agent.service_tier": "turbo"}},
        headers=headers,
    ))
    assert bad.status_code == 400
    assert "one of" in bad.json()["errors"]["tools.exec_mode"]
    assert "one of" in bad.json()["errors"]["agent.service_tier"]

    created = asyncio.run(_request(
        app,
        "POST",
        "/api/profiles",
        json={"name": "builder", "content": "Build small and verify.\n", "activate": True},
        headers=headers,
    ))
    assert created.status_code == 200
    assert created.json()["active"] == "builder"

    profile = asyncio.run(_request(app, "GET", "/api/profiles/builder", headers=headers))
    assert profile.status_code == 200
    assert profile.json()["content"] == "Build small and verify.\n"

    patched = asyncio.run(_request(
        app,
        "PATCH",
        "/api/profiles/builder",
        json={"content": "Build small, verify, then ship.\n"},
        headers=headers,
    ))
    assert patched.status_code == 200
    assert patched.json()["profile"]["content"] == "Build small, verify, then ship.\n"

    activated = asyncio.run(_request(app, "POST", "/api/profiles/default/activate", headers=headers))
    assert activated.status_code == 200
    assert activated.json()["active"] == ""

    traversal = asyncio.run(_request(app, "GET", "/api/profiles/..secret", headers=headers))
    assert traversal.status_code in {400, 404}

    catalog = asyncio.run(_request(app, "GET", "/api/gateway/channels/catalog", headers=headers))
    assert catalog.status_code == 200
    assert any(row["id"] == "telegram" for row in catalog.json()["channels"])

    configured = asyncio.run(_request(
        app,
        "PATCH",
        "/api/gateway/channels/telegram",
        json={"enabled": True, "service_tier": "priority"},
        headers=headers,
    ))
    assert configured.status_code == 200
    telegram = configured.json()["channel"]
    assert telegram["id"] == "telegram"
    assert telegram["enabled"] is True
    assert telegram["profile"]["service_tier"] == "priority"

    import aegis.doctor as doctor

    monkeypatch.setitem(doctor.CHANNEL_PROBES, "telegram", lambda: (True, "bot ready"))
    probe = asyncio.run(_request(app, "POST", "/api/gateway/channels/telegram/probe", headers=headers))
    assert probe.status_code == 200
    assert probe.json()["detail"] == "bot ready"

    plugin_dir = tmp_path / "sample_plugin"
    plugin_dir.mkdir()
    (plugin_dir / "sample.py").write_text("# plugin\n", encoding="utf-8")
    valid = asyncio.run(_request(
        app,
        "POST",
        "/api/plugins/validate",
        json={"source": str(plugin_dir)},
        headers=headers,
    ))
    assert valid.status_code == 200
    assert valid.json()["ok"] is True

    missing = asyncio.run(_request(
        app,
        "POST",
        "/api/plugins/validate",
        json={"source": str(plugin_dir / "missing")},
        headers=headers,
    ))
    assert missing.status_code == 400


def test_fastapi_runtime_profiles_control_plane(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    headers = {"X-Aegis-Token": "t"}

    listed = asyncio.run(_request(app, "GET", "/api/runtime-profiles", headers=headers))
    assert listed.status_code == 200
    assert listed.json()["active"] == "default"
    assert any(row["default"] and row["name"] == "default" for row in listed.json()["profiles"])

    created = asyncio.run(_request(
        app,
        "POST",
        "/api/runtime-profiles",
        json={"name": "research", "activate": True},
        headers=headers,
    ))
    assert created.status_code == 200
    assert created.json()["active"] == "research"
    assert any(row["name"] == "research" and row["active"] for row in created.json()["profiles"])

    activated = asyncio.run(_request(app, "POST", "/api/runtime-profiles/default/activate", headers=headers))
    assert activated.status_code == 200
    assert activated.json()["active"] == "default"

    deleted = asyncio.run(_request(app, "DELETE", "/api/runtime-profiles/research", headers=headers))
    assert deleted.status_code == 200
    assert deleted.json()["ok"] is True
    assert not any(row["name"] == "research" for row in deleted.json()["profiles"])

    default_delete = asyncio.run(_request(app, "DELETE", "/api/runtime-profiles/default", headers=headers))
    assert default_delete.status_code == 400


def test_fastapi_typed_mcp_and_skills_routes(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    headers = {"X-Aegis-Token": "t"}

    created = asyncio.run(_request(
        app,
        "POST",
        "/api/mcp/servers",
        json={"name": "local", "command": "python -m server", "env": {"TOKEN": "x"}},
        headers=headers,
    ))
    assert created.status_code == 200
    assert created.json()["ok"] is True

    detail = asyncio.run(_request(app, "GET", "/api/mcp/servers/local", headers=headers))
    assert detail.status_code == 200
    assert detail.json()["server"]["name"] == "local"

    patched = asyncio.run(_request(
        app,
        "PATCH",
        "/api/mcp/servers/local",
        json={"args": ["-m", "patched"]},
        headers=headers,
    ))
    assert patched.status_code == 200

    import aegis.mcp.client as mcp_client

    monkeypatch.setattr(mcp_client, "probe_server", lambda config, name: {
        "ok": True,
        "name": name,
        "tools": ["read", "write"],
    })
    monkeypatch.setattr(mcp_client, "tool_checklist", lambda config, name: {
        "ok": True,
        "name": name,
        "tools": [{"name": "read", "enabled": True}, {"name": "write", "enabled": False}],
    })
    saved = {}

    def fake_save_tool_checklist(config, name, include):
        saved[name] = include

    monkeypatch.setattr(mcp_client, "save_tool_checklist", fake_save_tool_checklist)

    probe = asyncio.run(_request(app, "POST", "/api/mcp/servers/local/probe", headers=headers))
    assert probe.status_code == 200
    assert probe.json()["tools"] == ["read", "write"]

    probe_alias = asyncio.run(_request(app, "POST", "/api/mcp/servers/local/test", headers=headers))
    assert probe_alias.status_code == 200
    assert probe_alias.json()["tools"] == ["read", "write"]

    disabled = asyncio.run(_request(
        app,
        "PUT",
        "/api/mcp/servers/local/enabled",
        json={"enabled": False},
        headers=headers,
    ))
    assert disabled.status_code == 200
    local = next(row for row in disabled.json()["servers"] if row["name"] == "local")
    assert local["enabled"] is False
    assert local["status"] == "disabled"

    tools = asyncio.run(_request(
        app,
        "POST",
        "/api/mcp/servers/local/tools",
        json={"include": ["read"]},
        headers=headers,
    ))
    assert tools.status_code == 200
    assert saved["local"] == ["read"]

    def fake_install_from_catalog(config, name):
        servers = config.data.setdefault("mcp", {}).setdefault("servers", {})
        servers[name] = {"command": "uvx", "args": [name]}
        config.save()
        return servers[name]

    monkeypatch.setattr(mcp_client, "install_from_catalog", fake_install_from_catalog)
    catalog_install = asyncio.run(_request(
        app,
        "POST",
        "/api/mcp/catalog/install",
        json={"name": "fetcher"},
        headers=headers,
    ))
    assert catalog_install.status_code == 200
    assert catalog_install.json()["name"] == "fetcher"
    assert any(row["name"] == "fetcher" for row in catalog_install.json()["servers"])

    skill = asyncio.run(_request(
        app,
        "POST",
        "/api/skills",
        json={
            "name": "dash-test",
            "description": "Dashboard test skill",
            "body": "Use this skill from the dashboard.",
        },
        headers=headers,
    ))
    assert skill.status_code == 200
    assert skill.json()["ok"] is True

    detail = asyncio.run(_request(app, "GET", "/api/skills/dash-test", headers=headers))
    assert detail.status_code == 200
    content = detail.json()["content"]
    assert "Dashboard test skill" in content

    updated_content = content.replace("Use this skill", "Use this edited skill")
    updated = asyncio.run(_request(
        app,
        "PATCH",
        "/api/skills/dash-test",
        json={"content": updated_content},
        headers=headers,
    ))
    assert updated.status_code == 200
    assert "edited skill" in updated.json()["body"]

    pinned = asyncio.run(_request(app, "POST", "/api/skills/dash-test/pin", headers=headers))
    assert pinned.status_code == 200
    assert pinned.json()["pinned"] is True

    deleted_skill = asyncio.run(_request(app, "DELETE", "/api/skills/dash-test", headers=headers))
    assert deleted_skill.status_code == 200
    assert deleted_skill.json()["ok"] is True

    deleted_mcp = asyncio.run(_request(app, "DELETE", "/api/mcp/servers/local", headers=headers))
    assert deleted_mcp.status_code == 200
    assert deleted_mcp.json()["ok"] is True


def test_fastapi_skill_delete_target_validation(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    from aegis import config as cfg
    from aegis.dashboard_fastapi import _validate_skill_delete_target

    skills_root = cfg.skills_dir()
    safe = skills_root / "safe-skill"
    safe.mkdir(parents=True)
    (safe / "SKILL.md").write_text("---\nname: safe-skill\ndescription: Safe skill.\n---\nbody\n")

    target, err = _validate_skill_delete_target(safe / "SKILL.md")
    assert err == ""
    assert target == safe.resolve()

    (skills_root / "SKILL.md").write_text("---\nname: root\ndescription: Root.\n---\nbody\n")
    assert "skills root" in _validate_skill_delete_target(skills_root / "SKILL.md")[1]

    outside = tmp_path / "outside-skill"
    outside.mkdir()
    (outside / "SKILL.md").write_text("---\nname: outside-skill\ndescription: Outside.\n---\nbody\n")
    assert "only workspace or personal skills" in _validate_skill_delete_target(outside / "SKILL.md")[1]

    real = skills_root / "real-skill"
    real.mkdir()
    (real / "SKILL.md").write_text("---\nname: real-skill\ndescription: Real.\n---\nbody\n")
    linked = skills_root / "linked-skill"
    try:
        linked.symlink_to(real, target_is_directory=True)
    except (OSError, NotImplementedError):
        return
    assert "symlinked" in _validate_skill_delete_target(linked / "SKILL.md")[1]


def test_fastapi_websocket_ticket_flow(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    headers = {"X-Aegis-Token": "t"}

    from types import SimpleNamespace

    from aegis.config import Config
    from aegis.dashboard_fastapi import _websocket_authorized

    denied = asyncio.run(_request(app, "POST", "/api/auth/ws-ticket"))
    assert denied.status_code == 401

    issued = asyncio.run(_request(app, "POST", "/api/auth/ws-ticket", headers=headers))
    assert issued.status_code == 200
    body = issued.json()
    assert body["ok"] is True
    assert body["ticket"]
    assert body["ttl_seconds"] > 0

    cfg = Config.load()
    ws = SimpleNamespace(query_params={"ticket": body["ticket"]}, headers={}, cookies={})
    assert _websocket_authorized(ws, cfg)
    assert not _websocket_authorized(ws, cfg)


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
    empty_session = Session.create("empty dashboard session")
    store.save(empty_session)
    alias_empty_session = Session.create("empty alias dashboard session")
    store.save(alias_empty_session)
    bulk_session = Session.create("bulk delete session")
    bulk_session.messages = [Message.user("bulk delete me")]
    store.save(bulk_session)
    alias_bulk_session = Session.create("bulk alias delete session")
    alias_bulk_session.messages = [Message.user("bulk alias delete me")]
    store.save(alias_bulk_session)

    listed = asyncio.run(_request(app, "GET", "/api/sessions", headers=headers))
    assert listed.status_code == 200
    assert any(row["id"] == session.id for row in listed.json())

    stats = asyncio.run(_request(app, "GET", "/api/sessions/stats", headers=headers))
    assert stats.status_code == 200
    assert stats.json()["session_count"] >= 1
    assert stats.json()["message_count"] >= 2
    assert stats.json()["empty_sessions"] >= 1

    empty = asyncio.run(_request(app, "GET", "/api/sessions/empty", headers=headers))
    assert empty.status_code == 200
    assert empty_session.id in empty.json()["ids"]
    assert alias_empty_session.id in empty.json()["ids"]

    empty_count = asyncio.run(_request(app, "GET", "/api/sessions/empty/count", headers=headers))
    assert empty_count.status_code == 200
    assert empty_count.json()["count"] >= 2
    assert empty_count.json()["empty_sessions"] == empty_count.json()["count"]

    pruned_empty = asyncio.run(_request(
        app,
        "POST",
        "/api/sessions/prune-empty",
        json={"dry_run": False},
        headers=headers,
    ))
    assert pruned_empty.status_code == 200
    assert empty_session.id in pruned_empty.json()["ids"]
    assert alias_empty_session.id in pruned_empty.json()["ids"]

    delete_empty_session = Session.create("delete empty alias session")
    store.save(delete_empty_session)
    deleted_empty = asyncio.run(_request(app, "DELETE", "/api/sessions/empty", headers=headers))
    assert deleted_empty.status_code == 200
    assert deleted_empty.json()["dry_run"] is False
    assert delete_empty_session.id in deleted_empty.json()["ids"]

    deleted_many = asyncio.run(_request(
        app,
        "POST",
        "/api/sessions/delete",
        json={"ids": [bulk_session.id, "missing-session"]},
        headers=headers,
    ))
    assert deleted_many.status_code == 200
    assert deleted_many.json()["removed"] == [bulk_session.id]
    assert deleted_many.json()["missing"] == ["missing-session"]

    bulk_deleted = asyncio.run(_request(
        app,
        "POST",
        "/api/sessions/bulk-delete",
        json={"session_ids": [alias_bulk_session.id, "missing-alias-session"]},
        headers=headers,
    ))
    assert bulk_deleted.status_code == 200
    assert bulk_deleted.json()["removed"] == [alias_bulk_session.id]
    assert bulk_deleted.json()["missing"] == ["missing-alias-session"]

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

    renamed = asyncio.run(_request(
        app,
        "POST",
        f"/api/sessions/{session.id}/rename",
        json={"title": "renamed typed session"},
        headers=headers,
    ))
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "renamed typed session"

    patched = asyncio.run(_request(
        app,
        "PATCH",
        f"/api/sessions/{session.id}",
        json={"meta": {"dashboard": True}},
        headers=headers,
    ))
    assert patched.status_code == 200
    assert patched.json()["session"]["meta"]["dashboard"] is True

    messages = asyncio.run(_request(app, "GET", f"/api/sessions/{session.id}/messages", headers=headers))
    assert messages.status_code == 200
    assert messages.json()["count"] == 2

    added = asyncio.run(_request(
        app,
        "POST",
        f"/api/sessions/{session.id}/messages",
        json={"role": "user", "content": "message api append"},
        headers=headers,
    ))
    assert added.status_code == 200
    assert added.json()["message"]["index"] == 2

    msg = asyncio.run(_request(app, "GET", f"/api/sessions/{session.id}/messages/2", headers=headers))
    assert msg.status_code == 200
    assert msg.json()["message"]["content"] == "message api append"

    msg_patch = asyncio.run(_request(
        app,
        "PATCH",
        f"/api/sessions/{session.id}/messages/2",
        json={"content": "message api patched"},
        headers=headers,
    ))
    assert msg_patch.status_code == 200
    assert msg_patch.json()["message"]["content"] == "message api patched"

    msg_delete = asyncio.run(_request(
        app,
        "DELETE",
        f"/api/sessions/{session.id}/messages/2",
        headers=headers,
    ))
    assert msg_delete.status_code == 200
    assert msg_delete.json()["count"] == 2

    export = asyncio.run(_request(app, "GET", f"/api/sessions/{session.id}/export", headers=headers))
    assert export.status_code == 200
    assert export.json()["messages"][0]["content"] == "remember the typed route migration"
    assert "attachment" in export.headers["content-disposition"]

    deleted = asyncio.run(_request(app, "DELETE", f"/api/sessions/{session.id}", headers=headers))
    assert deleted.status_code == 200
    assert deleted.json()["ok"] is True


def test_fastapi_dashboard_chat_stream_persists_session_and_run_across_app_recreate(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    monkeypatch.setenv("AEGIS_DASHBOARD_TOKEN", "t")

    from aegis.agent.agent import Agent
    from aegis.config import Config
    from aegis.dashboard_fastapi import create_app
    from aegis.types import Message

    class FakeAgent:
        stream = False

        def __init__(self, session, store):
            self.session = session
            self.store = store
            self.provider = types.SimpleNamespace(name="fake", model="fake-model", api_mode="fake")
            self.budget = types.SimpleNamespace(
                usage=types.SimpleNamespace(input_tokens=0, output_tokens=0, cache_read=0, cache_write=0),
            )
            self.tool_context = types.SimpleNamespace(session=session)
            self._trace_context = {"trace_id": "trace_cross_session", "turn_id": "turn_cross_session"}

        def run(self, prompt, on_event=None):  # noqa: ANN001
            if on_event:
                on_event({"type": "iteration", "n": 1, "max": 1})
            self.session.messages.append(Message.user(str(prompt)))
            reply = Message.assistant(f"persisted:{prompt}")
            self.session.messages.append(reply)
            self.store.save(self.session)
            return reply

    monkeypatch.setattr(
        Agent,
        "create",
        staticmethod(lambda _config, **kwargs: FakeAgent(kwargs["session"], kwargs["store"])),
    )

    app = create_app(Config.load())
    headers = {"X-Aegis-Token": "t"}
    streamed = asyncio.run(_request(
        app,
        "POST",
        "/api/chat/stream",
        json={"message": "remember this", "session_id": "dash:cross-session"},
        headers=headers,
    ))
    frames = [
        json.loads(line.removeprefix("data: "))
        for line in streamed.text.splitlines()
        if line.startswith("data: ")
    ]
    final = next(frame for frame in frames if frame.get("type") == "final")
    session_id = final["session_id"]
    run_id = final["run_id"]

    recreated = create_app(Config.load())
    session_detail = asyncio.run(_request(recreated, "GET", f"/api/sessions/{session_id}", headers=headers))
    run_detail = asyncio.run(_request(recreated, "GET", f"/api/run?id={run_id}", headers=headers))

    assert streamed.status_code == 200
    assert final["reply"] == "persisted:remember this"
    assert final["trace_id"] == "trace_cross_session"
    assert run_id
    assert session_detail.status_code == 200
    session_body = session_detail.json()
    assert session_body["found"] is True
    assert [m["content"] for m in session_body["messages"]] == [
        "remember this",
        "persisted:remember this",
    ]
    assert session_body["meta"]["last_run_id"] == run_id
    assert session_body["meta"]["last_trace_id"] == "trace_cross_session"
    assert run_detail.status_code == 200
    run_body = run_detail.json()
    assert run_body["found"] is True
    assert run_body["run"]["id"] == run_id
    assert run_body["run"]["session_id"] == session_id
    assert run_body["run"]["status"] == "ok"
    assert run_body["run"]["trace_id"] == "trace_cross_session"
    assert [m["content"] for m in run_body["messages"]] == [
        "remember this",
        "persisted:remember this",
    ]


def test_fastapi_legacy_chat_fallback_uses_cancellable_json_path(tmp_path, monkeypatch):
    import threading

    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    monkeypatch.setenv("AEGIS_DASHBOARD_TOKEN", "t")

    from aegis.config import Config
    from aegis.dashboard_fastapi import create_app
    from aegis.session import Session
    import aegis.surface as surface

    seen: dict[str, object] = {}

    class Runner:
        def __init__(self, *_args, **_kwargs):
            pass

        def load_or_create_session(self, session_id=None, **kwargs):
            seen["load_kwargs"] = kwargs
            return Session(id=session_id or "dash:legacy-fallback", title="legacy fallback")

        def make_agent(self, **kwargs):
            seen["make_agent_kwargs"] = kwargs
            return types.SimpleNamespace(cancel_event=threading.Event())

        def run_prompt(self, prompt, **kwargs):  # noqa: ANN001
            seen["run_kwargs"] = kwargs
            return types.SimpleNamespace(
                text=f"legacy:{prompt}",
                session=kwargs["session"],
                trace_id="trace_legacy",
                turn_id="turn_legacy",
                run_id="run_legacy",
            )

    monkeypatch.setattr(surface, "SurfaceRunner", Runner)
    app = create_app(Config.load())

    response = asyncio.run(_request(
        app,
        "POST",
        "/api/legacy-chat",
        json={"message": "hello", "session_id": "dash:legacy-fallback"},
        headers={"X-Aegis-Token": "t"},
    ))

    assert response.status_code == 200
    body = response.json()
    assert body["reply"] == "legacy:hello"
    assert body["session_id"] == "dash:legacy-fallback"
    assert body["trace_id"] == "trace_legacy"
    assert seen["run_kwargs"]["surface"] == "dashboard"
    assert seen["run_kwargs"]["reuse_agent"] is False
    assert "agent" in seen["run_kwargs"]
    assert "session" in seen["run_kwargs"]
    assert "session_id" not in seen["run_kwargs"]


def test_fastapi_dashboard_chat_stream_disconnect_cancels_live_agent():
    import threading

    from aegis.dashboard_fastapi import _dashboard_chat_streaming_response
    from aegis.session import Session

    class Runner:
        def __init__(self):
            self.started = threading.Event()
            self.finished = threading.Event()
            self.agents = []

        def load_or_create_session(self, session_id=None, **_kwargs):
            return Session(id=session_id or "dash:disconnect", title="disconnect")

        def make_agent(self, **_kwargs):
            agent = types.SimpleNamespace(cancel_event=threading.Event())

            def cancel():
                agent.cancel_event.set()

            agent.cancel = cancel
            self.agents.append(agent)
            return agent

        def run_prompt(self, prompt, **kwargs):  # noqa: ANN001
            self.started.set()
            agent = kwargs["agent"]
            assert kwargs["reuse_agent"] is False
            assert kwargs["surface"] == "dashboard"
            deadline = time.monotonic() + 2
            while not agent.cancel_event.is_set() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.finished.set()
            return types.SimpleNamespace(
                text=f"late:{prompt}",
                session=kwargs["session"],
                trace_id="",
                turn_id="",
                run_id="",
            )

    class DisconnectAfterFirstFrame:
        def __init__(self, runner: Runner):
            self.runner = runner
            self.calls = 0

        async def is_disconnected(self):
            self.calls += 1
            return self.calls >= 3 and self.runner.started.is_set()

    async def consume(response):
        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk)
        return chunks

    runner = Runner()
    response = _dashboard_chat_streaming_response(
        {"message": "slow", "session_id": "dash:disconnect"},
        runner,
        DisconnectAfterFirstFrame(runner),
    )

    chunks = asyncio.run(asyncio.wait_for(consume(response), timeout=3))

    assert chunks
    first = json.loads(chunks[0].decode().split("data: ", 1)[1])
    assert first["type"] == "start"
    assert runner.agents and runner.agents[0].cancel_event.is_set()
    assert runner.finished.wait(1)


def test_fastapi_dashboard_chat_stream_disconnect_reapplies_cancel_after_agent_entry_clear():
    import threading

    from aegis.dashboard_fastapi import _dashboard_chat_streaming_response
    from aegis.session import Session

    class Runner:
        def __init__(self):
            self.started = threading.Event()
            self.finished = threading.Event()
            self.agents = []
            self.saw_cancel_before_clear = False

        def load_or_create_session(self, session_id=None, **_kwargs):
            return Session(id=session_id or "dash:disconnect-race", title="disconnect race")

        def make_agent(self, **_kwargs):
            agent = types.SimpleNamespace(cancel_event=threading.Event())

            def cancel():
                agent.cancel_event.set()

            agent.cancel = cancel
            self.agents.append(agent)
            return agent

        def run_prompt(self, prompt, **kwargs):  # noqa: ANN001
            self.started.set()
            agent = kwargs["agent"]
            deadline = time.monotonic() + 1
            while not agent.cancel_event.is_set() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.saw_cancel_before_clear = agent.cancel_event.is_set()
            agent.cancel_event.clear()  # mirrors Agent.run() clearing the event at entry
            deadline = time.monotonic() + 1
            while not agent.cancel_event.is_set() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.finished.set()
            return types.SimpleNamespace(
                text=f"late:{prompt}",
                session=kwargs["session"],
                trace_id="",
                turn_id="",
                run_id="",
            )

    class DisconnectAfterRunStarts:
        def __init__(self, runner: Runner):
            self.runner = runner
            self.calls = 0

        async def is_disconnected(self):
            self.calls += 1
            if self.calls < 2:
                return False
            return self.runner.started.wait(2)

    async def consume(response):
        return [chunk async for chunk in response.body_iterator]

    runner = Runner()
    response = _dashboard_chat_streaming_response(
        {"message": "slow", "session_id": "dash:disconnect-race"},
        runner,
        DisconnectAfterRunStarts(runner),
    )

    chunks = asyncio.run(asyncio.wait_for(consume(response), timeout=3))

    assert chunks
    assert runner.finished.wait(1)
    assert runner.saw_cancel_before_clear is True
    assert runner.agents and runner.agents[0].cancel_event.is_set()


def test_fastapi_dashboard_chat_stream_close_cancels_live_agent():
    import threading

    from aegis.dashboard_fastapi import _dashboard_chat_streaming_response
    from aegis.session import Session

    class Runner:
        def __init__(self):
            self.started = threading.Event()
            self.finished = threading.Event()
            self.agents = []

        def load_or_create_session(self, session_id=None, **_kwargs):
            return Session(id=session_id or "dash:stream-close", title="stream close")

        def make_agent(self, **_kwargs):
            agent = types.SimpleNamespace(cancel_event=threading.Event())

            def cancel():
                agent.cancel_event.set()

            agent.cancel = cancel
            self.agents.append(agent)
            return agent

        def run_prompt(self, prompt, **kwargs):  # noqa: ANN001
            self.started.set()
            agent = kwargs["agent"]
            deadline = time.monotonic() + 2
            while not agent.cancel_event.is_set() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.finished.set()
            return types.SimpleNamespace(
                text=f"late:{prompt}",
                session=kwargs["session"],
                trace_id="",
                turn_id="",
                run_id="",
            )

    class NeverDisconnected:
        async def is_disconnected(self):
            return False

    async def consume_one_then_close(response):
        iterator = response.body_iterator
        first = await iterator.__anext__()
        await iterator.aclose()
        return first

    runner = Runner()
    response = _dashboard_chat_streaming_response(
        {"message": "slow", "session_id": "dash:stream-close"},
        runner,
        NeverDisconnected(),
    )

    first = asyncio.run(asyncio.wait_for(consume_one_then_close(response), timeout=3))

    assert json.loads(first.decode().split("data: ", 1)[1])["type"] == "start"
    assert runner.agents and runner.agents[0].cancel_event.is_set()
    assert runner.finished.wait(1)


def test_fastapi_dashboard_chat_json_disconnect_reapplies_cancel_after_agent_entry_clear():
    import threading

    from aegis.dashboard_fastapi import _dashboard_chat_json_response
    from aegis.session import Session

    class Runner:
        def __init__(self):
            self.started = threading.Event()
            self.finished = threading.Event()
            self.agents = []
            self.saw_cancel_before_clear = False

        def load_or_create_session(self, session_id=None, **_kwargs):
            return Session(id=session_id or "dash:json-disconnect", title="json disconnect")

        def make_agent(self, **_kwargs):
            agent = types.SimpleNamespace(cancel_event=threading.Event())

            def cancel():
                agent.cancel_event.set()

            agent.cancel = cancel
            self.agents.append(agent)
            return agent

        def run_prompt(self, prompt, **kwargs):  # noqa: ANN001
            self.started.set()
            agent = kwargs["agent"]
            assert kwargs["reuse_agent"] is False
            assert kwargs["surface"] == "dashboard"
            deadline = time.monotonic() + 1
            while not agent.cancel_event.is_set() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.saw_cancel_before_clear = agent.cancel_event.is_set()
            agent.cancel_event.clear()  # mirrors Agent.run() clearing the event at entry
            deadline = time.monotonic() + 1
            while not agent.cancel_event.is_set() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.finished.set()
            return types.SimpleNamespace(
                text=f"late:{prompt}",
                session=kwargs["session"],
                trace_id="",
                turn_id="",
                run_id="",
            )

    class DisconnectAfterRunStarts:
        def __init__(self, runner: Runner):
            self.runner = runner

        async def is_disconnected(self):
            return self.runner.started.wait(2)

    runner = Runner()
    response = asyncio.run(_dashboard_chat_json_response(
        {"message": "slow", "session_id": "dash:json-disconnect"},
        runner,
        DisconnectAfterRunStarts(runner),
    ))

    assert response.status_code == 499
    assert json.loads(response.body)["cancelled"] is True
    assert runner.finished.wait(1)
    assert runner.saw_cancel_before_clear is True
    assert runner.agents and runner.agents[0].cancel_event.is_set()


def test_dashboard_chat_stream_marks_late_run_cancelled(tmp_path, monkeypatch):
    import threading

    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    from aegis.dashboard import _dashboard_chat_stream
    from aegis.runs import RunStore
    from aegis.session import Session

    class Runner:
        def load_or_create_session(self, session_id=None, **_kwargs):
            return Session(id=session_id or "dash:late-cancel", title="late cancel")

        def make_agent(self, **_kwargs):
            agent = types.SimpleNamespace(cancel_event=threading.Event())

            def cancel():
                agent.cancel_event.set()

            agent.cancel = cancel
            return agent

        def run_prompt(self, prompt, **kwargs):  # noqa: ANN001
            session = kwargs["session"]
            run = RunStore().start(
                surface="dashboard",
                kind="dashboard",
                session_id=session.id,
                prompt=prompt,
            )
            RunStore().finish(run["id"], status="ok", result="late ok")
            return types.SimpleNamespace(
                text="late ok",
                session=session,
                trace_id="trace_late_cancel",
                turn_id="turn_late_cancel",
                run_id=run["id"],
            )

    sent = []
    cancel_event = threading.Event()
    cancel_event.set()

    final = _dashboard_chat_stream(
        {"message": "slow", "session_id": "dash:late-cancel"},
        Runner(),
        sent.append,
        on_agent=lambda _agent: None,
        cancel_event=cancel_event,
    )

    stored = RunStore().get(final["run_id"])
    assert final["type"] == "cancelled"
    assert final["cancelled"] is True
    assert sent[-1]["type"] == "cancelled"
    assert stored["status"] == "cancelled"
    assert stored["error"] == "client disconnected"
    assert stored["data"]["cancelled"] is True


def test_fastapi_session_checks_reports_cross_session_integrity(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    headers = {"X-Aegis-Token": "t"}

    from aegis.runs import RunStore
    from aegis.session import Session, SessionStore
    from aegis.types import Message

    session = Session(id="dash-check-session", title="dashboard check")
    session.messages = [Message.user("check"), Message.assistant("ok")]
    run = RunStore().start(surface="dashboard", kind="chat", session_id=session.id, prompt="check")
    RunStore().finish(run["id"], status="ok", result="ok")
    session.meta["last_run_id"] = run["id"]
    SessionStore().save(session)

    res = asyncio.run(_request(
        app,
        "GET",
        "/api/session-checks?session_limit=20&run_limit=20&stale_resume_pending_seconds=42",
        headers=headers,
    ))
    alias = asyncio.run(_request(app, "GET", "/api/harness/cross-session", headers=headers))

    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["counts"]["sessions_with_last_run"] == 1
    assert body["limits"]["stale_resume_pending_seconds"] == 42.0
    assert any(check["id"] == "session_run_links" for check in body["checks"])
    assert any(check["id"] == "resume_pending" for check in body["checks"])
    assert alias.status_code == 200
    assert alias.json()["ok"] is True


def test_fastapi_session_checks_repair_marks_resume_pending(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    headers = {"X-Aegis-Token": "t"}

    from aegis.runs import RunStore
    from aegis.session import Session, SessionStore

    store = SessionStore()
    runs = RunStore()
    session = Session(id="dash-repair-session", title="dashboard repair")
    store.save(session)
    run = runs.start(surface="dashboard", kind="chat", session_id=session.id, prompt="stale")
    run["started_at"] = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    runs.write(run)

    repair = asyncio.run(_request(
        app,
        "POST",
        "/api/session-checks/repair",
        json={
            "session_limit": 20,
            "run_limit": 20,
            "stale_running_seconds": 0,
            "reason": "dashboard_repair",
        },
        headers=headers,
    ))
    alias = asyncio.run(_request(
        app,
        "POST",
        "/api/harness/cross-session",
        json={"action": "report", "session_limit": 20, "run_limit": 20},
        headers=headers,
    ))

    body = repair.json()

    assert repair.status_code == 200
    assert body["object"] == "hermes.cross_session_integrity_repair_result"
    assert body["repair"]["object"] == "hermes.cross_session_integrity_repair"
    assert body["repair"]["repaired_running_runs"] == 1
    assert body["repair"]["marked_resume_pending"] == 1
    assert body["report"]["object"] == "hermes.cross_session_integrity_report"
    assert runs.get(run["id"])["status"] == "interrupted"
    assert store.load(session.id).meta["resume_reason"] == "dashboard_repair"
    assert alias.status_code == 200
    assert alias.json()["object"] == "hermes.cross_session_integrity_report"


def test_fastapi_session_checks_repair_false_only_reports(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    headers = {"X-Aegis-Token": "t"}

    from aegis.runs import RunStore
    from aegis.session import Session, SessionStore

    store = SessionStore()
    runs = RunStore()
    session = Session(id="dash-report-session", title="dashboard report")
    store.save(session)
    run = runs.start(surface="dashboard", kind="chat", session_id=session.id, prompt="stale")
    run["started_at"] = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    runs.write(run)

    report = asyncio.run(_request(
        app,
        "POST",
        "/api/session-checks",
        json={
            "repair": "false",
            "session_limit": 20,
            "run_limit": 20,
            "stale_running_seconds": 0,
        },
        headers=headers,
    ))

    body = report.json()

    assert report.status_code == 200
    assert body["object"] == "hermes.cross_session_integrity_report"
    assert "stale_running_run" in {issue["code"] for issue in body["issues"]}
    assert runs.get(run["id"])["status"] == "running"
    assert store.load(session.id).meta.get("resume_pending") is None


def test_fastapi_webhooks_status_redacts_security_posture(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    headers = {"X-Aegis-Token": "t"}

    from aegis.webhook import WebhookStore

    WebhookStore().add(
        "ci",
        "summarize {body}",
        secret="super-secret",
        deliver="telegram:1, discord:2",
        events=["push"],
        skills=["github-review"],
    )

    legacy = asyncio.run(_request(app, "GET", "/api/webhooks", headers=headers))
    status = asyncio.run(_request(app, "GET", "/api/webhooks/status", headers=headers))

    assert legacy.status_code == 200
    assert legacy.json() == [{"name": "ci", "prompt": "summarize {body}"}]
    assert status.status_code == 200
    body = status.json()
    rendered = json.dumps(body)
    hook = body["hooks"][0]
    assert body["ok"] is True
    assert body["count"] == 1
    assert hook["name"] == "ci"
    assert hook["secret_configured"] is True
    assert hook["deliver"] == ["telegram:1", "discord:2"]
    assert hook["events"] == ["push"]
    assert hook["skills"] == ["github-review"]
    assert "super-secret" not in rendered
    assert body["security"]["rate_limit_per_minute"] >= 1
    assert "X-Hub-Signature-256" in body["security"]["signature_schemes"]
    assert body["runtime"]["active"] in {True, False}
    assert body["runtime"]["delivery_cache"]["max_items"] >= 1
    assert body["runtime"]["delivery_cache"]["entries"] >= 0
    assert body["runtime"]["delivery_cache"]["accepted_count"] >= 0
    assert body["runtime"]["delivery_cache"]["duplicate_count"] >= 0
    assert body["runtime"]["delivery_cache"]["pruned_expired"] >= 0
    assert body["runtime"]["delivery_cache"]["pruned_capacity"] >= 0
    assert body["runtime"]["rate_limiter"]["limit"] >= 0
    assert body["runtime"]["rate_limiter"]["allowed_count"] >= 0
    assert body["runtime"]["rate_limiter"]["limited_count"] >= 0
    assert body["runtime"]["rate_limiter"]["pruned_windows"] >= 0


def test_fastapi_config_preferences_memory_provider_and_plugins(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    headers = {"X-Aegis-Token": "t"}

    exported = asyncio.run(_request(app, "GET", "/api/config/export", headers=headers))
    assert exported.status_code == 200
    assert exported.json()["ok"] is True
    assert "attachment" in exported.headers["content-disposition"]
    assert "config" in exported.json()

    prefs = asyncio.run(_request(app, "GET", "/api/dashboard/preferences", headers=headers))
    assert prefs.status_code == 200
    assert prefs.json()["theme"] == "system"
    assert prefs.json()["tool_progress_grouping"] == "accumulate"
    assert prefs.json()["tool_progress_style"] == "accumulate"
    assert prefs.json()["memory_notifications"] == "on"
    assert prefs.json()["platforms"] == {}

    updated = asyncio.run(_request(
        app,
        "PUT",
        "/api/dashboard/preferences",
        json={
            "theme": "dark",
            "tool_progress": "detailed",
            "tool_progress_grouping": "separate",
            "memory_notifications": "verbose",
            "platforms": {
                "Telegram": {
                    "tool_progress_style": "SEPARATE",
                    "memory_notifications": False,
                    "ignored": "value",
                }
            },
        },
        headers=headers,
    ))
    assert updated.status_code == 200
    assert updated.json()["preferences"]["theme"] == "dark"
    assert updated.json()["preferences"]["tool_progress"] == "detailed"
    assert updated.json()["preferences"]["tool_progress_grouping"] == "separate"
    assert updated.json()["preferences"]["tool_progress_style"] == "separate"
    assert updated.json()["preferences"]["memory_notifications"] == "verbose"
    assert updated.json()["preferences"]["platforms"] == {
        "telegram": {
            "tool_progress_grouping": "separate",
            "memory_notifications": "off",
        }
    }

    legacy = asyncio.run(_request(
        app,
        "PUT",
        "/api/dashboard/preferences",
        json={"tool_progress_style": "accumulate", "memory_notifications": False},
        headers=headers,
    ))
    assert legacy.status_code == 200
    assert legacy.json()["preferences"]["tool_progress_grouping"] == "accumulate"
    assert legacy.json()["preferences"]["tool_progress_style"] == "accumulate"
    assert legacy.json()["preferences"]["memory_notifications"] == "off"

    invalid = asyncio.run(_request(
        app,
        "PUT",
        "/api/dashboard/preferences",
        json={"tool_progress_grouping": "chatty", "memory_notifications": "loud"},
        headers=headers,
    ))
    assert invalid.status_code == 200
    assert invalid.json()["preferences"]["tool_progress_grouping"] == "accumulate"
    assert invalid.json()["preferences"]["memory_notifications"] == "on"

    providers = asyncio.run(_request(app, "GET", "/api/memory/providers", headers=headers))
    assert providers.status_code == 200
    assert any(row["name"] == "jsonl" for row in providers.json()["provider_catalog"])

    jsonl = asyncio.run(_request(app, "GET", "/api/memory/providers/jsonl", headers=headers))
    assert jsonl.status_code == 200
    assert jsonl.json()["name"] == "jsonl"

    setup = asyncio.run(_request(app, "GET", "/api/memory/providers/jsonl/setup", headers=headers))
    assert setup.status_code == 200
    assert setup.json()["known"] is True

    schema = asyncio.run(_request(app, "GET", "/api/memory/providers/jsonl/schema", headers=headers))
    assert schema.status_code == 200
    assert schema.json()["properties"]["memory.provider"]["const"] == "jsonl"

    plugins = asyncio.run(_request(app, "GET", "/api/plugins", headers=headers))
    assert plugins.status_code == 200
    assert "plugins" in plugins.json()
    assert "errors" in plugins.json()


def test_fastapi_dashboard_plugins_manifest_assets_and_api(tmp_path, monkeypatch):
    plug = tmp_path / "plugins" / "demo"
    (plug / "dashboard" / "dist").mkdir(parents=True)
    (plug / "plugin.json").write_text(
        '{"name":"demo","version":"1.0","description":"Demo plugin"}',
        encoding="utf-8",
    )
    (plug / "dashboard" / "manifest.json").write_text(
        json.dumps({
            "name": "demo-panel",
            "title": "Demo Panel",
            "tab": {"label": "Demo"},
            "slots": [{"slot": "overview", "component": "Demo"}],
            "entry": "index.js",
            "css": ["style.css"],
            "api": "api.py",
        }),
        encoding="utf-8",
    )
    (plug / "dashboard" / "dist" / "index.js").write_text("window.demoPlugin = true;", encoding="utf-8")
    (plug / "dashboard" / "dist" / "style.css").write_text(".demo{}", encoding="utf-8")
    (plug / "dashboard" / "dist" / "secret.py").write_text("print('no')", encoding="utf-8")
    (plug / "dashboard" / "api.py").write_text(
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        "@router.get('/ping')\n"
        "def ping():\n"
        "    return {'pong': True}\n",
        encoding="utf-8",
    )

    app = _app(tmp_path, monkeypatch)
    headers = {"X-Aegis-Token": "t"}

    manifest = asyncio.run(_request(app, "GET", "/api/dashboard/plugins", headers=headers))
    assert manifest.status_code == 200
    rows = manifest.json()
    assert rows[0]["name"] == "demo-panel"
    assert rows[0]["entry"] == "index.js"
    assert rows[0]["css"] == ["style.css"]
    assert rows[0]["has_api"] is True

    denied_asset = asyncio.run(_request(app, "GET", "/dashboard-plugins/demo-panel/index.js"))
    assert denied_asset.status_code == 401
    asset = asyncio.run(_request(app, "GET", "/dashboard-plugins/demo-panel/index.js", headers=headers))
    assert asset.status_code == 200
    assert "window.demoPlugin" in asset.text
    assert asset.headers["Cache-Control"] == "private, max-age=300"
    root = asyncio.run(_request(app, "GET", "/"))
    cookie_asset = asyncio.run(_request(
        app,
        "GET",
        "/dashboard-plugins/demo-panel/index.js",
        cookies={"aegis_dashboard_token": root.cookies["aegis_dashboard_token"]},
    ))
    assert cookie_asset.status_code == 200

    source = asyncio.run(_request(app, "GET", "/dashboard-plugins/demo-panel/secret.py", headers=headers))
    traversal = asyncio.run(_request(app, "GET", "/dashboard-plugins/demo-panel/../api.py", headers=headers))
    assert source.status_code == 404
    assert traversal.status_code == 404

    denied = asyncio.run(_request(app, "GET", "/api/plugins/demo-panel/ping"))
    allowed = asyncio.run(_request(app, "GET", "/api/plugins/demo-panel/ping", headers=headers))
    assert denied.status_code == 401
    assert allowed.status_code == 200
    assert allowed.json() == {"pong": True}

    observed = asyncio.run(_request(app, "GET", "/api/dashboard/plugins", headers=headers))
    observed_row = next(item for item in observed.json() if item["name"] == "demo-panel")
    mount = observed_row["api_mount"]
    assert mount["request_count"] == 1
    assert mount["last_request_path"] == "/api/plugins/demo-panel/ping"
    assert mount["last_request_method"] == "GET"
    assert mount["last_request_at"]
    assert mount["mount_count"] == 1
    assert mount["mount_error_count"] == 0
    assert mount["mounted_at"]
    assert mount["mount_duration_ms"] >= 0
    assert mount["fingerprint"]


def test_fastapi_dashboard_plugin_duplicate_names_report_conflict_and_do_not_mount_api(tmp_path, monkeypatch):
    for slug in ("alpha", "beta"):
        plug = tmp_path / "plugins" / slug
        (plug / "dashboard" / "dist").mkdir(parents=True)
        (plug / "dashboard" / "manifest.json").write_text(
            json.dumps({
                "name": "dup-panel",
                "title": f"{slug.title()} Panel",
                "entry": "dist/index.js",
                "api": "plugin_api.py",
            }),
            encoding="utf-8",
        )
        (plug / "dashboard" / "dist" / "index.js").write_text(f"window.{slug}=true;", encoding="utf-8")
        (plug / "dashboard" / "plugin_api.py").write_text(
            "from fastapi import APIRouter\n"
            "router = APIRouter()\n"
            "@router.get('/ping')\n"
            "def ping():\n"
            f"    return {{'plugin': '{slug}'}}\n",
            encoding="utf-8",
        )

    app = _app(tmp_path, monkeypatch)
    headers = {"X-Aegis-Token": "t"}

    manifest = asyncio.run(_request(app, "GET", "/api/dashboard/plugins", headers=headers))
    assert manifest.status_code == 200
    rows = [row for row in manifest.json() if row["name"] == "dup-panel"]
    assert len(rows) == 2
    assert all(row["name_conflict"] is True for row in rows)
    assert all("duplicate dashboard plugin name" in row["errors"][0] for row in rows)
    assert all(row["api_mount"]["status"] == "error" for row in rows)
    assert all(row["api_mount"]["mounted"] is False for row in rows)
    assert all("duplicate dashboard plugin name" in row["api_mount"]["error"] for row in rows)

    route = asyncio.run(_request(app, "GET", "/api/plugins/dup-panel/ping", headers=headers))
    assert route.status_code == 404

    hub = asyncio.run(_request(app, "GET", "/api/dashboard/plugins/hub", headers=headers))
    assert hub.status_code == 200
    orphan_rows = [row for row in hub.json()["orphan_dashboard_plugins"] if row["name"] == "dup-panel"]
    assert len(orphan_rows) == 2
    assert all(row["name_conflict"] is True for row in orphan_rows)


def test_fastapi_dashboard_plugin_invalid_manifest_reports_error(tmp_path, monkeypatch):
    plug = tmp_path / "plugins" / "broken"
    (plug / "dashboard").mkdir(parents=True)
    (plug / "dashboard" / "manifest.json").write_text("{bad", encoding="utf-8")

    app = _app(tmp_path, monkeypatch)
    headers = {"X-Aegis-Token": "t"}

    manifest = asyncio.run(_request(app, "GET", "/api/dashboard/plugins", headers=headers))
    assert manifest.status_code == 200
    row = next(item for item in manifest.json() if item["name"] == "broken")
    assert row["status"] == "error"
    assert row["manifest_error"] is True
    assert "manifest.json" in row["errors"][0]
    assert row["api_mount"]["status"] == "error"
    assert row["api_mount"]["mounted"] is False
    assert "manifest.json" in row["api_mount"]["error"]

    plugins = asyncio.run(_request(app, "GET", "/api/plugins", headers=headers))
    assert plugins.status_code == 200
    assert plugins.json()["dashboard_api_mounts"]["broken"]["status"] == "error"

    hub = asyncio.run(_request(app, "GET", "/api/dashboard/plugins/hub", headers=headers))
    assert hub.status_code == 200
    hub_row = next(item for item in hub.json()["plugins"] if item["key"] == "broken")
    assert hub_row["runtime_status"] == "error"
    assert hub_row["dashboard_manifest"]["manifest_error"] is True


def test_fastapi_dashboard_only_plugins_are_discovered_and_mounted(tmp_path, monkeypatch):
    plug = tmp_path / "plugins" / "status"
    (plug / "dashboard" / "dist").mkdir(parents=True)
    (plug / "dashboard" / "manifest.json").write_text(
        json.dumps({
            "name": "status-panel",
            "title": "Status Panel",
            "description": "Dashboard only",
            "entry": "dist/index.js",
            "api": "plugin_api.py",
        }),
        encoding="utf-8",
    )
    (plug / "dashboard" / "dist" / "index.js").write_text("window.statusPanel = true;", encoding="utf-8")
    (plug / "dashboard" / "plugin_api.py").write_text(
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        "@router.get('/status')\n"
        "def status():\n"
        "    return {'dashboard_only': True}\n"
        "def register(api):\n"
        "    class T:\n"
        "        name = 'dashboard_only_agent_tool'\n"
        "    api.register_tool(T())\n",
        encoding="utf-8",
    )

    app = _app(tmp_path, monkeypatch)
    headers = {"X-Aegis-Token": "t"}

    manifest = asyncio.run(_request(app, "GET", "/api/dashboard/plugins", headers=headers))
    assert manifest.status_code == 200
    row = next(item for item in manifest.json() if item["name"] == "status-panel")
    assert row["plugin"] == "status"
    assert row["key"] == "status"
    assert row["kind"] == "dashboard"
    assert row["source"] == "user"
    assert row["has_api"] is True

    hub = asyncio.run(_request(app, "GET", "/api/dashboard/plugins/hub", headers=headers))
    assert hub.status_code == 200
    hub_row = next(item for item in hub.json()["plugins"] if item["key"] == "status")
    assert hub_row["runtime_status"] == "dashboard"
    assert hub_row["has_dashboard_manifest"] is True
    assert hub_row["dashboard_manifest"]["name"] == "status-panel"
    assert any(item["name"] == "status-panel" for item in hub.json()["orphan_dashboard_plugins"])

    unauth_asset = asyncio.run(_request(app, "GET", "/dashboard-plugins/status-panel/dist/index.js"))
    assert unauth_asset.status_code == 401

    asset = asyncio.run(_request(app, "GET", "/dashboard-plugins/status-panel/dist/index.js", headers=headers))
    assert asset.status_code == 200
    assert "window.statusPanel" in asset.text

    route = asyncio.run(_request(app, "GET", "/api/plugins/status-panel/status", headers=headers))
    assert route.status_code == 200
    assert route.json() == {"dashboard_only": True}

    from aegis import plugins as plugin_runtime
    from aegis.config import Config

    plugin_runtime.clear_runtime_cache()
    api = plugin_runtime.load_plugins(config=Config.load())
    assert "dashboard_only_agent_tool" not in {getattr(tool, "name", "") for tool in api.tools}


def test_fastapi_dashboard_plugin_api_must_stay_under_dashboard_dir(tmp_path, monkeypatch):
    plug = tmp_path / "plugins" / "unsafe"
    (plug / "dashboard").mkdir(parents=True)
    (plug / "plugin.json").write_text('{"name":"unsafe"}', encoding="utf-8")
    (plug / "dashboard" / "manifest.json").write_text(
        json.dumps({"name": "unsafe-panel", "api": "api.py"}),
        encoding="utf-8",
    )
    (plug / "api.py").write_text(
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        "@router.get('/ping')\n"
        "def ping():\n"
        "    return {'unsafe': True}\n",
        encoding="utf-8",
    )

    app = _app(tmp_path, monkeypatch)
    headers = {"X-Aegis-Token": "t"}

    manifest = asyncio.run(_request(app, "GET", "/api/dashboard/plugins", headers=headers))
    row = next(item for item in manifest.json() if item["name"] == "unsafe-panel")
    assert row["has_api"] is False
    route = asyncio.run(_request(app, "GET", "/api/plugins/unsafe-panel/ping", headers=headers))
    assert route.status_code == 404


def test_fastapi_dashboard_plugin_yaml_manifest_normalized_tab_and_dashboard_api(tmp_path, monkeypatch):
    plug = tmp_path / "plugins" / "analytics" / "pulse"
    (plug / "dashboard" / "dist").mkdir(parents=True)
    (plug / ".git").mkdir()
    (plug / "plugin.yaml").write_text(
        "name: pulse\n"
        "version: 2.1.0\n"
        "description: Pulse dashboard plugin\n"
        "kind: backend\n"
        "author: AEGIS\n",
        encoding="utf-8",
    )
    (plug / "__init__.py").write_text("def register(api):\n    pass\n", encoding="utf-8")
    (plug / "dashboard" / "manifest.json").write_text(
        json.dumps({
            "name": "pulse-panel",
            "label": "Pulse",
            "description": "Live pulse",
            "icon": "Activity",
            "tab": {
                "path": "/pulse",
                "position": "after:sessions",
                "override": "/overview",
                "hidden": True,
            },
            "slots": ["overview.header", {"bad": True}, ""],
            "entry": "dist/index.js",
            "css": ["dist/style.css"],
            "api": "plugin_api.py",
        }),
        encoding="utf-8",
    )
    (plug / "dashboard" / "dist" / "index.js").write_text("window.pulse = true;", encoding="utf-8")
    (plug / "dashboard" / "dist" / "style.css").write_text(".pulse{}", encoding="utf-8")
    (plug / "dashboard" / "plugin_api.py").write_text(
        "from fastapi import APIRouter, HTTPException\n"
        "router = APIRouter()\n"
        "@router.get('/pulse')\n"
        "def pulse():\n"
        "    return {'pulse': True}\n"
        "@router.get('/fail')\n"
        "def fail():\n"
        "    raise HTTPException(status_code=503, detail='plugin unavailable')\n",
        encoding="utf-8",
    )

    app = _app(tmp_path, monkeypatch)
    headers = {"X-Aegis-Token": "t"}

    plugins = asyncio.run(_request(app, "GET", "/api/plugins", headers=headers))
    assert plugins.status_code == 200
    status = next(row for row in plugins.json()["plugin_status"] if row["key"] == "analytics/pulse")
    assert status["kind"] == "backend"
    assert status["source"] == "user"
    assert status["status"] == "loaded"

    hub = asyncio.run(_request(app, "GET", "/api/dashboard/plugins/hub", headers=headers))
    assert hub.status_code == 200
    hub_body = hub.json()
    hub_row = next(row for row in hub_body["plugins"] if row["key"] == "analytics/pulse")
    assert hub_row["runtime_status"] == "enabled"
    assert hub_row["load_status"] == "loaded"
    assert hub_row["load_duration_ms"] >= 0
    assert hub_row["loaded_at"]
    assert hub_row["runtime_contributions"] == {
        "tools": [],
        "channels": [],
        "providers": [],
        "hooks": [],
        "middleware": [],
    }
    assert hub_row["has_dashboard_manifest"] is True
    assert hub_row["dashboard_manifest"]["name"] == "pulse-panel"
    assert hub_row["dashboard_route"]["path"] == "/overview"
    assert hub_row["api_mount"]["status"] == "mounted"
    assert hub_row["can_remove"] is True
    assert hub_row["can_update_git"] is True
    assert "jsonl" in hub_body["providers"]["memory_options"]
    assert "default" in hub_body["providers"]["context_options"]
    assert hub_body["orphan_dashboard_plugins"] == []

    rescan = asyncio.run(_request(app, "POST", "/api/dashboard/plugins/rescan", headers=headers))
    assert rescan.status_code == 200
    assert rescan.json()["ok"] is True
    assert rescan.json()["count"] >= 1

    detail = asyncio.run(_request(app, "GET", "/api/dashboard/agent-plugins/analytics/pulse", headers=headers))
    assert detail.status_code == 200
    assert detail.json()["plugin"]["name"] == "pulse"

    manifest = asyncio.run(_request(app, "GET", "/api/dashboard/plugins", headers=headers))
    assert manifest.status_code == 200
    row = next(item for item in manifest.json() if item["name"] == "pulse-panel")
    assert row["label"] == "Pulse"
    assert row["icon"] == "Activity"
    assert row["source"] == "user"
    assert row["key"] == "analytics/pulse"
    assert row["tab"] == {
        "path": "/pulse",
        "position": "after:sessions",
        "override": "/overview",
        "hidden": True,
    }
    assert row["slots"] == ["overview.header"]
    assert row["entry"] == "dist/index.js"
    assert row["css"] == ["dist/style.css"]
    assert row["has_api"] is True
    assert row["route"] == {
        "path": "/overview",
        "label": "Pulse",
        "plugin": "pulse-panel",
        "hidden": True,
        "position": "after:sessions",
        "override": "/overview",
    }
    assert row["api_mount"]["status"] == "mounted"
    assert row["api_mounted"] is True
    assert "/api/plugins/pulse-panel/pulse" in row["api_routes"]
    assert row["api_compat_root"] is False
    assert row["ui_asset_status"]["status"] == "ok"
    assert row["ui_asset_status"]["entry_exists"] is True
    assert row["asset_errors"] == []

    asset = asyncio.run(_request(app, "GET", "/dashboard-plugins/pulse-panel/dist/index.js", headers=headers))
    assert asset.status_code == 200
    assert "window.pulse" in asset.text

    route = asyncio.run(_request(app, "GET", "/api/plugins/pulse-panel/pulse", headers=headers))
    assert route.status_code == 200
    assert route.json() == {"pulse": True}
    failed_route = asyncio.run(_request(app, "GET", "/api/plugins/pulse-panel/fail", headers=headers))
    assert failed_route.status_code == 503
    assert failed_route.json()["detail"] == "plugin unavailable"
    observed = asyncio.run(_request(app, "GET", "/api/plugins", headers=headers))
    assert observed.status_code == 200
    observed_body = observed.json()
    assert observed_body["dashboard_plugin_count"] >= 1
    assert observed_body["dashboard_api_route_count"] >= 1
    assert any(row["name"] == "pulse-panel" for row in observed_body["dashboard_plugins"])
    pulse_mount = observed_body["dashboard_api_mounts"]["pulse-panel"]
    assert pulse_mount["mounted"] is True
    assert "/api/plugins/pulse-panel/pulse" in pulse_mount["routes"]
    assert pulse_mount["mount_count"] == 1
    assert pulse_mount["mount_error_count"] == 0
    assert pulse_mount["mounted_at"]
    assert pulse_mount["mount_duration_ms"] >= 0
    assert pulse_mount["fingerprint"]
    assert pulse_mount["request_count"] >= 2
    assert pulse_mount["success_count"] >= 1
    assert pulse_mount["error_count"] == 1
    assert pulse_mount["last_request_path"] == "/api/plugins/pulse-panel/fail"
    assert pulse_mount["last_request_method"] == "GET"
    assert pulse_mount["last_success_at"]
    assert pulse_mount["last_error_at"]
    assert pulse_mount["last_error_path"] == "/api/plugins/pulse-panel/fail"
    assert pulse_mount["last_error_method"] == "GET"
    assert pulse_mount["last_error_type"] == "HTTPException"
    assert pulse_mount["last_error"] == "plugin unavailable"
    observed_hub = asyncio.run(_request(app, "GET", "/api/dashboard/plugins/hub", headers=headers))
    assert observed_hub.status_code == 200
    observed_hub_row = next(row for row in observed_hub.json()["plugins"] if row["key"] == "analytics/pulse")
    hub_mount = observed_hub_row["api_mount"]
    assert hub_mount["mounted"] is True
    assert hub_mount["request_count"] >= 2
    assert hub_mount["success_count"] >= 1
    assert hub_mount["error_count"] == 1
    assert hub_mount["last_error_path"] == "/api/plugins/pulse-panel/fail"
    assert hub_mount["last_error_type"] == "HTTPException"
    assert hub_mount["last_error"] == "plugin unavailable"
    assert observed_hub_row["ui_asset_status"]["status"] == "ok"
    observability = asyncio.run(_request(app, "GET", "/api/observability/contract", headers=headers))
    assert observability.status_code == 200
    observed_mount = observability.json()["dashboard_plugin_api_mounts"]["pulse-panel"]
    assert observed_mount["mounted"] is True
    assert observed_mount["request_count"] >= 2
    assert observed_mount["error_count"] == 1
    assert observed_mount["last_request_path"] == "/api/plugins/pulse-panel/fail"
    assert observed_mount["last_error"] == "plugin unavailable"
    assert observability.json()["dashboard_plugins"]["api_mounted_count"] >= 1
    assert observability.json()["dashboard_plugins"]["api_route_count"] >= 1
    assert observability.json()["dashboard_plugins"]["api_request_error_count"] >= 1
    assert observability.json()["dashboard_plugins"]["api_error_count"] >= 1
    assert observability.json()["dashboard_plugins"]["ui_assets"]["pulse-panel"]["status"] == "ok"
    assert observability.json()["routes"]["dashboard_plugin_hub"] == "/api/dashboard/plugins/hub"

    disabled = asyncio.run(_request(app, "POST", "/api/plugins/analytics/pulse/disable", headers=headers))
    assert disabled.status_code == 200
    assert "analytics/pulse" in disabled.json()["disabled"]


    disabled_hub = asyncio.run(_request(app, "GET", "/api/dashboard/plugins/hub", headers=headers))
    assert disabled_hub.status_code == 200
    disabled_row = next(row for row in disabled_hub.json()["plugins"] if row["key"] == "analytics/pulse")
    assert disabled_row["runtime_status"] == "disabled"
    assert disabled_row["has_dashboard_manifest"] is True
    disabled_route = asyncio.run(_request(app, "GET", "/api/plugins/pulse-panel/pulse", headers=headers))
    assert disabled_route.status_code == 404

    enabled = asyncio.run(_request(app, "POST", "/api/dashboard/agent-plugins/analytics/pulse/enable", headers=headers))
    assert enabled.status_code == 200
    assert "analytics/pulse" in enabled.json()["enabled"]
    enabled_route = asyncio.run(_request(app, "GET", "/api/plugins/pulse-panel/pulse", headers=headers))
    assert enabled_route.status_code == 200
    assert enabled_route.json() == {"pulse": True}

    providers_saved = asyncio.run(_request(
        app,
        "PUT",
        "/api/dashboard/plugin-providers",
        json={"memory_provider": "jsonl", "context_engine": "default"},
        headers=headers,
    ))
    assert providers_saved.status_code == 200
    assert providers_saved.json()["providers"]["memory_provider"] == "jsonl"
    assert providers_saved.json()["providers"]["context_engine"] == "default"

    hidden = asyncio.run(_request(
        app,
        "POST",
        "/api/dashboard/plugins/pulse-panel/visibility",
        json={"hidden": True},
        headers=headers,
    ))
    assert hidden.status_code == 200
    hidden_hub_row = next(row for row in hidden.json()["plugins"] if row["key"] == "analytics/pulse")
    assert hidden_hub_row["user_hidden"] is True
    hidden_manifest = asyncio.run(_request(app, "GET", "/api/dashboard/plugins", headers=headers))
    assert all(item["name"] != "pulse-panel" for item in hidden_manifest.json())

    shown = asyncio.run(_request(
        app,
        "POST",
        "/api/dashboard/plugins/pulse-panel/visibility",
        json={"hidden": False},
        headers=headers,
    ))
    assert shown.status_code == 200
    shown_hub_row = next(row for row in shown.json()["plugins"] if row["key"] == "analytics/pulse")
    assert shown_hub_row["user_hidden"] is False
    shown_manifest = asyncio.run(_request(app, "GET", "/api/dashboard/plugins", headers=headers))
    assert any(item["name"] == "pulse-panel" for item in shown_manifest.json())

    import aegis.dashboard_fastapi as dash_api

    def fake_git_pull(cmd, **kwargs):  # noqa: ANN001
        assert cmd[:3] == ["git", "-C", str(plug)]
        assert cmd[-2:] == ["pull", "--ff-only"]
        assert kwargs["check"] is False
        return types.SimpleNamespace(returncode=0, stdout="Already up to date.\n", stderr="")

    monkeypatch.setattr(dash_api.subprocess, "run", fake_git_pull)
    updated = asyncio.run(_request(
        app,
        "POST",
        "/api/dashboard/agent-plugins/analytics/pulse/update",
        headers=headers,
    ))
    assert updated.status_code == 200
    assert updated.json()["ok"] is True
    assert updated.json()["unchanged"] is True


def test_fastapi_dashboard_plugin_reports_missing_ui_entry_without_blocking_api(tmp_path, monkeypatch):
    plug = tmp_path / "plugins" / "broken-ui"
    (plug / "dashboard").mkdir(parents=True)
    (plug / "plugin.yaml").write_text(
        "name: broken-ui\n"
        "version: 1.0.0\n"
        "description: Broken UI still has API\n",
        encoding="utf-8",
    )
    (plug / "__init__.py").write_text("def register(api):\n    pass\n", encoding="utf-8")
    (plug / "dashboard" / "manifest.json").write_text(
        json.dumps({
            "name": "broken-ui-panel",
            "label": "Broken UI",
            "entry": "dist/missing.js",
            "css": ["dist/missing.css"],
            "api": "plugin_api.py",
        }),
        encoding="utf-8",
    )
    (plug / "dashboard" / "plugin_api.py").write_text(
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        "@router.get('/ping')\n"
        "def ping():\n"
        "    return {'ok': True}\n",
        encoding="utf-8",
    )

    app = _app(tmp_path, monkeypatch)
    headers = {"X-Aegis-Token": "t"}

    manifest = asyncio.run(_request(app, "GET", "/api/dashboard/plugins", headers=headers))
    assert manifest.status_code == 200
    row = next(item for item in manifest.json() if item["name"] == "broken-ui-panel")
    assert row["has_api"] is True
    assert row["api_mount"]["status"] == "mounted"
    assert row["ui_asset_status"]["status"] == "error"
    assert row["ui_asset_status"]["missing"] == ["dist/missing.js", "dist/missing.css"]
    assert "missing entry asset: dist/missing.js" in row["asset_errors"]

    api_route = asyncio.run(_request(app, "GET", "/api/plugins/broken-ui-panel/ping", headers=headers))
    assert api_route.status_code == 200
    assert api_route.json() == {"ok": True}

    hub = asyncio.run(_request(app, "GET", "/api/dashboard/plugins/hub", headers=headers))
    assert hub.status_code == 200
    hub_row = next(item for item in hub.json()["plugins"] if item["key"] == "broken-ui")
    assert hub_row["api_mount"]["mounted"] is True
    assert hub_row["ui_asset_status"]["status"] == "error"

    observability = asyncio.run(_request(app, "GET", "/api/observability/contract", headers=headers))
    assert observability.status_code == 200
    dashboard_plugins = observability.json()["dashboard_plugins"]
    assert dashboard_plugins["ui_assets"]["broken-ui-panel"]["status"] == "error"
    assert dashboard_plugins["ui_asset_error_plugin_count"] >= 1
    assert dashboard_plugins["ui_asset_error_count"] >= 2


def test_fastapi_dashboard_plugin_config_enabled_flag_disables_dashboard_mount(tmp_path, monkeypatch):
    plug = tmp_path / "plugins" / "analytics" / "pulse"
    (plug / "dashboard" / "dist").mkdir(parents=True)
    (plug / "plugin.yaml").write_text(
        "name: pulse\n"
        "version: 2.1.0\n"
        "description: Pulse dashboard plugin\n"
        "kind: backend\n",
        encoding="utf-8",
    )
    (plug / "__init__.py").write_text("def register(api):\n    pass\n", encoding="utf-8")
    (plug / "dashboard" / "manifest.json").write_text(
        json.dumps({
            "name": "pulse-panel",
            "label": "Pulse",
            "entry": "dist/index.js",
            "api": "plugin_api.py",
        }),
        encoding="utf-8",
    )
    (plug / "dashboard" / "dist" / "index.js").write_text("window.pulse = true;", encoding="utf-8")
    (plug / "dashboard" / "plugin_api.py").write_text(
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        "@router.get('/pulse')\n"
        "def pulse():\n"
        "    return {'pulse': True}\n",
        encoding="utf-8",
    )
    (tmp_path / "config.yaml").write_text(
        "dashboard:\n"
        "  plugins:\n"
        "    pulse-panel:\n"
        "      enabled: false\n",
        encoding="utf-8",
    )

    app = _app(tmp_path, monkeypatch)
    headers = {"X-Aegis-Token": "t"}

    manifest = asyncio.run(_request(app, "GET", "/api/dashboard/plugins", headers=headers))
    assert manifest.status_code == 200
    assert all(item["name"] != "pulse-panel" for item in manifest.json())

    hub = asyncio.run(_request(app, "GET", "/api/dashboard/plugins/hub", headers=headers))
    assert hub.status_code == 200
    hub_row = next(row for row in hub.json()["plugins"] if row["key"] == "analytics/pulse")
    assert hub_row["has_dashboard_manifest"] is True
    assert hub_row["dashboard_enabled"] is False
    assert hub_row["dashboard_manifest"] is None
    assert hub_row["api_mount"] is None

    asset = asyncio.run(_request(app, "GET", "/dashboard-plugins/pulse-panel/dist/index.js", headers=headers))
    assert asset.status_code == 404
    route = asyncio.run(_request(app, "GET", "/api/plugins/pulse-panel/pulse", headers=headers))
    assert route.status_code == 404


def test_fastapi_dashboard_plugin_embedded_yaml_dashboard_manifest(tmp_path, monkeypatch):
    plug = tmp_path / "plugins" / "ops" / "brief"
    (plug / "dashboard" / "dist").mkdir(parents=True)
    (plug / "plugin.yaml").write_text(
        "name: brief\n"
        "version: 1.2.3\n"
        "description: Embedded dashboard metadata\n"
        "kind: backend\n"
        "dashboard:\n"
        "  name: brief-panel\n"
        "  label: Brief\n"
        "  icon: ClipboardList\n"
        "  tab:\n"
        "    path: /brief\n"
        "  entry: dist/index.js\n"
        "  css:\n"
        "    - dist/brief.css\n"
        "  api: plugin_api.py\n",
        encoding="utf-8",
    )
    (plug / "__init__.py").write_text("def register(api):\n    pass\n", encoding="utf-8")
    (plug / "dashboard" / "dist" / "index.js").write_text("window.brief = true;", encoding="utf-8")
    (plug / "dashboard" / "dist" / "brief.css").write_text(".brief{}", encoding="utf-8")
    (plug / "dashboard" / "plugin_api.py").write_text(
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        "@router.get('/brief')\n"
        "def brief():\n"
        "    return {'brief': True}\n",
        encoding="utf-8",
    )

    app = _app(tmp_path, monkeypatch)
    headers = {"X-Aegis-Token": "t"}

    manifest = asyncio.run(_request(app, "GET", "/api/dashboard/plugins", headers=headers))
    assert manifest.status_code == 200
    row = next(item for item in manifest.json() if item["name"] == "brief-panel")
    assert row["plugin"] == "brief"
    assert row["key"] == "ops/brief"
    assert row["label"] == "Brief"
    assert row["icon"] == "ClipboardList"
    assert row["tab"]["path"] == "/brief"
    assert row["entry"] == "dist/index.js"
    assert row["css"] == ["dist/brief.css"]
    assert row["has_api"] is True
    assert row["api_mount"]["status"] == "mounted"
    assert "/api/plugins/brief-panel/brief" in row["api_routes"]

    hub = asyncio.run(_request(app, "GET", "/api/dashboard/plugins/hub", headers=headers))
    assert hub.status_code == 200
    hub_row = next(item for item in hub.json()["plugins"] if item["key"] == "ops/brief")
    assert hub_row["has_dashboard_manifest"] is True
    assert hub_row["dashboard_manifest"]["name"] == "brief-panel"
    assert hub_row["api_mount"]["status"] == "mounted"

    asset = asyncio.run(_request(app, "GET", "/dashboard-plugins/brief-panel/dist/index.js", headers=headers))
    assert asset.status_code == 200
    assert "window.brief" in asset.text
    route = asyncio.run(_request(app, "GET", "/api/plugins/brief-panel/brief", headers=headers))
    assert route.status_code == 200
    assert route.json() == {"brief": True}


def test_fastapi_entrypoint_plugin_package_dashboard_manifest_mounts(tmp_path, monkeypatch):
    package_base = tmp_path / "site-packages"
    package = package_base / "entrydash"
    (package / "dashboard" / "dist").mkdir(parents=True)
    (package / "__init__.py").write_text("def register(api):\n    pass\n", encoding="utf-8")
    (package / "plugin.yaml").write_text(
        "name: entry-dash\n"
        "version: 1.0.0\n"
        "description: Entrypoint dashboard package\n"
        "kind: dashboard\n",
        encoding="utf-8",
    )
    (package / "dashboard" / "manifest.json").write_text(
        json.dumps({
            "name": "entry-panel",
            "label": "Entry Panel",
            "entry": "dist/index.js",
            "api": "plugin_api.py",
        }),
        encoding="utf-8",
    )
    (package / "dashboard" / "dist" / "index.js").write_text("window.entryPanel = true;", encoding="utf-8")
    (package / "dashboard" / "plugin_api.py").write_text(
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        "@router.get('/ping')\n"
        "def ping():\n"
        "    return {'entrypoint': True}\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(package_base))

    import aegis.plugins as plugin_runtime
    from aegis.config import Config
    from aegis.dashboard_fastapi import create_app

    ep = types.SimpleNamespace(name="entry-dash", value="entrydash:register", group="hermes_agent.plugins")

    class FakeEntryPoints(list):
        def select(self, *, group):
            return [item for item in self if item.group == group]

    monkeypatch.setattr(plugin_runtime.importlib_metadata, "entry_points", lambda: FakeEntryPoints([ep]))
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    monkeypatch.setenv("AEGIS_DASHBOARD_TOKEN", "t")
    cfg = Config.load()
    cfg.data.setdefault("plugins", {})["enabled"] = ["entry-dash"]
    app = create_app(cfg)
    headers = {"X-Aegis-Token": "t"}

    plugins = asyncio.run(_request(app, "GET", "/api/plugins", headers=headers))
    manifest = asyncio.run(_request(app, "GET", "/api/dashboard/plugins", headers=headers))
    asset = asyncio.run(_request(app, "GET", "/dashboard-plugins/entry-panel/dist/index.js", headers=headers))
    route = asyncio.run(_request(app, "GET", "/api/plugins/entry-panel/ping", headers=headers))
    hub = asyncio.run(_request(app, "GET", "/api/dashboard/plugins/hub", headers=headers))

    assert plugins.status_code == 200
    status = next(row for row in plugins.json()["plugin_status"] if row["name"] == "entry-dash")
    assert status["source"] == "entrypoint"
    assert status["status"] == "loaded"
    assert manifest.status_code == 200
    row = next(item for item in manifest.json() if item["name"] == "entry-panel")
    assert row["plugin"] == "entry-dash"
    assert row["source"] == "entrypoint"
    assert row["has_api"] is True
    assert row["api_mount"]["status"] == "mounted"
    assert asset.status_code == 200
    assert "window.entryPanel" in asset.text
    assert route.status_code == 200
    assert route.json() == {"entrypoint": True}
    assert hub.status_code == 200
    hub_row = next(item for item in hub.json()["plugins"] if item["name"] == "entry-dash")
    assert hub_row["has_dashboard_manifest"] is True
    assert hub_row["dashboard_manifest"]["name"] == "entry-panel"


def test_fastapi_dashboard_plugin_install_mounts_api_without_restart(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    headers = {"X-Aegis-Token": "t"}
    source = tmp_path / "source_plugins" / "live"
    (source / "dashboard").mkdir(parents=True)
    (source / "plugin.json").write_text(
        json.dumps({"name": "live-plugin", "version": "1.0.0"}),
        encoding="utf-8",
    )
    (source / "dashboard" / "manifest.json").write_text(
        json.dumps({
            "name": "live-panel",
            "title": "Live Panel",
            "entry": "dist/index.js",
            "api": "plugin_api.py",
        }),
        encoding="utf-8",
    )
    (source / "dashboard" / "plugin_api.py").write_text(
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        "@router.get('/ping')\n"
        "def ping():\n"
        "    return {'live': True}\n",
        encoding="utf-8",
    )

    missing = asyncio.run(_request(app, "GET", "/api/plugins/live-panel/ping", headers=headers))
    install = asyncio.run(_request(
        app,
        "POST",
        "/api/plugins/install",
        json={"source": str(source)},
        headers=headers,
    ))
    route = asyncio.run(_request(app, "GET", "/api/plugins/live-panel/ping", headers=headers))
    disabled = asyncio.run(_request(app, "POST", "/api/plugins/live-plugin/disable", headers=headers))
    disabled_route = asyncio.run(_request(app, "GET", "/api/plugins/live-panel/ping", headers=headers))
    enabled = asyncio.run(_request(app, "POST", "/api/plugins/live-plugin/enable", headers=headers))
    enabled_route = asyncio.run(_request(app, "GET", "/api/plugins/live-panel/ping", headers=headers))
    installed_api = Path(install.json()["target"]) / "dashboard" / "plugin_api.py"
    installed_api.write_text(
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        "@router.get('/ping')\n"
        "def ping():\n"
        "    return {'live': False, 'version': 2}\n",
        encoding="utf-8",
    )
    rescan = asyncio.run(_request(app, "POST", "/api/dashboard/plugins/rescan", headers=headers))
    reloaded_route = asyncio.run(_request(app, "GET", "/api/plugins/live-panel/ping", headers=headers))

    assert missing.status_code == 404
    assert install.status_code == 200
    assert install.json()["name"] == "live-plugin"
    assert route.status_code == 200
    assert route.json() == {"live": True}
    assert disabled.status_code == 200
    assert disabled_route.status_code == 404
    assert enabled.status_code == 200
    assert enabled_route.status_code == 200
    assert enabled_route.json() == {"live": True}
    assert rescan.status_code == 200
    assert reloaded_route.status_code == 200
    assert reloaded_route.json() == {"live": False, "version": 2}


def test_fastapi_dashboard_agent_plugin_install_supports_git_identifier(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    headers = {"X-Aegis-Token": "t"}

    import aegis.plugins as plugin_runtime

    clone_calls: list[list[str]] = []

    def fake_git_clone(cmd, **kwargs):  # noqa: ANN001
        clone_calls.append(list(cmd))
        assert cmd[1:5] == ["clone", "--depth", "1", "https://github.com/alien/remote-pulse.git"]
        assert kwargs["check"] is False
        clone_root = Path(cmd[-1])
        (clone_root / ".git").mkdir(parents=True)
        (clone_root / "dashboard").mkdir()
        (clone_root / "plugin.yaml").write_text(
            "name: remote-pulse\n"
            "version: 1.0.0\n"
            "kind: backend\n"
            "requires_env:\n"
            "  - REMOTE_PULSE_TOKEN\n",
            encoding="utf-8",
        )
        (clone_root / "__init__.py").write_text("def register(api):\n    pass\n", encoding="utf-8")
        (clone_root / "dashboard" / "manifest.json").write_text(
            json.dumps({
                "name": "remote-pulse-panel",
                "entry": "dist/index.js",
                "api": "plugin_api.py",
            }),
            encoding="utf-8",
        )
        (clone_root / "dashboard" / "plugin_api.py").write_text(
            "from fastapi import APIRouter\n"
            "router = APIRouter()\n"
            "@router.get('/ping')\n"
            "def ping():\n"
            "    return {'remote': True}\n",
            encoding="utf-8",
        )
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(plugin_runtime.subprocess, "run", fake_git_clone)

    validated = asyncio.run(_request(
        app,
        "POST",
        "/api/plugins/validate",
        json={"source": "alien/remote-pulse"},
        headers=headers,
    ))
    installed = asyncio.run(_request(
        app,
        "POST",
        "/api/dashboard/agent-plugins/install",
        json={"identifier": "alien/remote-pulse", "force": "false", "enable": "true"},
        headers=headers,
    ))
    route = asyncio.run(_request(app, "GET", "/api/plugins/remote-pulse-panel/ping", headers=headers))
    duplicate = asyncio.run(_request(
        app,
        "POST",
        "/api/dashboard/agent-plugins/install",
        json={"identifier": "alien/remote-pulse", "force": "false"},
        headers=headers,
    ))

    assert validated.status_code == 200
    assert validated.json()["kind"] == "git"
    assert validated.json()["git_url"] == "https://github.com/alien/remote-pulse.git"
    assert installed.status_code == 200
    body = installed.json()
    assert body["plugin_name"] == "remote-pulse"
    assert body["source"] == "git"
    assert body["install_url"] == "https://github.com/alien/remote-pulse.git"
    assert body["missing_env"] == ["REMOTE_PULSE_TOKEN"]
    row = next(item for item in body["plugins"] if item["name"] == "remote-pulse")
    assert row["source"] == "git"
    assert row["installed_from"] == "alien/remote-pulse"
    assert row["install_url"] == "https://github.com/alien/remote-pulse.git"
    assert row["trusted"] is False
    assert row["can_remove"] is True
    assert row["can_update_git"] is True
    assert route.status_code == 200
    assert route.json() == {"remote": True}
    assert duplicate.status_code == 400
    assert "already exists" in duplicate.json()["error"]
    assert len(clone_calls) == 2


def test_fastapi_audio_control_plane(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    headers = {"X-Aegis-Token": "t"}

    from aegis.tools.base import ToolResult

    monkeypatch.setattr(
        "aegis.tools.voice.SpeakTool.run",
        lambda self, args, ctx: ToolResult.ok("saved speech to /tmp/speech.mp3", display="tts"),
    )
    monkeypatch.setattr(
        "aegis.tools.voice.TranscribeTool.run",
        lambda self, args, ctx: ToolResult.ok("transcribed words", display="transcribed"),
    )

    voices = asyncio.run(_request(app, "GET", "/api/audio/voices", headers=headers))
    assert voices.status_code == 200
    assert "alloy" in voices.json()["voices"]

    tts = asyncio.run(_request(
        app,
        "POST",
        "/api/audio/tts",
        json={"text": "hello", "voice": "alloy"},
        headers=headers,
    ))
    assert tts.status_code == 200
    assert tts.json()["ok"] is True
    assert "speech" in tts.json()["content"]

    transcribed = asyncio.run(_request(
        app,
        "POST",
        "/api/audio/transcribe",
        data={"model": "whisper-1"},
        files={"file": ("clip.wav", b"RIFF", "audio/wav")},
        headers=headers,
    ))
    assert transcribed.status_code == 200
    assert transcribed.json()["text"] == "transcribed words"


def test_fastapi_cron_control_plane(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    headers = {"X-Aegis-Token": "t"}

    import aegis.cron as cron
    from aegis.daemon import ServiceResult

    monkeypatch.setattr(
        cron,
        "run_job",
        lambda _config, job, **_kw: {
            "ok": True,
            "job_id": job,
            "run_id": "run_typed",
            "session_id": f"cron:{job}",
        },
    )
    monkeypatch.setattr("aegis.daemon.cron_service_status", lambda: "active (running, enabled)")
    monkeypatch.setattr("aegis.daemon.control_cron_service", lambda action: ServiceResult(True, f"cron {action}"))

    create = asyncio.run(_request(
        app,
        "POST",
        "/api/cron/jobs",
        json={
            "name": "Dashboard digest",
            "schedule": "every 2h",
            "prompt": "ship a dashboard digest",
            "deliver": "telegram:42",
            "skills": ["summarize"],
            "model": "cron-model",
            "enabled_toolsets": ["core", "web"],
            "workdir": str(tmp_path),
        },
        headers=headers,
    ))
    assert create.status_code == 200
    job_id = create.json()["id"]
    assert create.json()["job"]["enabled"] is True
    assert create.json()["job"]["name"] == "Dashboard digest"
    assert create.json()["job"]["model"] == "cron-model"
    assert create.json()["job"]["enabled_toolsets"] == ["core", "web"]
    assert create.json()["job"]["workdir"] == str(tmp_path.resolve())

    bad_workdir = asyncio.run(_request(
        app,
        "POST",
        "/api/cron/jobs",
        json={
            "schedule": "every 2h",
            "prompt": "bad cwd",
            "workdir": "relative/path",
        },
        headers=headers,
    ))
    assert bad_workdir.status_code == 400
    assert "absolute existing directory" in bad_workdir.json()["error"]

    bad_no_agent = asyncio.run(_request(
        app,
        "POST",
        "/api/cron/jobs",
        json={
            "schedule": "every 2h",
            "prompt": "scriptless",
            "no_agent": True,
        },
        headers=headers,
    ))
    assert bad_no_agent.status_code == 400
    assert "no_agent jobs require a script" in bad_no_agent.json()["error"]

    string_false_no_agent = asyncio.run(_request(
        app,
        "POST",
        "/api/cron/jobs",
        json={
            "schedule": "every 2h",
            "prompt": "string false stays agentic",
            "no_agent": "false",
        },
        headers=headers,
    ))
    assert string_false_no_agent.status_code == 200
    string_false_job_id = string_false_no_agent.json()["id"]
    assert string_false_no_agent.json()["job"]["no_agent"] is False

    alias_bad_workdir = asyncio.run(_request(
        app,
        "POST",
        "/api/jobs",
        json={
            "schedule": "every 2h",
            "prompt": "bad alias cwd",
            "workdir": "relative/path",
        },
        headers=headers,
    ))
    assert alias_bad_workdir.status_code == 400
    assert "absolute existing directory" in alias_bad_workdir.json()["error"]

    legacy_bad_no_agent = asyncio.run(_request(
        app,
        "POST",
        "/api/cron",
        json={
            "action": "add",
            "schedule": "every 2h",
            "prompt": "legacy scriptless",
            "no_agent": True,
        },
        headers=headers,
    ))
    assert legacy_bad_no_agent.status_code == 200
    assert legacy_bad_no_agent.json()["ok"] is False
    assert "no_agent jobs require a script" in legacy_bad_no_agent.json()["error"]

    legacy_string_false = asyncio.run(_request(
        app,
        "POST",
        "/api/cron",
        json={
            "action": "add",
            "schedule": "every 2h",
            "prompt": "legacy string false stays agentic",
            "no_agent": "false",
        },
        headers=headers,
    ))
    assert legacy_string_false.status_code == 200
    legacy_string_false_id = legacy_string_false.json()["id"]

    jobs = asyncio.run(_request(app, "GET", "/api/cron/jobs", headers=headers))
    assert any(job["id"] == job_id and job["name"] == "Dashboard digest" for job in jobs.json()["jobs"])
    alias_jobs = asyncio.run(_request(app, "GET", "/api/jobs", headers=headers))
    assert alias_jobs.status_code == 200
    assert any(job["id"] == job_id for job in alias_jobs.json()["jobs"])
    assert any(job["id"] == job_id for job in alias_jobs.json()["data"])

    delivery_targets = asyncio.run(_request(app, "GET", "/api/cron/delivery-targets", headers=headers))
    assert delivery_targets.status_code == 200
    assert any(target["id"] == "local" for target in delivery_targets.json()["targets"])

    alias_create = asyncio.run(_request(
        app,
        "POST",
        "/api/jobs",
        json={
            "name": "Alias digest",
            "schedule": "every 4h",
            "prompt": "ship alias digest",
            "toolsets": "core",
        },
        headers=headers,
    ))
    assert alias_create.status_code == 200
    alias_job_id = alias_create.json()["id"]
    assert alias_create.json()["job"]["enabled_toolsets"] == ["core"]

    patch = asyncio.run(_request(
        app,
        "PATCH",
        f"/api/cron/jobs/{job_id}",
        json={
            "enabled": False,
            "schedule": "every 3h",
            "name": "Paused digest",
            "model": "cron-updated",
            "enabled_toolsets": ["core"],
        },
        headers=headers,
    ))
    assert patch.status_code == 200
    assert patch.json()["job"]["enabled"] is False
    assert patch.json()["job"]["schedule"] == "every 3h"
    assert patch.json()["job"]["name"] == "Paused digest"
    assert patch.json()["job"]["model"] == "cron-updated"
    assert patch.json()["job"]["enabled_toolsets"] == ["core"]

    put_patch = asyncio.run(_request(
        app,
        "PUT",
        f"/api/cron/jobs/{job_id}",
        json={"updates": {"name": "Put digest", "deliver": "local"}},
        headers=headers,
    ))
    assert put_patch.status_code == 200
    assert put_patch.json()["job"]["name"] == "Put digest"
    assert put_patch.json()["job"]["deliver"] == "local"

    bad_patch_workdir = asyncio.run(_request(
        app,
        "PATCH",
        f"/api/cron/jobs/{job_id}",
        json={"workdir": str(tmp_path / "missing")},
        headers=headers,
    ))
    assert bad_patch_workdir.status_code == 400
    assert "workdir not found" in bad_patch_workdir.json()["error"]

    bad_patch_no_agent = asyncio.run(_request(
        app,
        "PATCH",
        f"/api/cron/jobs/{job_id}",
        json={"no_agent": True},
        headers=headers,
    ))
    assert bad_patch_no_agent.status_code == 400
    assert "no_agent jobs require a script" in bad_patch_no_agent.json()["error"]

    alias_detail = asyncio.run(_request(app, "GET", f"/api/jobs/{job_id}", headers=headers))
    assert alias_detail.status_code == 200
    assert alias_detail.json()["job"]["id"] == job_id

    alias_pause = asyncio.run(_request(app, "POST", f"/api/jobs/{job_id}/pause", headers=headers))
    assert alias_pause.status_code == 200
    assert alias_pause.json()["paused"] is True
    assert alias_pause.json()["job"]["enabled"] is False

    alias_resume = asyncio.run(_request(app, "POST", f"/api/jobs/{job_id}/resume", headers=headers))
    assert alias_resume.status_code == 200
    assert alias_resume.json()["paused"] is False
    assert alias_resume.json()["job"]["enabled"] is True

    run = asyncio.run(_request(app, "POST", f"/api/cron/jobs/{job_id}/run", headers=headers))
    assert run.status_code == 200
    assert run.json()["run_id"] == "run_typed"
    alias_run = asyncio.run(_request(app, "POST", f"/api/jobs/{job_id}/run", headers=headers))
    assert alias_run.status_code == 200
    assert alias_run.json()["run_id"] == "run_typed"

    runs = asyncio.run(_request(app, "GET", f"/api/cron/jobs/{job_id}/runs?limit=3", headers=headers))
    assert runs.status_code == 200
    assert runs.json()["id"] == job_id
    assert runs.json()["limit"] == 3
    assert "runs" in runs.json()

    service = asyncio.run(_request(
        app,
        "POST",
        "/api/cron/service",
        json={"action": "restart"},
        headers=headers,
    ))
    assert service.status_code == 200
    assert service.json() == {"ok": True, "message": "cron restart"}

    string_false_delete = asyncio.run(_request(app, "DELETE", f"/api/cron/jobs/{string_false_job_id}", headers=headers))
    assert string_false_delete.status_code == 200
    legacy_string_false_delete = asyncio.run(
        _request(app, "DELETE", f"/api/cron/jobs/{legacy_string_false_id}", headers=headers)
    )
    assert legacy_string_false_delete.status_code == 200
    delete = asyncio.run(_request(app, "DELETE", f"/api/cron/jobs/{job_id}", headers=headers))
    assert delete.status_code == 200
    assert delete.json()["ok"] is True
    alias_delete = asyncio.run(_request(app, "DELETE", f"/api/jobs/{alias_job_id}", headers=headers))
    assert alias_delete.status_code == 200
    assert alias_delete.json()["ok"] is True


def test_fastapi_cron_job_routes_reject_invalid_ids(tmp_path, monkeypatch, caplog):
    app = _app(tmp_path, monkeypatch)
    headers = {
        "X-Aegis-Token": "t",
        "X-Forwarded-For": "203.0.113.9",
        "User-Agent": "aegis-dashboard-test",
    }
    caplog.set_level(logging.WARNING, logger="aegis.dashboard_fastapi")

    calls = [
        ("GET", "/api/jobs/not-a-valid-hex!", {}),
        ("PATCH", "/api/cron/jobs/not-a-valid-hex!", {"json": {}}),
        ("DELETE", "/api/cron/jobs/not-a-valid-hex!", {}),
        ("POST", "/api/jobs/not-a-valid-hex!/pause", {}),
        ("POST", "/api/jobs/not-a-valid-hex!/run", {}),
        ("GET", "/api/cron/jobs/not-a-valid-hex!/runs", {}),
    ]
    results = [
        asyncio.run(_request(app, method, path, headers=headers, **kwargs))
        for method, path, kwargs in calls
    ]

    for response in results:
        payload = response.json()
        assert response.status_code == 400
        assert payload["ok"] is False
        assert payload["code"] == "invalid_job_id"
        assert "Invalid" in payload["error"]
    logs = "\n".join(record.getMessage() for record in caplog.records)
    assert "Cron jobs API rejected invalid job_id" in logs
    assert "203.0.113.9" in logs
    assert "aegis-dashboard-test" in logs


def test_fastapi_gateway_control_plane(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    headers = {"X-Aegis-Token": "t"}

    from aegis.daemon import ServiceResult

    control_actions: list[str] = []
    monkeypatch.setattr("aegis.daemon.gateway_service_status", lambda: "inactive (dead, disabled)")
    monkeypatch.setattr(
        "aegis.daemon.install_gateway_service",
        lambda _config, channels, enable_now=True: ServiceResult(True, f"installed {','.join(channels)}"),
    )
    monkeypatch.setattr(
        "aegis.daemon.control_gateway_service",
        lambda action: control_actions.append(action) or ServiceResult(True, f"gateway {action}"),
    )
    monkeypatch.setattr(
        "aegis.providers.registry.provider_report",
        lambda _config: {
            "active": {
                "name": "openai",
                "model": "gpt-status",
                "context_length": 128000,
                "capabilities": {"fast_mode": True},
            }
        },
    )

    set_channels = asyncio.run(_request(
        app,
        "POST",
        "/api/gateway/channels",
        json={"channels": "telegram,discord"},
        headers=headers,
    ))
    assert set_channels.status_code == 200
    assert set_channels.json()["gateway"]["channels"] == ["telegram", "discord"]

    status = asyncio.run(_request(app, "GET", "/api/gateway/status", headers=headers))
    assert status.status_code == 200
    assert status.json()["configured"] is True
    assert status.json()["service"] == "inactive (dead, disabled)"
    assert status.json()["provider"] == "openai"
    assert status.json()["model"] == "gpt-status"
    assert status.json()["context_length"] == 128000
    assert status.json()["capabilities"]["fast_mode"] is True
    assert "reasoning_effort" in status.json()
    assert "service_tier" in status.json()

    install = asyncio.run(_request(
        app,
        "POST",
        "/api/gateway/service",
        json={"action": "install", "channels": ["telegram"], "no_start": True},
        headers=headers,
    ))
    assert install.status_code == 200
    assert install.json() == {"ok": True, "message": "installed telegram"}

    restart = asyncio.run(_request(
        app,
        "POST",
        "/api/gateway/service",
        json={"action": "restart"},
        headers=headers,
    ))
    assert restart.status_code == 200
    assert restart.json() == {"ok": True, "message": "gateway restart"}

    for action in ("start", "stop", "restart"):
        alias = asyncio.run(_request(app, "POST", f"/api/gateway/{action}", headers=headers))
        assert alias.status_code == 200
        assert alias.json() == {"ok": True, "message": f"gateway {action}"}
    assert control_actions[-4:] == ["restart", "start", "stop", "restart"]


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
    from aegis.dashboard_pty import dashboard_terminal_argv

    monkeypatch.setenv("AEGIS_BIN", "/tmp/aegis-test")
    assert dashboard_terminal_argv("sess") == ["/tmp/aegis-test", "chat", "--resume", "sess"]
