"""Tool-result classification, cache-token tracking, and cost estimation."""

from __future__ import annotations


def test_tool_result_classification():
    from aegis.tools.base import ToolResult
    assert ToolResult.ok("done").classification == "success"
    assert ToolResult.error("boom").classification == "error"
    assert ToolResult.error("permission denied: bash").classification == "refused"
    assert ToolResult("output …[truncated]").classification == "truncated"
    assert ToolResult("(no output)").classification == "partial"


def test_usage_cache_fields_add():
    from aegis.types import Usage
    u = Usage(10, 5, 3, 2)
    u.add(Usage(1, 1, 1, 1))
    assert (u.input_tokens, u.output_tokens, u.cache_read, u.cache_write) == (11, 6, 4, 3)


def test_anthropic_parses_cache_tokens():
    import inspect
    from aegis.providers.anthropic import AnthropicTransport
    src = inspect.getsource(AnthropicTransport)
    assert "cache_read_input_tokens" in src and "cache_creation_input_tokens" in src


def test_cost_report_and_log():
    from aegis import usage_log
    from aegis.types import Usage
    usage_log.log("anthropic", "claude-sonnet-4-5", Usage(2000, 1000, 500))
    usage_log.log("openai", "gpt-4o", Usage(1000, 200, 0))
    r = usage_log.cost_report(30)
    assert r["calls"] == 2 and r["total_cost_usd"] > 0
    assert "claude-sonnet-4-5" in r["by_model"] and "gpt-4o" in r["by_model"]
    assert r["cache_read_tokens"] == 500


def test_agent_usage_log_records_cache_deltas(monkeypatch, tmp_path):
    from aegis import usage_log
    from aegis.agent.agent import Agent
    from aegis.config import Config
    from aegis.session import Session
    from aegis.types import LLMResponse, Usage
    from conftest import FakeProvider

    cfg = Config.load()
    cfg.data["memory"]["enabled"] = False
    logged: list[Usage] = []
    monkeypatch.setattr(usage_log, "log", lambda _provider, _model, usage: logged.append(usage))
    provider = FakeProvider([
        LLMResponse(text="one", usage=Usage(10, 3, 4, 2)),
        LLMResponse(text="two", usage=Usage(7, 2, 1, 1)),
    ])
    agent = Agent(config=cfg, provider=provider, session=Session.create(), cwd=tmp_path)

    agent.run("first")
    agent.run("second")

    assert [(u.input_tokens, u.output_tokens, u.cache_read, u.cache_write) for u in logged] == [
        (10, 3, 4, 2),
        (7, 2, 1, 1),
    ]


