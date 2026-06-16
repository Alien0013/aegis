from __future__ import annotations

import asyncio
import base64
import json
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

    res = asyncio.run(_request(app, "GET", "/api/status"))
    assert res.status_code == 401

    res = asyncio.run(_request(app, "GET", "/"))
    assert res.status_code == 200
    assert "aegis_dashboard_token" in res.headers.get("set-cookie", "")

    res = asyncio.run(_request(app, "GET", "/api/status", headers={"X-Aegis-Token": "t"}))
    assert res.status_code == 200


def test_fastapi_basic_login_session_and_logout(tmp_path, monkeypatch):
    app = _basic_app(tmp_path, monkeypatch)

    res = asyncio.run(_request(app, "GET", "/api/status"))
    assert res.status_code == 401

    login_page = asyncio.run(_request(app, "GET", "/login"))
    assert login_page.status_code == 200
    assert "AEGIS" in login_page.text

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
        "/api/auth/ws-ticket",
        "/api/config/schema",
        "/api/config/raw",
        "/api/env",
        "/api/browser/manage",
        "/api/sessions/search",
        "/api/sessions/stats",
        "/api/cron/jobs",
        "/api/cron/service",
        "/api/gateway/status",
        "/api/messaging/platforms",
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

    blocked = _dashboard_ws_rpc_response(
        '{"jsonrpc":"2.0","id":"bad","method":"dashboard.get","params":{"path":"/etc/passwd"}}',
        cfg,
    )
    assert blocked["error"]["code"] == -32602


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


def test_fastapi_messaging_platform_aliases(tmp_path, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
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
        json={"enabled": True, "env": {"TELEGRAM_BOT_TOKEN": "test-token"}},
        headers=headers,
    ))
    assert updated.status_code == 200
    assert updated.json()["platform"]["enabled"] is True
    assert updated.json()["platform"]["configured"] is True

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
        json={"enabled": False, "clear_env": ["TELEGRAM_BOT_TOKEN"]},
        headers=headers,
    ))
    assert cleared.status_code == 200
    assert cleared.json()["platform"]["state"] == "disabled"
    assert "TELEGRAM_BOT_TOKEN" in cleared.json()["platform"]["missing_env_vars"]


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

    tools = asyncio.run(_request(
        app,
        "POST",
        "/api/mcp/servers/local/tools",
        json={"include": ["read"]},
        headers=headers,
    ))
    assert tools.status_code == 200
    assert saved["local"] == ["read"]

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
    bulk_session = Session.create("bulk delete session")
    bulk_session.messages = [Message.user("bulk delete me")]
    store.save(bulk_session)

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

    pruned_empty = asyncio.run(_request(
        app,
        "POST",
        "/api/sessions/prune-empty",
        json={"dry_run": False},
        headers=headers,
    ))
    assert pruned_empty.status_code == 200
    assert empty_session.id in pruned_empty.json()["ids"]

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

    asset = asyncio.run(_request(app, "GET", "/dashboard-plugins/demo-panel/index.js"))
    assert asset.status_code == 200
    assert "window.demoPlugin" in asset.text

    source = asyncio.run(_request(app, "GET", "/dashboard-plugins/demo-panel/secret.py"))
    traversal = asyncio.run(_request(app, "GET", "/dashboard-plugins/demo-panel/../api.py"))
    assert source.status_code == 404
    assert traversal.status_code == 404

    denied = asyncio.run(_request(app, "GET", "/api/plugins/demo-panel/ping"))
    allowed = asyncio.run(_request(app, "GET", "/api/plugins/demo-panel/ping", headers=headers))
    assert denied.status_code == 401
    assert allowed.status_code == 200
    assert allowed.json() == {"pong": True}


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
        "    return {'dashboard_only': True}\n",
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

    asset = asyncio.run(_request(app, "GET", "/dashboard-plugins/status-panel/dist/index.js"))
    assert asset.status_code == 200
    assert "window.statusPanel" in asset.text

    route = asyncio.run(_request(app, "GET", "/api/plugins/status-panel/status", headers=headers))
    assert route.status_code == 200
    assert route.json() == {"dashboard_only": True}


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
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        "@router.get('/pulse')\n"
        "def pulse():\n"
        "    return {'pulse': True}\n",
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
    assert hub_row["has_dashboard_manifest"] is True
    assert hub_row["dashboard_manifest"]["name"] == "pulse-panel"
    assert hub_row["can_remove"] is True
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
    assert row["api_compat_root"] is False

    asset = asyncio.run(_request(app, "GET", "/dashboard-plugins/pulse-panel/dist/index.js"))
    assert asset.status_code == 200
    assert "window.pulse" in asset.text

    route = asyncio.run(_request(app, "GET", "/api/plugins/pulse-panel/pulse", headers=headers))
    assert route.status_code == 200
    assert route.json() == {"pulse": True}

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
        "    - dist/brief.css\n",
        encoding="utf-8",
    )
    (plug / "__init__.py").write_text("def register(api):\n    pass\n", encoding="utf-8")
    (plug / "dashboard" / "dist" / "index.js").write_text("window.brief = true;", encoding="utf-8")
    (plug / "dashboard" / "dist" / "brief.css").write_text(".brief{}", encoding="utf-8")

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

    hub = asyncio.run(_request(app, "GET", "/api/dashboard/plugins/hub", headers=headers))
    assert hub.status_code == 200
    hub_row = next(item for item in hub.json()["plugins"] if item["key"] == "ops/brief")
    assert hub_row["has_dashboard_manifest"] is True
    assert hub_row["dashboard_manifest"]["name"] == "brief-panel"

    asset = asyncio.run(_request(app, "GET", "/dashboard-plugins/brief-panel/dist/index.js"))
    assert asset.status_code == 200
    assert "window.brief" in asset.text


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

    jobs = asyncio.run(_request(app, "GET", "/api/cron/jobs", headers=headers))
    assert any(job["id"] == job_id and job["name"] == "Dashboard digest" for job in jobs.json()["jobs"])
    alias_jobs = asyncio.run(_request(app, "GET", "/api/jobs", headers=headers))
    assert alias_jobs.status_code == 200
    assert any(job["id"] == job_id for job in alias_jobs.json()["jobs"])
    assert any(job["id"] == job_id for job in alias_jobs.json()["data"])

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

    service = asyncio.run(_request(
        app,
        "POST",
        "/api/cron/service",
        json={"action": "restart"},
        headers=headers,
    ))
    assert service.status_code == 200
    assert service.json() == {"ok": True, "message": "cron restart"}

    delete = asyncio.run(_request(app, "DELETE", f"/api/cron/jobs/{job_id}", headers=headers))
    assert delete.status_code == 200
    assert delete.json()["ok"] is True
    alias_delete = asyncio.run(_request(app, "DELETE", f"/api/jobs/{alias_job_id}", headers=headers))
    assert alias_delete.status_code == 200
    assert alias_delete.json()["ok"] is True


def test_fastapi_gateway_control_plane(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    headers = {"X-Aegis-Token": "t"}

    from aegis.daemon import ServiceResult

    monkeypatch.setattr("aegis.daemon.gateway_service_status", lambda: "inactive (dead, disabled)")
    monkeypatch.setattr(
        "aegis.daemon.install_gateway_service",
        lambda _config, channels, enable_now=True: ServiceResult(True, f"installed {','.join(channels)}"),
    )
    monkeypatch.setattr(
        "aegis.daemon.control_gateway_service",
        lambda action: ServiceResult(True, f"gateway {action}"),
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
