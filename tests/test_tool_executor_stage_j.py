"""Stage J ToolExecutor checkpoint ordering contracts."""

from __future__ import annotations

import json
from types import SimpleNamespace

from aegis.agent.loop import ToolExecutor
from aegis.checkpoints import CheckpointStore
from aegis.config import Config
from aegis.tools.base import Tool, ToolContext, ToolResult
from aegis.tools.builtin import BashTool, WriteFileTool
from aegis.tools.permissions import PermissionEngine
from aegis.tools.registry import ToolRegistry
from aegis.types import ToolCall


def _config(*, exec_mode: str = "auto") -> Config:
    cfg = Config.load()
    cfg.set("checkpoints.enabled", True)
    cfg.set("security.scan_enabled", False)
    cfg.set("tools.deny_groups", [])
    cfg.set("tools.exec_mode", exec_mode)
    cfg.set("tools.terminal_backend", "local")
    return cfg


def _registry(*tools) -> ToolRegistry:
    reg = ToolRegistry()
    reg.register_all(list(tools))
    return reg


def _executor(tmp_path, *, exec_mode: str = "auto", guard=None, tools=None) -> ToolExecutor:
    cfg = _config(exec_mode=exec_mode)
    ctx = ToolContext(cwd=tmp_path, config=cfg)
    return ToolExecutor(
        _registry(*(tools or [WriteFileTool()])),
        PermissionEngine(cfg),
        ctx,
        lambda _event: None,
        guard=guard,
    )


def _checkpoint_count(tmp_path) -> int:
    return len(CheckpointStore(tmp_path).list())


class EchoTool(Tool):
    name = "echo_tool"
    description = "Synthetic tool for executor hook tests."
    parameters = {
        "type": "object",
        "properties": {
            "value": {"type": "string"},
        },
    }

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def run(self, args, ctx):
        self.calls.append(dict(args))
        return ToolResult.ok(json.dumps({"ok": True, "args": args}, sort_keys=True))


class AsyncLoopTool(Tool):
    name = "async_loop_tool"
    description = "Synthetic async tool for executor bridge tests."
    parameters = {
        "type": "object",
        "properties": {
            "value": {"type": "string"},
        },
    }

    def __init__(self) -> None:
        self.loop_ids: list[int] = []

    def run(self, args, ctx):
        async def inner():
            import asyncio
            from aegis.tools.thread_context import get_current_approval_context

            self.loop_ids.append(id(asyncio.get_running_loop()))
            payload = {
                "ok": True,
                "args": dict(args),
                "session_key": get_current_approval_context().session_key,
            }
            return ToolResult.ok(json.dumps(payload, sort_keys=True))

        return inner()


def test_executor_parallelizes_only_mcp_tools_with_provenance_opt_in(tmp_path):
    class FakeMCP:
        def __init__(self, safe: set[str]) -> None:
            self.safe = safe

        def is_mcp_tool_parallel_safe(self, name: str) -> bool:
            return name in self.safe

    cfg = _config()
    agent = SimpleNamespace(
        _mcp=FakeMCP({"mcp__docs__search", "mcp__docs__lookup"})
    )
    ex = ToolExecutor(
        _registry(),
        PermissionEngine(cfg),
        ToolContext(cwd=tmp_path, config=cfg, agent=agent),
        lambda _event: None,
    )

    assert ex._should_parallelize([
        ToolCall("mcp-a", "mcp__docs__search", {}),
        ToolCall("mcp-b", "mcp__docs__lookup", {}),
    ])
    assert not ex._should_parallelize([
        ToolCall("mcp-a", "mcp__docs__search", {}),
        ToolCall("mcp-b", "mcp__serial__mutate", {}),
    ])
    ex.ctx.agent = SimpleNamespace(_mcp=None)
    assert not ex._should_parallelize([
        ToolCall("mcp-a", "mcp__docs__search", {}),
        ToolCall("mcp-b", "mcp__docs__lookup", {}),
    ])