def test_provider_attempt_hooks_fire_for_success_and_error(tmp_path):
    import json
    from aegis.agent.agent import Agent
    from aegis.config import Config
    from aegis.hooks import EVENTS
    from aegis.plugins import PluginAPI, _HOOKS
    from aegis.session import Session
    from aegis.types import LLMResponse, Usage
    from conftest import FakeProvider

    assert {"pre_api_request", "post_api_request", "api_request_error"} <= set(EVENTS)

    original_hooks = {event: list(hooks) for event, hooks in _HOOKS.items()}
    _HOOKS.clear()
    events: list[tuple[str, dict]] = []
    api = PluginAPI()

    def record(event: str):
        def _hook(payload, _agent):
            if event == "pre_api_request":
                payload["request"]["mutated_by_hook"] = True
            events.append((event, payload))

        return _hook

    try:
        for event in ("pre_api_request", "post_api_request", "api_request_error"):
            api.register_hook(event, record(event))

        cfg = Config.load()
        cfg.data["memory"]["enabled"] = False
        cfg.data["hooks"] = {}
        provider = FakeProvider([LLMResponse(text="ok", usage=Usage(2, 1))])
        agent = Agent(config=cfg, provider=provider, session=Session.create(), cwd=tmp_path)

        agent.run("hello secret-token")

        assert [event for event, _payload in events] == ["pre_api_request", "post_api_request"]
        pre_payload = events[0][1]
        post_payload = events[1][1]
        assert pre_payload["api_request_id"].startswith("api_")
        assert pre_payload["session_id"] == agent.session.id
        assert pre_payload["turn_id"].startswith("turn_")
        assert pre_payload["provider"] == "fake"
        assert pre_payload["model"] == "fake-model"
        assert pre_payload["request"]["message_count"] >= 1
        assert pre_payload["request"]["tool_schema_count"] >= 0
        assert "secret-token" not in json.dumps(pre_payload["request"])
        assert post_payload["status"] == "ok"
        assert post_payload["response"]["input_tokens"] == 2
        assert post_payload["response"]["output_tokens"] == 1
        assert post_payload["response"]["duration_ms"] >= 0
        assert "mutated_by_hook" not in post_payload["request"]

        events.clear()

        class FailingProvider(FakeProvider):
            def complete(self, messages, tools=None, stream=False, on_delta=None):
                self.calls += 1
                raise RuntimeError("provider exploded")

        failing = FailingProvider()
        failed_agent = Agent(config=cfg, provider=failing, session=Session.create(), cwd=tmp_path)
        msg = failed_agent.run("explode")

        assert "[provider error]" in msg.content
        assert [event for event, _payload in events] == ["pre_api_request", "api_request_error"]
        error_payload = events[1][1]
        assert error_payload["status"] == "error"
        assert error_payload["error"]["type"] == "RuntimeError"
        assert error_payload["error"]["message"] == "provider exploded"
        assert error_payload["error"]["recovery"] == "retry"
        assert error_payload["error"]["duration_ms"] >= 0
    finally:
        _HOOKS.clear()
        _HOOKS.update(original_hooks)


def test_budget_exhausted_grace_fires_provider_hooks(tmp_path):
    from aegis.agent.agent import Agent
    from aegis.config import Config
    from aegis.plugins import PluginAPI, _HOOKS
    from aegis.session import Session
    from aegis.types import LLMResponse, ToolCall, Usage
    from conftest import FakeProvider

    original_hooks = {event: list(hooks) for event, hooks in _HOOKS.items()}
    _HOOKS.clear()
    events: list[tuple[str, dict]] = []
    api = PluginAPI()

    try:
        for event in ("pre_api_request", "post_api_request", "api_request_error"):
            api.register_hook(event, lambda payload, _agent, event=event: events.append((event, payload)))

        cfg = Config.load()
        cfg.data["memory"]["enabled"] = False
        cfg.data["hooks"] = {}
        provider = FakeProvider([
            LLMResponse(text="", tool_calls=[ToolCall("c1", "list_dir", {"path": "."})]),
            LLMResponse(text="summary", usage=Usage(input_tokens=5, output_tokens=2, cache_read=1)),
        ])
        agent = Agent(config=cfg, provider=provider, session=Session.create(), cwd=tmp_path)
        agent.budget.max_iterations = 1

        out = agent.run("loop forever")

        assert out.content == "summary"
        assert [event for event, _payload in events] == [
            "pre_api_request",
            "post_api_request",
            "pre_api_request",
            "post_api_request",
        ]
        grace_pre = events[2][1]
        grace_post = events[3][1]
        assert grace_pre["request"]["grace"] is True
        assert grace_pre["request"]["reason"] == "budget_exhausted"
        assert grace_pre["request"]["tools_enabled"] is False
        assert grace_pre["request"]["tool_schema_count"] == 0
        assert grace_post["status"] == "ok"
        assert grace_post["response"]["input_tokens"] == 5
        assert grace_post["response"]["output_tokens"] == 2
        assert grace_post["response"]["duration_ms"] >= 0
    finally:
        _HOOKS.clear()
        _HOOKS.update(original_hooks)


