"""Dashboard is editable: env-token gating + the memory add/remove POST endpoint."""

from __future__ import annotations

import http.client
import json


def _serve(cfg):
    from _dashboard_server import serve_app
    return serve_app(cfg)


def _req(port, method, path, body=None, token="t"):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    h = {"X-Aegis-Token": token, "Content-Type": "application/json"} if token else {}
    c.request(method, path, json.dumps(body) if body is not None else None, h)
    r = c.getresponse()
    return r.status, json.loads(r.read() or b"{}")


def test_env_token_gates_dashboard(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    monkeypatch.setenv("AEGIS_DASHBOARD_TOKEN", "envtok")
    from aegis.config import Config
    from aegis.dashboard import _dashboard_token
    cfg = Config.load()
    assert _dashboard_token(cfg) == "envtok"          # env wins
    srv, port = _serve(cfg)
    try:
        assert _req(port, "GET", "/api/status", token=None)[0] == 401
        assert _req(port, "GET", "/api/status", token="envtok")[0] == 200
    finally:
        srv.shutdown()


def test_chat_event_row_carries_text_and_tool_id():
    """The Chat page needs streamed text, thinking, and paired tool ids to render
    the agent's words and live tool cards — _chat_event_row must preserve them."""
    from aegis.dashboard import _chat_event_row
    assert _chat_event_row({"type": "assistant_delta", "text": "hel"})["text"] == "hel"
    assert _chat_event_row({"type": "reasoning_delta", "text": "hmm"})["text"] == "hmm"
    start = _chat_event_row({"type": "tool_start", "id": "c1", "name": "bash",
                             "args": {"command": "ls -la"}})
    assert start["id"] == "c1" and start["target"] == "ls -la"
    assert start["args"] == {"command": "ls -la"}
    patch = "*** Begin Patch\n*** Update File: app.py\n@@\n-print('old')\n+print('new')\n*** End Patch"
    patch_start = _chat_event_row({"type": "tool_start", "id": "p1", "name": "apply_patch",
                                   "args": {"patch": patch}})
    assert patch_start["args"]["patch"] == patch
    edit_start = _chat_event_row({"type": "tool_start", "id": "e1", "name": "edit_file",
                                  "args": {"path": "app.py", "old_string": "old", "new_string": "new"}})
    assert edit_start["args"]["old_string"] == "old"
    assert edit_start["args"]["new_string"] == "new"
    single = _chat_event_row({"type": "tool_start", "id": "s1", "name": "spawn_subagent",
                              "args": {"task": "audit the API adapter"}})
    assert single["target"] == "audit the API adapter"
    batch = _chat_event_row({"type": "tool_start", "id": "s2", "name": "spawn_subagent",
                             "args": {"tasks": ["audit API", "check dashboard", "write tests"]}})
    assert batch["target"] == "3 tasks: audit API | check dashboard | write tests"
    aegis_batch = _chat_event_row({"type": "tool_start", "id": "s3", "name": "delegate_task",
                                    "args": {"tasks": [{"goal": "inspect gateway"},
                                                       {"goal": "verify cron"}]}})
    assert aegis_batch["target"] == "2 tasks: inspect gateway | verify cron"
    res = _chat_event_row({"type": "tool_result", "id": "c1", "name": "bash", "preview": "ok"})
    assert res["id"] == "c1" and res["status"] == "ok" and res["target"] == "ok"
    assert res["preview"] == "ok"
    err = _chat_event_row({"type": "tool_result", "id": "c2", "is_error": True})
    assert err["status"] == "error"
    it = _chat_event_row({"type": "iteration", "n": 2, "max": 30})
    assert it["n"] == 2 and it["max"] == 30


def test_dashboard_status_includes_operational_controls(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    from aegis.config import Config
    from aegis.dashboard import _dashboard_status

    cfg = Config.load()
    cfg.data["model"] = {"provider": "localtest", "default": "local-model"}
    cfg.data["custom_providers"] = [{
        "name": "localtest",
        "base_url": "http://local.test/v1",
        "api_mode": "chat_completions",
        "context_length": 70_000,
    }]
    cfg.data["tools"]["exec_mode"] = "ask"
    cfg.data["tools"]["toolsets"] = ["core"]
    cfg.data["display"]["reasoning"] = "live"
    cfg.data["agent"]["reasoning_effort"] = "high"
    cfg.data["agent"]["service_tier"] = "priority"
    cfg.data["gateway"]["busy_mode"] = "steer"

    data = _dashboard_status(cfg)

    assert data["context_length"] == 70_000
    assert data["exec_mode"] == "ask"
    assert data["toolsets"] == ["core"]
    assert data["reasoning_display"] == "live"
    assert data["reasoning_effort"] == "high"
    assert data["service_tier"] == "priority"
    assert data["busy_mode"] == "steer"
    assert data["learn"]["memory_every"] >= 1


def test_dashboard_cockpit_payload_aggregates_operator_surfaces(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    from aegis.config import Config
    from aegis.dashboard import _dashboard_cockpit

    cfg = Config.load()
    data = _dashboard_cockpit(cfg)

    assert data["status"]["model"]
    assert "sessions" in data
    assert "agents" in data and "agents" in data["agents"]
    assert "kanban" in data and "ready" in data["kanban"]
    assert "tools" in data and data["tools"]["tools"]
    assert "memory" in data and "user_entries" in data["memory"]
    assert "review" in data and "files" in data["review"]
    assert "logs" in data and "errors" in data["logs"]


def test_dashboard_tools_payload_includes_schema_and_policy(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    from aegis.config import Config
    from aegis.dashboard import _dashboard_tools

    cfg = Config.load()
    cfg.data.setdefault("tools", {})["toolsets"] = ["core"]
    cfg.data["tools"]["deny_groups"] = ["runtime"]
    cfg.data["tools"]["allowlist"] = ["git status"]

    payload = _dashboard_tools(cfg)

    assert payload["toolsets"] == ["core"]
    assert payload["deny_groups"] == ["runtime"]
    assert payload["allowlist"] == ["git status"]
    assert any(
        "schema" in row
        and "toolset" in row
        and "enabled" in row
        and "schema_hash" in row
        and "provenance" in row
        and "risk_level" in row
        for row in payload["tools"]
    )


def test_files_browser_lists_and_reads(tmp_path, monkeypatch):
    """Read-only file browser endpoints back the dashboard Files page."""
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    monkeypatch.setenv("AEGIS_DASHBOARD_TOKEN", "t")
    (tmp_path / "hello.txt").write_text("hi there")
    (tmp_path / "sub").mkdir()
    from aegis.config import Config
    srv, port = _serve(Config.load())
    try:
        st, b = _req(port, "GET", f"/api/files?path={tmp_path}")
        assert st == 200
        names = {e["name"] for e in b.get("entries", [])}
        assert "hello.txt" in names and "sub" in names
        st, b = _req(port, "GET", f"/api/files/read?path={tmp_path}/hello.txt")
        assert b.get("content") == "hi there"
        st, b = _req(port, "GET", f"/api/files/read?path={tmp_path}/nope.txt")
        assert b.get("error")
    finally:
        srv.shutdown()


def test_chat_meta_maps_reasoning_to_runtime_control():
    """The Chat 'Thinking' toggle sends a reasoning level that must become a
    session runtime control so the agent actually streams live reasoning."""
    from aegis.dashboard import _dashboard_chat_meta
    on = _dashboard_chat_meta({"reasoning": "medium"}, "/api/chat/stream")
    assert on["runtime_controls"] == {"reasoning_effort": "medium", "reasoning_display": "live"}
    off = _dashboard_chat_meta({"reasoning": "off"}, "/api/chat/stream")
    assert off["runtime_controls"] == {"reasoning_effort": "off", "reasoning_display": "off"}
    none = _dashboard_chat_meta({}, "/api/chat/stream")
    assert "runtime_controls" not in none
    bad = _dashboard_chat_meta({"reasoning": "bogus"}, "/api/chat/stream")
    assert "runtime_controls" not in bad


def test_chat_meta_maps_model_picker_to_session_runtime():
    """Composer model/provider picks are per-session runtime controls, not
    profile-default writes."""
    from aegis.dashboard import _dashboard_chat_meta

    meta = _dashboard_chat_meta(
        {"model": "gpt-session", "provider": "openai", "reasoning": "high"},
        "/api/chat/stream",
    )

    assert meta["model"] == "gpt-session"
    assert meta["provider"] == "openai"
    assert meta["runtime_controls"] == {
        "reasoning_effort": "high",
        "reasoning_display": "live",
        "model": "gpt-session",
        "provider": "openai",
    }
    assert meta["runtime"]["reasoning_effort"] == "high"


def test_chat_meta_maps_fast_to_service_tier():
    from aegis.dashboard import _dashboard_chat_meta, _dashboard_chat_runtime

    fast = _dashboard_chat_meta({"fast": True}, "/api/chat/stream")
    assert fast["runtime_controls"] == {"service_tier": "priority"}
    assert fast["runtime"] == {"service_tier": "priority"}
    assert _dashboard_chat_runtime({"fast": True}) == {"service_tier": "priority"}

    normal = _dashboard_chat_meta({"fast": False}, "/api/chat/stream")
    assert normal["runtime_controls"] == {"service_tier": "normal"}
    assert normal["runtime"] == {"service_tier": "normal"}
    assert _dashboard_chat_runtime({"fast": False}) == {"service_tier": "normal"}

    explicit = _dashboard_chat_meta({"service_tier": "priority"}, "/api/chat/stream")
    assert explicit["runtime_controls"] == {"service_tier": "priority"}
    assert _dashboard_chat_runtime({"service_tier": "priority"}) == {"service_tier": "priority"}


def test_memory_post_add_and_remove(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    monkeypatch.setenv("AEGIS_DASHBOARD_TOKEN", "t")
    from aegis.config import Config
    srv, port = _serve(Config.load())
    try:
        st, b = _req(port, "POST", "/api/memory", {"action": "add", "target": "user", "content": "Name: TJ"})
        assert st == 200 and "remembered" in b.get("result", "")
        st, b = _req(port, "GET", "/api/memory")
        assert "TJ" in b.get("user", "")
        st, b = _req(port, "POST", "/api/memory", {"action": "remove", "target": "user", "match": "TJ"})
        assert st == 200 and "removed" in b.get("result", "")
        # bad request
        assert _req(port, "POST", "/api/memory", {"action": "add", "target": "bogus", "content": "x"})[1].get("error")
    finally:
        srv.shutdown()