def test_tool_hooks_observe_ids_and_transform_after_post(tmp_path):
    from aegis.plugins import PluginAPI, _HOOKS

    original_hooks = {event: list(hooks) for event, hooks in _HOOKS.items()}
    _HOOKS.clear()
    observed: list[tuple[str, dict]] = []
    api = PluginAPI()

    def pre_hook(**kwargs):
        observed.append(("pre_tool_call", dict(kwargs)))

    def post_hook(**kwargs):
        observed.append(("post_tool_call", dict(kwargs)))
        return "observer return must be ignored"

    def transform_hook(**kwargs):
        observed.append(("transform_tool_result", dict(kwargs)))
        return "rewritten result"

    api.register_hook("pre_tool_call", pre_hook)
    api.register_hook("post_tool_call", post_hook)
    api.register_hook("transform_tool_result", transform_hook)

    tool = EchoTool()
    cfg = _config()
    session = SimpleNamespace(id="session-1", meta={})
    agent = SimpleNamespace(
        session=session,
        _trace_context={"turn_id": "turn-1"},
        _current_api_request_id="api-1",
        _last_api_request_id="api-1",
    )
    events: list[dict] = []
    ctx = ToolContext(cwd=tmp_path, config=cfg, session=session, agent=agent, task_id="task-1")
    ex = ToolExecutor(_registry(tool), PermissionEngine(cfg), ctx, events.append)

    try:
        result = ex.execute_one_raw(ToolCall("call-1", "echo_tool", {"value": "original"}))
    finally:
        _HOOKS.clear()
        _HOOKS.update(original_hooks)

    assert not result.is_error
    assert result.content == "rewritten result"
    assert tool.calls == [{"value": "original"}]
    assert [name for name, _ in observed] == [
        "pre_tool_call",
        "post_tool_call",
        "transform_tool_result",
    ]
    pre = observed[0][1]
    post = observed[1][1]
    transform = observed[2][1]
    for payload in (pre, post, transform):
        assert payload["tool_name"] == "echo_tool"
        assert payload["args"] == {"value": "original"}
        assert payload["task_id"] == "task-1"
        assert payload["session_id"] == "session-1"
        assert payload["tool_call_id"] == "call-1"
        assert payload["turn_id"] == "turn-1"
        assert payload["api_request_id"] == "api-1"
    assert "duration_ms" not in pre
    assert isinstance(post["duration_ms"], int) and post["duration_ms"] >= 0
    assert transform["duration_ms"] == post["duration_ms"]
    original_result = json.dumps({"ok": True, "args": {"value": "original"}}, sort_keys=True)
    assert post["result"] == original_result
    assert transform["result"] == original_result
    assert post["status"] == "ok"
    assert any(e.get("type") == "tool_result" and e.get("preview") == "rewritten result" for e in events)


def test_pre_tool_call_plugin_block_skips_dispatch_and_emits_post(tmp_path):
    from aegis.plugins import PluginAPI, _HOOKS

    class ShouldNotRunTool(EchoTool):
        def run(self, args, ctx):
            raise AssertionError("blocked tool should not dispatch")

    original_hooks = {event: list(hooks) for event, hooks in _HOOKS.items()}
    _HOOKS.clear()
    observed: list[tuple[str, dict]] = []
    api = PluginAPI()
    api.register_hook("pre_tool_call", lambda **kwargs: observed.append(("pre_tool_call", dict(kwargs))) or {
        "action": "block",
        "message": "Blocked by policy",
    })
    api.register_hook("post_tool_call", lambda **kwargs: observed.append(("post_tool_call", dict(kwargs))))

    cfg = _config()
    events: list[dict] = []
    ex = ToolExecutor(
        _registry(ShouldNotRunTool()),
        PermissionEngine(cfg),
        ToolContext(cwd=tmp_path, config=cfg),
        events.append,
    )

    try:
        result = ex.execute_one_raw(ToolCall("blocked-call", "echo_tool", {"value": "nope"}))
    finally:
        _HOOKS.clear()
        _HOOKS.update(original_hooks)

    assert result.is_error
    assert result.content == "Blocked by policy"
    assert [name for name, _ in observed] == ["pre_tool_call", "post_tool_call"]
    post = observed[1][1]
    assert post["status"] == "blocked"
    assert post["error_type"] == "plugin_block"
    assert post["error_message"] == "Blocked by policy"
    assert post["duration_ms"] == 0
    assert any(e.get("type") == "tool_result" and e.get("name") == "echo_tool" for e in events)