def test_cost_pricing_prefix_match():
    from aegis.usage_log import _price
    assert _price("claude-opus-4-8")[1] == 25.0      # output price (Opus 4.5+ tier)
    assert _price("claude-opus-4-1")[1] == 75.0      # legacy Opus tier fallback
    assert _price("claude-fable-5") == (10.0, 50.0)
    assert _price("gpt-4o-mini")[0] == 0.15
    assert _price("totally-unknown-model") == (0.0, 0.0)


def test_trajectory_export_formats():
    from aegis.session import Session, SessionStore
    from aegis.types import Message, ToolCall
    from aegis import trajectory as t
    s = Session.create()
    s.messages = [Message.system("sys"), Message.user("hi"),
                  Message.assistant("go", [ToolCall("c1", "list_dir", {"path": "."})]),
                  Message.tool("c1", "list_dir", "a\nb")]
    SessionStore().save(s)
    assert t.export("/tmp/_t_aegis.jsonl", [s.id], fmt="aegis") == 1
    oa = t._openai_finetune(t.record(s.id))
    assert any("tool_calls" in m for m in oa["messages"])
    assert any(m["role"] == "tool" for m in oa["messages"])
    hf = t._sharegpt(t.record(s.id))
    assert [c["from"] for c in hf["conversations"]] == ["system", "human", "gpt", "tool"]
    try:
        t.export("/tmp/_t_bad.jsonl", [s.id], fmt="nope")
        raise AssertionError("should reject unknown format")
    except ValueError:
        pass


def test_compaction_prunes_tool_output_and_images():
    from aegis.agent import compaction
    from aegis.types import Message
    big = "x" * 40_000
    img = "before data:image/png;base64," + ("A" * 5000) + " after"
    msgs = [Message.system("s"), Message.user("go"),
            Message.tool("c1", "bash", big), Message.tool("c2", "screenshot", img)]
    out = compaction.compress(msgs, provider=None, preserve_first=1, preserve_last=10)
    joined = "".join(m.content or "" for m in out)
    assert "…[truncated]" in joined                      # oversized tool dump pruned
    assert "data:image/png;base64" not in joined          # image stripped
    assert "[image omitted]" in joined


def test_background_tick_removed():
    """The dead duplicate path is gone; agent/review.py::maybe_review is the live one."""
    from aegis import learn
    assert not hasattr(learn, "background_tick")


def test_dependency_audit_tool(monkeypatch, tmp_path):
    from aegis.tools import extra_builtin as eb
    from aegis.tools.base import ToolContext
    # offline: stub OSV + package collection
    monkeypatch.setattr(eb, "_collect_packages", lambda path, cwd: [("badpkg", "1.0"), ("ok", "2.0")])
    monkeypatch.setattr(eb, "_osv_querybatch",
                        lambda pkgs: [{"vulns": [{"id": "GHSA-x"}]} if n == "badpkg" else {} for n, v in pkgs])
    r = eb.DependencyAuditTool().run({}, ToolContext(cwd=tmp_path))
    assert "1 vulnerable" in r.display and "badpkg" in r.content and "GHSA-x" in r.content
    monkeypatch.setattr(eb, "_osv_querybatch", lambda pkgs: [{} for _ in pkgs])
    assert eb.DependencyAuditTool().run({}, ToolContext(cwd=tmp_path)).display == "0 vulnerabilities"


def test_dependency_audit_parses_requirements(tmp_path):
    from aegis.tools.extra_builtin import _collect_packages
    req = tmp_path / "requirements.txt"
    req.write_text("requests==2.31.0  # http\nflask==3.0.0\nunpinned\n")
    pkgs = _collect_packages("requirements.txt", str(tmp_path))
    assert ("requests", "2.31.0") in pkgs and ("flask", "3.0.0") in pkgs and len(pkgs) == 2


