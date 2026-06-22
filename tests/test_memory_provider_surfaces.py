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


def test_mem0_schema_status_supports_host_mode_without_mem0ai(monkeypatch):
    from aegis.memory_providers import (
        memory_provider_config_schema,
        memory_provider_metadata,
        memory_provider_status,
    )

    cfg = _cfg()
    cfg.data["memory"]["provider"] = "mem0"
    cfg.data["memory"]["mem0"] = {"host": "http://mem0.local", "user_id": "u1", "agent_id": "a1"}
    monkeypatch.setattr("aegis.memory_providers.importlib.util.find_spec", lambda _name: None)

    meta = memory_provider_metadata("mem0")
    schema = memory_provider_config_schema("mem0")
    status = memory_provider_status("mem0", cfg)

    assert "mem0_search" in meta["tool_names"]
    assert "mem0_update" in meta["tool_names"]
    assert "memory.mem0.host" in schema["properties"]
    assert status["ready"] is True
    assert status["dependency"]["required"] is False
    assert status["dependency"]["mode"] == "host"
    assert status["config"]["memory.mem0.host"] == "http://mem0.local"


def test_mem0_host_provider_crud_tools_call_rest_endpoints(monkeypatch):
    from aegis.memory_providers import build_memory_provider
    from aegis.tools.base import ToolContext
    from aegis.types import Message

    cfg = _cfg()
    cfg.data["memory"]["provider"] = "mem0"
    cfg.data["memory"]["mem0"] = {
        "host": "http://mem0.local/",
        "user_id": "user-1",
        "agent_id": "agent-1",
        "timeout": 7,
    }
    monkeypatch.setenv("MEM0_API_KEY", "secret-key")
    calls = []

    class Response:
        text = ""

        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    def fake_request(method, url, json=None, headers=None, timeout=None):
        calls.append({"method": method, "url": url, "json": json, "headers": headers, "timeout": timeout})
        if url.endswith("/search"):
            return Response({"results": [{"id": "m1", "memory": "ship parser fix"}]})
        return Response({"ok": True})

    monkeypatch.setattr("httpx.request", fake_request)

    provider = build_memory_provider("mem0", cfg)
    tools = {tool.name: tool for tool in provider.tools()}

    assert {"memory_provider_recall", "mem0_search", "mem0_add", "mem0_update", "mem0_delete"} <= set(tools)
    search = tools["mem0_search"].run({"query": "parser", "limit": 3}, ToolContext(config=cfg))
    add = tools["mem0_add"].run({"text": "remember alpha"}, ToolContext(config=cfg))
    update = tools["mem0_update"].run({"memory_id": "m1", "text": "remember beta"}, ToolContext(config=cfg))
    delete = tools["mem0_delete"].run({"memory_id": "m1"}, ToolContext(config=cfg))
    provider.sync_turn([Message.user("question"), Message.assistant("answer")])

    assert not search.is_error and "ship parser fix" in search.content
    assert not add.is_error and not update.is_error and not delete.is_error
    assert calls[0] == {
        "method": "POST",
        "url": "http://mem0.local/search",
        "json": {"query": "parser", "filters": {"user_id": "user-1"}, "top_k": 3},
        "headers": {"X-API-Key": "secret-key"},
        "timeout": 7,
    }
    assert calls[1]["method"] == "POST"
    assert calls[1]["url"] == "http://mem0.local/memories"
    assert calls[1]["json"] == {
        "messages": [{"role": "user", "content": "remember alpha"}],
        "user_id": "user-1",
        "agent_id": "agent-1",
        "infer": False,
    }
    assert calls[2]["method"] == "PUT"
    assert calls[2]["url"] == "http://mem0.local/memories/m1"
    assert calls[2]["json"] == {"text": "remember beta"}
    assert calls[3]["method"] == "DELETE"
    assert calls[3]["url"] == "http://mem0.local/memories/m1"
    assert calls[4]["method"] == "POST"
    assert calls[4]["json"]["infer"] is True
    assert calls[4]["json"]["messages"][-1] == {"role": "assistant", "content": "answer"}


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


def test_provider_surface_background_prefetch_cache():
    import time

    from aegis.memory_providers import ProviderSurfaceMixin

    class Provider(ProviderSurfaceMixin):
        name = "cachey"

        def __init__(self):
            self.calls = []

        def prefetch(self, query, *, session_id=""):
            self.calls.append((query, session_id))
            return f"cached {query} for {session_id}"

    provider = Provider()
    provider.initialize("sess-cache")
    provider.queue_prefetch("alpha")

    deadline = time.time() + 2
    cached = ""
    while time.time() < deadline:
        cached = provider.consume_prefetch("alpha", session_id="sess-cache")
        if cached:
            break
        time.sleep(0.01)

    assert cached == "cached alpha for sess-cache"
    assert provider.consume_prefetch("alpha", session_id="sess-cache") == ""