def test_tool_request_middleware_block_still_emits_tool_outcome(tmp_path, monkeypatch):
    from aegis.plugins import PluginAPI, _HOOKS, _MIDDLEWARE

    class ShouldNotRunTool(EchoTool):
        def run(self, args, ctx):
            raise AssertionError("middleware-blocked tool should not dispatch")

    original_hooks = {event: list(hooks) for event, hooks in _HOOKS.items()}
    original_middleware = {kind: list(chain) for kind, chain in _MIDDLEWARE.items()}
    _HOOKS.clear()
    _MIDDLEWARE.clear()
    api = PluginAPI()
    api.register_middleware(
        "tool_request",
        lambda payload, _next_call, _agent: {
            "block": True,
            "reason": "blocked by request middleware",
            "middleware_trace": [{"source": "unit-test", "reason": "deny"}],
        },
    )
    hook_events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        "aegis.hooks.run_hooks",
        lambda _config, event, context=None: hook_events.append((event, dict(context or {}))) or [],
    )
    events: list[dict] = []
    cfg = _config()
    ex = ToolExecutor(
        _registry(ShouldNotRunTool()),
        PermissionEngine(cfg),
        ToolContext(cwd=tmp_path, config=cfg),
        events.append,
    )

    try:
        result = ex.execute_one_raw(ToolCall("mw-blocked-call", "echo_tool", {"value": "nope"}))
    finally:
        _HOOKS.clear()
        _HOOKS.update(original_hooks)
        _MIDDLEWARE.clear()
        _MIDDLEWARE.update(original_middleware)

    assert result.is_error
    assert result.content == "blocked by request middleware"
    assert [event for event, _context in hook_events] == ["pre_tool", "pre_tool_call", "post_tool_call", "post_tool"]
    post_tool_call = next(context for event, context in hook_events if event == "post_tool_call")
    assert post_tool_call["status"] == "blocked"
    assert post_tool_call["error_type"] == "middleware_block"
    assert post_tool_call["error_message"] == "blocked by request middleware"
    assert post_tool_call["duration_ms"] == 0
    assert post_tool_call["middleware_trace"] == [{"source": "unit-test", "reason": "deny"}]
    tool_result = next(e for e in events if e.get("type") == "tool_result")
    assert tool_result["name"] == "echo_tool"
    assert tool_result["is_error"] is True


def test_permission_denied_write_file_does_not_checkpoint(tmp_path):
    target = tmp_path / "denied.txt"
    target.write_text("before\n")
    ex = _executor(tmp_path, exec_mode="deny")

    before = _checkpoint_count(tmp_path)
    res = ex.execute_one_raw(
        ToolCall("write-denied", "write_file", {"path": str(target), "content": "after\n"})
    )

    assert res.is_error
    assert "permission denied" in res.content
    assert target.read_text() == "before\n"
    assert _checkpoint_count(tmp_path) == before


def test_guard_blocked_write_file_does_not_checkpoint(tmp_path):
    class BlockingGuard:
        def check(self, _name, _arguments):
            return "blocked by loop guard"

        def record(self, *_args):
            raise AssertionError("blocked tool results should not be recorded")

    target = tmp_path / "guarded.txt"
    target.write_text("before\n")
    ex = _executor(tmp_path, guard=BlockingGuard())

    before = _checkpoint_count(tmp_path)
    res = ex.execute_one_raw(
        ToolCall("write-blocked", "write_file", {"path": str(target), "content": "after\n"})
    )

    assert res.is_error
    assert "blocked by loop guard" in res.content
    assert target.read_text() == "before\n"
    assert _checkpoint_count(tmp_path) == before


def test_allowed_write_file_creates_one_checkpoint(tmp_path):
    target = tmp_path / "allowed.txt"
    target.write_text("before\n")
    ex = _executor(tmp_path)

    before = _checkpoint_count(tmp_path)
    res = ex.execute_one_raw(
        ToolCall("write-allowed", "write_file", {"path": str(target), "content": "after\n"})
    )

    assert not res.is_error
    assert target.read_text() == "after\n"
    assert _checkpoint_count(tmp_path) == before + 1


def test_destructive_bash_overwrite_snapshots_target_before_execution(tmp_path):
    target = tmp_path / "bash-target.txt"
    target.write_text("before\n")
    ex = _executor(tmp_path, tools=[BashTool()])

    before = _checkpoint_count(tmp_path)
    res = ex.execute_one_raw(
        ToolCall(
            "bash-overwrite",
            "bash",
            {"command": f"printf 'after\\n' > {target.name}", "timeout": 10},
        )
    )

    assert not res.is_error
    assert target.read_text() == "after\n"
    assert _checkpoint_count(tmp_path) == before + 1
    restored = CheckpointStore(tmp_path).rollback()
    assert str(target) in restored
    assert target.read_text() == "before\n"


