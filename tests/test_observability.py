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


def test_cost_pricing_prefix_match():
    from aegis.usage_log import _price
    assert _price("claude-opus-4-8")[1] == 75.0      # output price
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
        assert False, "should reject unknown format"
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


def test_background_learn_is_opt_in_and_gated(monkeypatch):
    from aegis import learn
    from aegis.config import Config
    from aegis.session import Session
    from aegis.types import Message
    calls = {"n": 0}
    monkeypatch.setattr(learn, "review_session", lambda *a, **k: calls.__setitem__("n", calls["n"] + 1) or [])
    s = Session.create()
    s.messages = [Message.assistant("a1"), Message.assistant("a2")]
    cfg = Config.load()
    assert learn.background_tick(cfg, s) is False         # off by default
    cfg.data["learn"].update({"background": True, "background_every": 2})
    assert learn.background_tick(cfg, s) is True          # 2 turns >= every=2
    import time; time.sleep(0.2)
    assert calls["n"] == 1
    assert learn.background_tick(cfg, s) is False          # no new turns -> no re-review


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
