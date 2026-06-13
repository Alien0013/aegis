from __future__ import annotations

import asyncio
import base64

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
        "/api/auth/ws-ticket",
        "/api/config/schema",
        "/api/config/raw",
        "/api/env",
        "/api/sessions/search",
        "/api/sessions/stats",
        "/api/cron/jobs",
        "/api/cron/service",
        "/api/gateway/status",
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


def test_fastapi_typed_config_profile_gateway_and_plugin_routes(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    headers = {"X-Aegis-Token": "t"}

    changed = asyncio.run(_request(
        app,
        "PATCH",
        "/api/config/fields",
        json={"updates": {"tools.exec_mode": "smart", "agent.compression.max_tool_tokens": 12000}},
        headers=headers,
    ))
    assert changed.status_code == 200
    body = changed.json()
    assert body["ok"] is True
    assert body["changed"]["tools.exec_mode"] == "smart"
    raw_config = asyncio.run(_request(app, "GET", "/api/config/raw", headers=headers)).json()["config"]
    assert raw_config["agent"]["compression"]["max_tool_tokens"] == 12000

    bad = asyncio.run(_request(
        app,
        "PATCH",
        "/api/config/fields",
        json={"updates": {"tools.exec_mode": "root"}},
        headers=headers,
    ))
    assert bad.status_code == 400
    assert "one of" in bad.json()["errors"]["tools.exec_mode"]

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
        json={"enabled": True},
        headers=headers,
    ))
    assert configured.status_code == 200
    telegram = configured.json()["channel"]
    assert telegram["id"] == "telegram"
    assert telegram["enabled"] is True

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

    updated = asyncio.run(_request(
        app,
        "PUT",
        "/api/dashboard/preferences",
        json={"theme": "dark", "tool_progress": "detailed"},
        headers=headers,
    ))
    assert updated.status_code == 200
    assert updated.json()["preferences"]["theme"] == "dark"
    assert updated.json()["preferences"]["tool_progress"] == "detailed"

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
        },
        headers=headers,
    ))
    assert create.status_code == 200
    job_id = create.json()["id"]
    assert create.json()["job"]["enabled"] is True
    assert create.json()["job"]["name"] == "Dashboard digest"

    jobs = asyncio.run(_request(app, "GET", "/api/cron/jobs", headers=headers))
    assert any(job["id"] == job_id and job["name"] == "Dashboard digest" for job in jobs.json()["jobs"])

    patch = asyncio.run(_request(
        app,
        "PATCH",
        f"/api/cron/jobs/{job_id}",
        json={"enabled": False, "schedule": "every 3h", "name": "Paused digest"},
        headers=headers,
    ))
    assert patch.status_code == 200
    assert patch.json()["job"]["enabled"] is False
    assert patch.json()["job"]["schedule"] == "every 3h"
    assert patch.json()["job"]["name"] == "Paused digest"

    run = asyncio.run(_request(app, "POST", f"/api/cron/jobs/{job_id}/run", headers=headers))
    assert run.status_code == 200
    assert run.json()["run_id"] == "run_typed"

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