def test_executor_coerces_schema_arguments_before_tool_dispatch(tmp_path):
    class CoerceTool(Tool):
        name = "coerce_schema"
        description = "Synthetic schema coercion target."
        parameters = {
            "type": "object",
            "properties": {
                "count": {"type": "integer"},
                "ratio": {"type": "number"},
                "enabled": {"type": "boolean"},
                "items": {"type": "array"},
                "payload": {"type": "object"},
                "maybe": {"type": ["string", "null"]},
                "invalid": {"type": "integer"},
            },
        }

        def __init__(self) -> None:
            self.seen: dict | None = None

        def run(self, args, ctx):
            self.seen = dict(args)
            return ToolResult.ok("ok")

    tool = CoerceTool()
    ex = _executor(tmp_path, tools=[tool])
    original = {
        "count": "42",
        "ratio": "3.5",
        "enabled": "true",
        "items": "one",
        "payload": '{"mode": "fast"}',
        "maybe": "null",
        "invalid": "3.14",
        "unknown": "left alone",
    }

    res = ex.execute_one_raw(ToolCall("coerce-call", "coerce_schema", dict(original)))

    assert not res.is_error
    assert original["count"] == "42"
    assert tool.seen == {
        "count": 42,
        "ratio": 3.5,
        "enabled": True,
        "items": ["one"],
        "payload": {"mode": "fast"},
        "maybe": None,
        "invalid": "3.14",
        "unknown": "left alone",
    }


def test_async_tool_uses_persistent_main_thread_loop(tmp_path):
    tool = AsyncLoopTool()
    ex = _executor(tmp_path, tools=[tool])

    first = ex.execute_one_raw(ToolCall("async-main-1", "async_loop_tool", {"value": "one"}))
    second = ex.execute_one_raw(ToolCall("async-main-2", "async_loop_tool", {"value": "two"}))

    assert not first.is_error
    assert not second.is_error
    assert json.loads(first.content)["args"] == {"value": "one"}
    assert json.loads(second.content)["args"] == {"value": "two"}
    assert len(tool.loop_ids) == 2
    assert tool.loop_ids[0] == tool.loop_ids[1]


def test_async_tool_uses_persistent_worker_thread_loop(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    tool = AsyncLoopTool()
    ex = _executor(tmp_path, tools=[tool])

    def run_twice():
        return [
            ex.execute_one_raw(ToolCall("async-worker-1", "async_loop_tool", {"value": "one"})),
            ex.execute_one_raw(ToolCall("async-worker-2", "async_loop_tool", {"value": "two"})),
        ]

    with ThreadPoolExecutor(max_workers=1) as pool:
        first, second = pool.submit(run_twice).result(timeout=5)

    assert not first.is_error
    assert not second.is_error
    assert len(tool.loop_ids) == 2
    assert tool.loop_ids[0] == tool.loop_ids[1]


def test_async_tool_runs_from_existing_event_loop_with_context(tmp_path):
    import asyncio
    from aegis.tools.thread_context import reset_current_session_key, set_current_session_key

    tool = AsyncLoopTool()
    ex = _executor(tmp_path, tools=[tool])

    async def exercise():
        token = set_current_session_key("session-from-running-loop")
        try:
            return ex.execute_one_raw(
                ToolCall("async-running-loop", "async_loop_tool", {"value": "inside"})
            )
        finally:
            reset_current_session_key(token)

    result = asyncio.run(exercise())

    assert not result.is_error
    assert json.loads(result.content) == {
        "ok": True,
        "args": {"value": "inside"},
        "session_key": "session-from-running-loop",
    }
    assert len(tool.loop_ids) == 1


def test_async_tool_execution_middleware_result_is_awaited(tmp_path):
    from aegis.plugins import PluginAPI, _MIDDLEWARE

    original_middleware = {kind: list(chain) for kind, chain in _MIDDLEWARE.items()}
    _MIDDLEWARE.clear()
    api = PluginAPI()

    def async_middleware(payload, next_call, _agent):
        original_payload = dict(payload)

        async def inner():
            updated = dict(original_payload)
            updated["arguments"] = {**updated.get("arguments", {}), "value": "middleware"}
            return next_call(updated)

        return inner()

    api.register_middleware("tool_execution", async_middleware)
    tool = EchoTool()
    ex = _executor(tmp_path, tools=[tool])

    try:
        result = ex.execute_one_raw(ToolCall("async-middleware", "echo_tool", {"value": "original"}))
    finally:
        _MIDDLEWARE.clear()
        _MIDDLEWARE.update(original_middleware)

    assert not result.is_error
    assert tool.calls == [{"value": "middleware"}]
    assert json.loads(result.content)["args"] == {"value": "middleware"}
