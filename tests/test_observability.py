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


def test_mcp_skips_malformed_servers():
    from aegis.config import Config
    from aegis.mcp.client import build_manager
    cfg = Config.load()
    cfg.data["mcp"] = {"servers": {"broken": {}, "ok": {"command": "true"}}}
    mgr = build_manager(cfg)
    names = [c.name for c in mgr.clients]
    assert "ok" in names and "broken" not in names      # malformed entry skipped