def test_clarify_tool_both_surfaces(tmp_path):
    from aegis.tools.base import ToolContext
    from aegis.tools.extra_builtin import ClarifyTool
    # interactive surface: asker callback returns the chosen value
    ctx = ToolContext(cwd=tmp_path, asker=lambda q, ch: ch[0] if ch else "x")
    r = ClarifyTool().run({"question": "DB?", "choices": ["postgres", "sqlite"]}, ctx)
    assert "postgres" in r.content
    # headless surface: no asker -> surfaces the question and waits
    r2 = ClarifyTool().run({"question": "Proceed?", "choices": ["yes", "no"]}, ToolContext(cwd=tmp_path))
    assert "waiting" in r2.content.lower() and "1. yes" in r2.content
    assert ClarifyTool().run({"question": ""}, ToolContext(cwd=tmp_path)).is_error


def test_tool_output_spill_to_disk(tmp_path):
    import os
    from aegis.agent.loop import ToolExecutor
    from aegis.config import Config
    from aegis.tools.base import ToolContext
    from aegis.tools.registry import default_registry
    from aegis.types import ToolCall
    cfg = Config.load()
    cfg.data["tools"]["max_result_tokens"] = 50
    ex = ToolExecutor(default_registry(), None, ToolContext(cwd=tmp_path, config=cfg), lambda e: None)
    big = "LINE\n" * 2000
    out = ex._maybe_spill(ToolCall("c1", "bash", {}), big, is_error=False)
    assert "truncated to protect context" in out and len(out) < len(big)
    from aegis import config as c
    assert os.path.exists(os.path.join(c.sub("tool_outputs"), "bash_c1.txt"))
    # errors and small outputs are never spilled
    assert ex._maybe_spill(ToolCall("c2", "bash", {}), big, is_error=True) == big
    assert ex._maybe_spill(ToolCall("c3", "bash", {}), "short", is_error=False) == "short"


def test_anthropic_oauth_injects_claude_code_prefix():
    """claude.ai OAuth tokens require the system prompt to start with the Claude Code
    identity block, or the Messages API rejects the request. API-key auth must not."""
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from aegis.providers.anthropic import AnthropicTransport
    from aegis.types import Message

    cap = {}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_POST(self):
            cap["payload"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
            self.wfile.write(json.dumps({"content": [{"type": "text", "text": "ok"}],
                                         "stop_reason": "end_turn", "usage": {}}).encode())

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    msgs = [Message.system("You are AEGIS."), Message.user("hi")]

    class OAuth:
        def headers(self): return {"Authorization": "Bearer X", "anthropic-beta": "oauth-2025-04-20"}
    class ApiKey:
        def headers(self): return {"x-api-key": "sk-ant-X"}

    t = AnthropicTransport()
    try:
        t.complete(base_url=url, auth=OAuth(), model="claude-sonnet-4-5", messages=msgs,
                   tools=None, stream=False)
        oauth_sys = cap["payload"]["system"]
        assert oauth_sys[0]["text"].startswith("You are Claude Code")
        assert any("AEGIS" in b["text"] for b in oauth_sys)        # real prompt kept
        t.complete(base_url=url, auth=ApiKey(), model="claude-sonnet-4-5", messages=msgs,
                   tools=None, stream=False)
        assert not cap["payload"]["system"][0]["text"].startswith("You are Claude Code")
    finally:
        srv.shutdown()


def test_onboarding_offers_detected_key_user_decides(monkeypatch):
    """A key already in the environment is OFFERED, never used without consent."""
    from aegis import onboarding as ob
    from aegis.config import Config
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-EXISTING")
    # user accepts -> detected key used (True), no paste needed
    assert ob._configure_api_key(Config.load(), "ANTHROPIC_API_KEY",
                                 lambda p: "", lambda m: None, input_func=lambda p: "y") is True
    # user declines -> falls through to paste; empty paste -> skipped (False)
    assert ob._configure_api_key(Config.load(), "ANTHROPIC_API_KEY",
                                 lambda p: "", lambda m: None, input_func=lambda p: "n") is False


def test_import_claude_cli_login(monkeypatch, tmp_path):
    """Reuse an existing Claude Code login for the anthropic provider."""
    import json
    import time
    from aegis.providers.auth import AuthStore, import_claude_cli_login
    monkeypatch.setenv("HOME", str(tmp_path))
    cdir = tmp_path / ".claude"
    cdir.mkdir()
    (cdir / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {
        "accessToken": "tok-abc", "refreshToken": "ref-xyz",
        "expiresAt": int((time.time() + 3600) * 1000),
        "scopes": ["user:inference"], "subscriptionType": "max"}}))
    store = AuthStore(tmp_path / "auth.json")
    ok, detail = import_claude_cli_login(store)
    assert ok and "max" in detail
    creds = store.load("anthropic")
    assert creds["access_token"] == "tok-abc" and creds["token_type"] == "Bearer"
    assert creds["refresh_token"] == "ref-xyz" and creds["expires_at"] < 1e11   # ms -> s
    # missing credential -> clean failure, no crash
    (cdir / ".credentials.json").unlink()
    ok2, _ = import_claude_cli_login(store)
    assert ok2 is False


