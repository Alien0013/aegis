"""First-class memory-provider metadata, setup, status, and tools."""

from __future__ import annotations

import json


def _cfg():
    from aegis.config import Config

    return Config.load()


def test_catalog_covers_existing_memory_providers():
    from aegis.memory_providers import (
        memory_provider_config_schema,
        memory_provider_metadata,
        memory_provider_report,
        memory_provider_setup,
    )

    expected = {
        "jsonl",
        "mem0",
        "honcho",
        "openviking",
        "supermemory",
        "byterover",
        "hindsight",
        "holographic",
        "retaindb",
    }
    cfg = _cfg()
    report = memory_provider_report(cfg)
    names = {row["name"] for row in report["provider_catalog"]}

    assert expected <= names
    assert report["active"]["status"] == "builtin_only"
    for name in expected:
        meta = memory_provider_metadata(name)
        setup = memory_provider_setup(name)
        schema = memory_provider_config_schema(name)
        assert meta["name"] == name
        assert meta["description"]
        assert meta["config_schema"]
        assert setup["known"] is True and setup["setup_steps"]
        assert schema["properties"]["memory.provider"]["const"] == name
        assert "memory_provider_status" in meta["tool_names"]


def test_http_provider_status_redacts_secrets(monkeypatch):
    from aegis.memory_providers import memory_provider_status

    cfg = _cfg()
    cfg.data["memory"]["provider"] = "supermemory"
    cfg.data["memory"]["supermemory"] = {
        "search_url": "https://memory.example/search",
        "headers": {"Authorization": "Bearer file-secret", "X-Trace": "trace-secret"},
        "result_path": "data.items",
    }
    monkeypatch.setenv("SUPERMEMORY_API_KEY", "env-secret")

    status = memory_provider_status("supermemory", cfg)
    rendered = json.dumps(status)

    assert status["ready"] is True
    assert status["configured"] is True
    assert status["config"]["memory.supermemory.search_url"] == "https://memory.example/search"
    assert status["config"]["memory.supermemory.result_path"] == "data.items"
    assert status["config"]["memory.supermemory.headers"] == {
        "configured": True,
        "keys": ["Authorization", "X-Trace"],
    }
    assert status["env"]["optional"] == [{"name": "SUPERMEMORY_API_KEY", "set": True}]
    assert "file-secret" not in rendered
    assert "trace-secret" not in rendered
    assert "env-secret" not in rendered


def test_jsonl_provider_uses_config_and_exposes_local_tools():
    from aegis.memory_providers import build_memory_provider
    from aegis.tools.base import ToolContext
    from aegis.types import Message

    cfg = _cfg()
    cfg.data["memory"]["provider"] = "jsonl"
    cfg.data["memory"]["jsonl"] = {"max_recent": 1}
    provider = build_memory_provider("jsonl", cfg)

    provider.sync_turn([Message.user("alpha"), Message.assistant("noted alpha")])
    provider.sync_turn([Message.user("beta"), Message.assistant("noted beta")])

    block = provider.system_prompt_block()
    tools = {tool.name: tool for tool in provider.tools()}
    status = tools["memory_provider_status"].run({}, ToolContext(config=cfg)).data
    setup = tools["memory_provider_setup"].run({}, ToolContext(config=cfg)).data
    recent = tools["jsonl_memory_recent"].run({"limit": 2}, ToolContext(config=cfg))

    assert "beta" in block and "alpha" not in block
    assert status["name"] == "jsonl"
    assert status["ready"] is True
    assert status["note_count"] == 2
    assert setup["config_schema"]["memory.jsonl.max_recent"]["default"] == 12
    assert "alpha" in recent.content and "beta" in recent.content
    assert recent.data["provider"] == "jsonl"


def test_http_provider_recall_tool_is_mockable_and_fail_soft(monkeypatch):
    from aegis.memory_providers import build_memory_provider
    from aegis.tools.base import ToolContext
    from aegis.types import Message

    cfg = _cfg()
    cfg.data["memory"]["provider"] = "openviking"
    cfg.data["memory"]["openviking"] = {
        "add_url": "https://memory.example/add",
        "search_url": "https://memory.example/search",
        "result_path": "data.items",
    }
    monkeypatch.setenv("OPENVIKING_API_KEY", "token")
    calls = []

    class Response:
        def json(self):
            return {"data": {"items": [{"text": "ship parser fix"}, {"memory": "prefers terse"}]}}

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return Response()

    monkeypatch.setattr("httpx.post", fake_post)
    provider = build_memory_provider("openviking", cfg)
    recall = next(tool for tool in provider.tools() if tool.name == "memory_provider_recall")

    result = recall.run({"query": "parser"}, ToolContext(config=cfg))
    provider.sync_turn([Message.user("remember this"), Message.assistant("stored")])

    assert not result.is_error
    assert "ship parser fix" in result.content
    assert "prefers terse" in result.content
    assert calls[0]["url"] == "https://memory.example/search"
    assert calls[0]["json"] == {"query": "parser"}
    assert calls[0]["headers"]["Authorization"] == "Bearer token"
    assert calls[1]["url"] == "https://memory.example/add"
    assert calls[1]["json"]["messages"][-1] == {"role": "assistant", "content": "stored"}

    def boom(*_args, **_kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr("httpx.post", boom)
    failed_recall = recall.run({"query": "parser"}, ToolContext(config=cfg))
    provider.sync_turn([Message.user("still fail-soft")])

    assert not failed_recall.is_error
    assert "No memory returned" in failed_recall.content