def test_skill_slash_new_scaffolds(tmp_path):
    import contextlib
    import io
    from aegis.agent.agent import Agent
    from aegis.cli import repl
    from aegis.config import Config
    from aegis.session import Session
    from conftest import FakeProvider
    a = Agent(config=Config.load(), provider=FakeProvider(), session=Session.create(), cwd=tmp_path)
    with contextlib.redirect_stdout(io.StringIO()):
        repl.handle_slash("/skill new my-skill does a thing", a)
    assert "my-skill" in (a.skills.discover() or {})


def test_onboarding_memory_step(monkeypatch):
    from aegis import onboarding as ob
    from aegis.config import Config
    cfg = Config.load()
    ob._configure_memory(cfg, ob.OnboardingState(), True, lambda p: "2", lambda m: None)
    assert cfg.get("memory.provider") == "jsonl"          # advanced pick applied
    # quick mode is a no-op (leaves the existing value untouched)
    ob._configure_memory(cfg, ob.OnboardingState(), False, lambda p: "1", lambda m: None)
    assert cfg.get("memory.provider") == "jsonl"


def test_status_shows_state_section(capsys):
    from aegis.cli.main import cmd_status
    from aegis.config import Config
    cmd_status(object(), Config.load())
    out = capsys.readouterr().out
    for label in ("State", "sessions:", "trajectory:", "cost (30d):", "disk:", "home:"):
        assert label in out


def test_trajectory_auto_capture_wired(tmp_path):
    """trajectory.enabled must actually write a line per turn (was dead config)."""
    import os
    from aegis.agent.agent import Agent
    from aegis.config import Config
    from aegis.session import Session
    from aegis import config as c
    from conftest import FakeProvider
    cfg = Config.load()
    cfg.data["trajectory"]["enabled"] = True
    cfg.data["tools"]["exec_mode"] = "full"
    Agent(config=cfg, provider=FakeProvider(), session=Session.create()).run("hi there")
    assert os.path.exists(c.sub("trajectories.jsonl"))
    # disabled by default -> nothing written for a fresh home
    cfg2 = Config.load()
    assert cfg2.get("trajectory.enabled") is False


def test_mcp_enabled_flag_respected():
    from aegis.config import Config
    from aegis.mcp.client import mcp_tools_from_config
    cfg = Config.load()
    cfg.data["mcp"]["enabled"] = False
    tools, mgr = mcp_tools_from_config(cfg)
    assert tools == [] and not mgr.clients      # disable flag now actually disables MCP


def test_mcp_skips_malformed_servers():
    from aegis.config import Config
    from aegis.mcp.client import build_manager
    cfg = Config.load()
    cfg.data["mcp"] = {"servers": {"broken": {}, "ok": {"command": "true"}}}
    mgr = build_manager(cfg)
    names = [c.name for c in mgr.clients]
    assert "ok" in names and "broken" not in names      # malformed entry skipped
