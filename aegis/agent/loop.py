"""The bounded synchronous agent loop + concurrent tool executor."""

from __future__ import annotations

import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from ..constants import MAX_PARALLEL_TOOLS
from ..redact import redact_secret_values, redact_secrets
from ..tools.base import ToolContext, ToolResult
from ..types import Message, ToolCall, new_id
from . import compaction, governance
from .events import EventType

OnEvent = Callable[[dict], None]
_PERSISTED_OUTPUT_TAG = "<persisted-output>"
_PERSISTED_OUTPUT_CLOSE = "</persisted-output>"
_SPILL_MARKER = "truncated to protect context"
_NEVER_PARALLEL_TOOLS = frozenset({
    "bash", "clarify", "execute_code", "process", "send_message", "memory",
    "todo_write", "skill_manage", "browser", "computer", "github", "cronjob",
    "schedule_task", "download", "http_request",
})
_PARALLEL_SAFE_TOOLS = frozenset({
    "agent_state", "dependency_audit", "glob", "list_dir", "read_file",
    "search", "session_search", "skill", "system_status", "tool_search",
    "vision_analyze", "web_extract", "web_fetch", "web_search",
})
_PATH_SCOPED_TOOLS = frozenset({"apply_patch", "edit_file", "list_dir", "read_file", "write_file"})
# Max times the ultracode loop is pushed to continue past a premature "done" while
# todo items remain open — bounded so it can never loop forever.
_ULTRACODE_MAX_CONTINUES = 12
_DESTRUCTIVE_COMMAND_RE = re.compile(
    r"""(?:^|\s|&&|\|\||;|`)(?:rm\s|rmdir\s|cp\s|install\s|mv\s|sed\s+-i|
        truncate\s|dd\s|shred\s|git\s+(?:reset|clean|checkout)\s)""",
    re.VERBOSE,
)
_REDIRECT_OVERWRITE_RE = re.compile(r"[^>]>[^>]|^>[^>]")


def _without_thinking(m: Message) -> Message:
    """A shallow copy of ``m`` with thinking blocks/reasoning removed — for the
    thinking-signature 400 retry. Non-assistant or block-free messages pass through
    unchanged (identity) so unaffected turns aren't needlessly copied."""
    if m.role != "assistant" or not (getattr(m, "thinking_blocks", None) or m.reasoning):
        return m
    import dataclasses
    return dataclasses.replace(m, thinking_blocks=[], reasoning="")


def _with_retrieved_memory(m: Message, fetched: str) -> Message:
    import dataclasses
    content = f"<retrieved_memory>\n{fetched}\n</retrieved_memory>\n\n{m.content}"
    return dataclasses.replace(m, content=content)


def _provider_wire_messages(agent, messages: list[Message]) -> list[Message]:
    """Return provider-only message copies for volatile context tweaks.

    Retrieved memory is relevant to this turn, but it is not part of the user's
    canonical transcript and must not be persisted into history or memory sync.
    """
    wire_messages = messages
    fetched = str(getattr(agent, "_retrieved_memory_for_turn", "") or "").strip()
    target = str(getattr(agent, "_retrieved_memory_user_content", "") or "")
    if fetched and target:
        for idx in range(len(messages) - 1, -1, -1):
            msg = messages[idx]
            if msg.role == "user" and msg.content == target:
                wire_messages = list(messages)
                wire_messages[idx] = _with_retrieved_memory(msg, fetched)
                break
    if getattr(agent, "_strip_thinking", False):
        wire_messages = [_without_thinking(m) for m in wire_messages]
    return wire_messages


def _provider_complete(provider, messages, *, tools=None, **kwargs):
    """Call provider.complete without breaking older Provider-compatible fakes/plugins."""
    import inspect

    try:
        params = inspect.signature(provider.complete).parameters
    except (TypeError, ValueError):
        return provider.complete(messages, tools=tools, **kwargs)
    accepts_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
    if accepts_kwargs:
        return provider.complete(messages, tools=tools, **kwargs)
    filtered = {k: v for k, v in kwargs.items() if k in params}
    return provider.complete(messages, tools=tools, **filtered)


def _preview(text: str, limit: int = 500) -> str:
    one_line = (text or "").replace("\r", "").strip()
    if len(one_line) <= limit:
        return one_line
    return one_line[:limit].rstrip() + " …"


def _last_nonempty_assistant_text(messages: list[Message], *, exclude: Message | None = None) -> str:
    """Most recent assistant text with real content — used to hand back earlier output when a
    final reply comes back empty, instead of returning nothing."""
    for m in reversed(messages):
        if m is exclude or getattr(m, "role", "") != "assistant":
            continue
        text = (getattr(m, "content", "") or "").strip()
        if text:
            return text
    return ""


def _artifact_ref(data) -> str:
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        for key in ("artifact_ref", "artifact", "path", "file", "url"):
            value = data.get(key)
            if value:
                return str(value)
    return ""


def _prompt_trace_meta(session) -> dict:
    meta = getattr(session, "meta", {}) or {}
    return {
        "system_prompt_hash": meta.get("system_prompt_hash", ""),
        "system_prompt_tokens": meta.get("system_prompt_tokens", 0),
        "system_prompt_chars": meta.get("system_prompt_chars", 0),
        "prompt_parts": list(meta.get("prompt_parts") or []),
    }


def _trace_scalar(value) -> str:
    if value is None:
        return ""
    raw = getattr(value, "value", value)
    return str(raw)


def _agent_request_max_tokens(agent) -> int:
    try:
        value = int(getattr(agent, "_request_max_tokens", 0) or 0)
    except (TypeError, ValueError):
        return 0
    return value if value > 0 else 0


def _inherit_child_session_meta(parent, child) -> None:
    meta = getattr(parent, "meta", {}) or {}
    child_meta = getattr(child, "meta", None)
    if not isinstance(meta, dict) or not isinstance(child_meta, dict):
        return
    for key in ("runtime", "runtime_controls", "model", "provider"):
        if key not in meta:
            continue
        value = meta[key]
        child_meta[key] = dict(value) if isinstance(value, dict) else value


def _provider_trace_data(agent, wire_messages, schemas, response_state: dict, prompt_meta: dict) -> dict:
    api_mode = getattr(agent.provider, "api_mode", "")
    tool_names = [str(t.get("name") or "") for t in (schemas or []) if isinstance(t, dict)]
    return {
        "iteration": agent.budget.api_call_count + 1,
        "system_prompt_hash": prompt_meta.get("system_prompt_hash", ""),
        "transport": type(agent.provider).__name__,
        "api_mode": _trace_scalar(api_mode),
        "context_length": int(getattr(agent.provider, "context_length", 0) or 0),
        "message_count": len(wire_messages or []),
        "tool_schema_count": len(schemas or []),
        "tool_schema_names": [name for name in tool_names if name],
        "stream": bool(getattr(agent, "stream", False)),
        "reasoning": getattr(agent, "reasoning", "off"),
        "max_tokens": _agent_request_max_tokens(agent),
        "responses_state": {
            "enabled": bool(response_state.get("enabled")),
            "store": bool(response_state.get("store")),
            "send_previous": bool(response_state.get("send_previous", True)),
            "preserve_items": bool(response_state.get("preserve_items", True)),
            "context_management": bool(response_state.get("context_management") or response_state.get("compaction")),
            "previous_response_id": str(response_state.get("previous_response_id") or ""),
            "provider": str(response_state.get("provider") or ""),
            "model": str(response_state.get("model") or ""),
            "previous_response_skipped": str(response_state.get("previous_response_skipped") or ""),
        },
    }


def _responses_state_matches(prev, *, provider: str = "", model: str = "") -> bool:
    prev_provider = str(getattr(prev, "provider", "") or "")
    prev_model = str(getattr(prev, "model", "") or "")
    provider = str(provider or "")
    model = str(model or "")
    if prev_provider and provider and prev_provider != provider:
        return False
    if prev_model and model and prev_model != model:
        return False
    return True


def _hydrate_previous_response_id(
    session_id: str,
    response_state: dict,
    *,
    provider: str = "",
    model: str = "",
) -> dict:
    state = dict(response_state or {})
    if not (state.get("enabled") and state.get("store") and state.get("send_previous", True) and session_id):
        return state
    if state.get("previous_response_id"):
        return state
    try:
        from ..responses_state import ResponsesStateStore

        prev = ResponsesStateStore().get(session_id)
        if prev and prev.response_id and _responses_state_matches(prev, provider=provider, model=model):
            state["previous_response_id"] = prev.response_id
        elif prev and prev.response_id:
            state["previous_response_skipped"] = "provider_or_model_changed"
    except Exception:  # noqa: BLE001
        pass
    return state


def _response_state_for_agent(agent, session_id: str) -> dict:
    response_state = dict(agent.config.get("responses.state", {}) or {})
    response_state["provider"] = str(getattr(agent.provider, "name", "") or "")
    response_state["model"] = str(getattr(agent.provider, "model", "") or "")
    native_compaction = dict(agent.config.get("responses.compaction", {}) or {})
    if native_compaction.get("enabled"):
        response_state["context_management"] = _responses_context_management(agent, native_compaction)
    return _hydrate_previous_response_id(
        session_id,
        response_state,
        provider=response_state.get("provider", ""),
        model=response_state.get("model", ""),
    )


def _responses_context_management(agent, native_compaction: dict) -> list[dict[str, int | str]]:
    from ..constants import COMPACT_THRESHOLD

    default_threshold = agent.config.get("agent.compression.threshold", COMPACT_THRESHOLD)
    provider_name = str(getattr(getattr(agent, "provider", None), "name", "") or "")
    model = str(getattr(getattr(agent, "provider", None), "model", "") or "")
    if (
        "compact_threshold" not in native_compaction
        and "compact_threshold_tokens" not in native_compaction
        and provider_name in {"openai-codex", "codex", "codex-app-server"}
        and model.startswith("gpt-5.5")
    ):
        default_threshold = 0.85
    raw = native_compaction.get("compact_threshold_tokens",
                                native_compaction.get("compact_threshold", default_threshold))
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = float(default_threshold or COMPACT_THRESHOLD)
    if 0 < value < 1:
        context_length = int(getattr(getattr(agent, "provider", None), "context_length", 0) or 0)
        threshold = int(context_length * value) if context_length else 1000
    else:
        threshold = int(value)
    threshold = max(1000, threshold)
    return [{"type": "compaction", "compact_threshold": threshold}]


def _provider_metadata(agent) -> dict[str, str]:
    session = getattr(agent, "session", None)
    trace_ctx = getattr(agent, "_trace_context", None) or {}
    data = {
        "session_id": getattr(session, "id", ""),
        "trace_id": trace_ctx.get("trace_id", ""),
        "turn_id": trace_ctx.get("turn_id", ""),
        "run_id": getattr(agent, "_surface_run_id", ""),
    }
    return {key: str(value) for key, value in data.items() if value}


def _record_response_request_meta(session, response_state: dict) -> None:
    if session is None:
        return
    meta = {
        "enabled": bool(response_state.get("enabled")),
        "store": bool(response_state.get("store")),
        "send_previous": bool(response_state.get("send_previous", True)),
        "previous_response_id": str(response_state.get("previous_response_id") or ""),
    }
    try:
        session.meta["response_state_request"] = meta
    except Exception:  # noqa: BLE001
        pass


def _response_trace_data(resp, duration_ms: int) -> dict:
    raw = resp.raw if isinstance(resp.raw, dict) else {}
    output = raw.get("output") if isinstance(raw.get("output"), list) else []
    data: dict[str, Any] = {
        "finish_reason": resp.finish_reason,
        "input_tokens": getattr(resp.usage, "input_tokens", 0),
        "output_tokens": getattr(resp.usage, "output_tokens", 0),
        "tool_calls": len(resp.tool_calls),
        "duration_ms": duration_ms,
    }
    if raw:
        data.update({
            "response_id": raw.get("id", ""),
            "response_status": raw.get("status", ""),
            "output_item_count": len(output),
            "output_item_types": [str(item.get("type") or "") for item in output if isinstance(item, dict)],
        })
    return data


def _hook_json(value) -> str:
    if isinstance(value, (dict, list, tuple)):
        try:
            return json.dumps(value, sort_keys=True, default=str)
        except Exception:  # noqa: BLE001
            return str(value)
    if value is None:
        return ""
    return str(value)


def _shell_hook_context(payload: dict[str, Any]) -> dict[str, str]:
    return {str(key): _hook_json(value) for key, value in payload.items()}


def _observer_payload_copy(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return json.loads(json.dumps(payload, default=str))
    except Exception:  # noqa: BLE001
        return dict(payload)


def _provider_observer_base(
    agent,
    *,
    api_request_id: str,
    session,
    trace_id: str,
    turn_id: str,
    provider_span,
    request: dict,
) -> dict[str, Any]:
    span_id = ""
    if isinstance(provider_span, dict):
        span_id = str(provider_span.get("span_id") or "")
    provider = getattr(agent, "provider", None)
    return {
        "api_request_id": api_request_id,
        "session_id": str(getattr(session, "id", "") or ""),
        "trace_id": trace_id,
        "turn_id": turn_id,
        "run_id": str(getattr(agent, "_surface_run_id", "") or ""),
        "provider": str(getattr(provider, "name", "") or ""),
        "model": str(getattr(provider, "model", "") or ""),
        "api_mode": _trace_scalar(getattr(provider, "api_mode", "")),
        "span_id": span_id,
        "request": request,
    }


def _fire_provider_observer(agent, event: str, payload: dict[str, Any]) -> None:
    try:
        from ..plugins import fire_hook
        fire_hook(event, _observer_payload_copy(payload), agent)
    except Exception:  # noqa: BLE001
        pass
    try:
        from ..hooks import run_hooks
        run_hooks(agent.config, event, _shell_hook_context(payload))
    except Exception:  # noqa: BLE001
        pass


def _usage_cost_usd(model: str, usage, config) -> float:
    try:
        from ..usage_log import _price
        pin, pout = _price(model, config)
        cache_read = int(getattr(usage, "cache_read", 0) or 0)
        input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        fresh_in = max(0, input_tokens - cache_read)
        return round((fresh_in * pin + cache_read * pin * 0.1 + output_tokens * pout) / 1_000_000, 6)
    except Exception:  # noqa: BLE001
        return 0.0


def _trace_compaction(agent, session, rec: dict) -> None:
    trace_store = getattr(agent, "_trace_store", None)
    trace_ctx = getattr(agent, "_trace_context", None) or {}
    if not (trace_store and trace_ctx):
        return
    try:
        summarizer = _summarizer(agent)
        span = trace_store.start_span(
            trace_id=trace_ctx.get("trace_id"),
            session_id=trace_ctx.get("session_id", getattr(session, "id", "")),
            turn_id=trace_ctx.get("turn_id", ""),
            parent_span_id=trace_ctx.get("turn_span_id", ""),
            kind="compaction",
            provider=getattr(summarizer, "name", ""),
            model=getattr(summarizer, "model", ""),
            data=dict(rec),
        )
        trace_store.finish_span(span["span_id"], status="ok", data=dict(rec))
    except Exception:  # noqa: BLE001
        pass


class ToolExecutor:
    """Runs requested tool calls (concurrently), enforcing permissions per call."""

    def __init__(self, registry, permissions, ctx: ToolContext, on_event: OnEvent,
                 guard=None):
        self.registry = registry
        self.permissions = permissions
        self.ctx = ctx
        self.emit = on_event
        self.guard = guard          # per-turn ToolLoopGuard (None in bare/test usage)
        self._turn_checkpoint: str | None = None   # one checkpoint per turn's edit batch

    def _run_hooks(self, event: str, context: dict) -> None:
        cfg = getattr(self.ctx, "config", None)
        if cfg is None:
            return
        try:
            from ..hooks import run_hooks
            run_hooks(cfg, event, context)
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _edit_paths(call: ToolCall) -> list[str]:
        """File paths a mutating tool call is about to touch ([] for non-edits)."""
        if call.name in ("write_file", "edit_file"):
            p = call.arguments.get("path")
            return [p] if p else []
        if call.name == "apply_patch":
            from ..tools.extra_builtin import extract_patch_paths
            return extract_patch_paths(call.arguments.get("patch", "") or "")
        return []

    def _maybe_checkpoint(self, call: ToolCall) -> None:
        """Auto-checkpoint each turn's edit batch: the first edit of the turn opens a
        checkpoint (pre-turn state); later edits join it. /rollback undoes the batch,
        `aegis checkpoints diff` previews it."""
        cfg = getattr(self.ctx, "config", None)
        if cfg is None or not cfg.get("checkpoints.enabled", True):
            return
        paths = self._edit_paths(call)
        if not paths:
            return
        try:
            from ..checkpoints import CheckpointStore
            store = CheckpointStore(self.ctx.cwd)
            if self._turn_checkpoint:
                store.add_to(self._turn_checkpoint, paths)
            else:
                self._turn_checkpoint = store.snapshot(paths, label=f"turn edits ({call.name})")
        except Exception:  # noqa: BLE001
            pass

    def execute_one_raw(self, call: ToolCall) -> ToolResult:
        import time
        started = time.perf_counter()
        try:
            from ..plugins import fire_middleware
            payload = fire_middleware(
                "tool_request",
                {"tool": call.name, "arguments": dict(call.arguments or {}), "call": call},
                lambda p: p,
                getattr(self.ctx, "agent", None),
            )
            if isinstance(payload, dict):
                if payload.get("block"):
                    return ToolResult.error(str(payload.get("reason") or "blocked by plugin middleware"))
                if isinstance(payload.get("arguments"), dict):
                    call.arguments = payload["arguments"]
        except Exception:  # noqa: BLE001
            pass
        safe_args = redact_secret_values(call.arguments)
        self.emit({"type": "tool_start", "id": call.id, "name": call.name, "args": safe_args})
        trace_span = None
        trace_store = getattr(getattr(self.ctx, "agent", None), "_trace_store", None)
        trace_ctx = getattr(getattr(self.ctx, "agent", None), "_trace_context", None) or {}
        if trace_store and trace_ctx:
            try:
                trace_span = trace_store.start_span(
                    trace_id=trace_ctx.get("trace_id"),
                    session_id=trace_ctx.get("session_id", ""),
                    turn_id=trace_ctx.get("turn_id", ""),
                    parent_span_id=trace_ctx.get("turn_span_id", ""),
                    kind="tool",
                    tool_name=call.name,
                    data={"args": safe_args},
                )
            except Exception:  # noqa: BLE001
                trace_span = None
        self._run_hooks("pre_tool", {"tool": call.name, "args": str(safe_args)[:300]})
        self._maybe_checkpoint(call)
        blocked = self.guard.check(call.name, call.arguments) if self.guard else None
        tool = self.registry.get(call.name)
        if blocked:
            res = ToolResult.error(blocked)        # loop guard: don't run it again
        elif tool is None:
            res = ToolResult.error(f"unknown tool '{call.name}'")
        else:
            allowed, reason = self.permissions.authorize(tool, call.arguments, self.ctx)
            if not allowed:
                res = ToolResult.error(f"permission denied: {reason}")
            else:
                try:
                    from ..plugins import fire_middleware

                    payload = {
                        "tool": call.name,
                        "arguments": call.arguments,
                        "context": self.ctx,
                        "call": call,
                    }

                    def _run_tool(p):
                        args = p.get("arguments", call.arguments) if isinstance(p, dict) else call.arguments
                        return tool.run(args, self.ctx)

                    candidate = fire_middleware(
                        "tool_execution",
                        payload,
                        _run_tool,
                        getattr(self.ctx, "agent", None),
                    )
                    res = candidate if isinstance(candidate, ToolResult) else ToolResult.ok(str(candidate))
                except Exception as e:  # noqa: BLE001
                    res = ToolResult.error(f"tool raised {type(e).__name__}: {e}")
        if self.guard and not blocked:
            warn = self.guard.record(call.name, call.arguments, res.content, res.is_error)
            if warn:
                res.content = (res.content or "") + "\n\n" + warn
        duration_ms = int((time.perf_counter() - started) * 1000)
        artifact_ref = _artifact_ref(res.data)
        safe_summary = redact_secrets(res.summary)
        safe_preview = _preview(redact_secrets(res.content))
        safe_data = redact_secret_values(res.data) if isinstance(res.data, dict) else None
        self.emit({"type": "tool_result", "id": call.id, "name": call.name,
                   "summary": safe_summary, "is_error": res.is_error,
                   "classification": res.classification,
                   "preview": safe_preview,
                   "duration_ms": duration_ms,
                   "artifact_ref": artifact_ref,
                   "data": safe_data})
        if trace_store and trace_span:
            try:
                span_data = {
                    "summary": safe_summary,
                    "classification": res.classification,
                    "preview": safe_preview,
                    "is_error": bool(res.is_error),
                    "duration_ms": duration_ms,
                    "artifact_ref": artifact_ref,
                }
                if safe_data is not None:
                    span_data["result"] = safe_data
                trace_store.finish_span(
                    trace_span["span_id"],
                    status="error" if res.is_error else "ok",
                    data=span_data,
                    artifact_ref=artifact_ref,
                )
            except Exception:  # noqa: BLE001
                pass
        self._run_hooks("post_tool", {"tool": call.name, "is_error": str(res.is_error)})
        return res

    def _inline_spill_fallback(self, content: str, preview_chars: int) -> str:
        preview = content[:max(1, preview_chars)].rstrip()
        return (f"{_PERSISTED_OUTPUT_TAG}\n"
                f"This tool result was {_SPILL_MARKER}, but the full output could not "
                f"be saved to disk ({len(content):,} chars).\n\n"
                f"Preview (first {len(preview)} chars):\n{preview}\n"
                f"{_PERSISTED_OUTPUT_CLOSE}")

    def _spill_to_disk(self, call: ToolCall, content: str, *, preview_chars: int,
                       reason: str) -> str:
        import os
        import re
        import time
        from .. import config as cfg

        d = cfg.sub("tool_outputs")
        try:
            os.makedirs(d, exist_ok=True)
            cutoff = time.time() - 7 * 86400          # spills are scratch — prune old ones
            for old in os.listdir(d):
                p = os.path.join(d, old)
                try:
                    if os.path.getmtime(p) < cutoff:
                        os.unlink(p)
                except OSError:
                    continue
            safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", call.name or "tool")[:80]
            safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", call.id or "call")[:80]
            path = os.path.join(d, f"{safe_name}_{safe_id}.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception:  # noqa: BLE001
            return self._inline_spill_fallback(content, preview_chars)
        preview = content[:max(1, preview_chars)].rstrip()
        more = "\n..." if len(preview) < len(content) else ""
        return (f"{_PERSISTED_OUTPUT_TAG}\n"
                f"This tool result was {_SPILL_MARKER}: {reason} ({len(content):,} chars).\n"
                f"Full output saved to: {path}\n"
                "Use read_file with offset and limit to inspect specific sections.\n\n"
                f"Preview (first {len(preview)} chars):\n{preview}{more}\n"
                f"{_PERSISTED_OUTPUT_CLOSE}")

    def _maybe_spill(self, call: ToolCall, content: str, is_error: bool) -> str:
        """Spill an oversized tool output to disk; return a preview + reference path so
        a single huge result can't blow the context window (the agent can read_file it)."""
        cfg_obj = getattr(self.ctx, "config", None)
        if is_error or not content or cfg_obj is None:
            return content
        from ..util import estimate_tokens
        limit = int(cfg_obj.get("tools.max_result_tokens", 4000) or 0)
        if limit <= 0 or estimate_tokens(content) <= limit:
            return content
        return self._spill_to_disk(
            call,
            content,
            preview_chars=limit * 4,
            reason=f"single-output limit exceeded ({limit:,} estimated tokens)",
        )

    def _enforce_turn_result_budget(self, messages: list[Message]) -> list[Message]:
        """Aggregate clamp for many medium tool results in one turn."""
        cfg_obj = getattr(self.ctx, "config", None)
        if not messages or cfg_obj is None:
            return messages
        from ..util import estimate_tokens
        limit = int(cfg_obj.get("tools.max_turn_result_tokens", 50000) or 0)
        if limit <= 0:
            return messages
        sizes: list[tuple[int, int]] = []
        total = 0
        for i, msg in enumerate(messages):
            content = msg.content or ""
            tokens = estimate_tokens(content)
            total += tokens
            if content and _PERSISTED_OUTPUT_TAG not in content and _SPILL_MARKER not in content:
                sizes.append((i, tokens))
        if total <= limit:
            return messages
        preview_chars = int(cfg_obj.get("tools.turn_result_preview_chars", 1500) or 1500)
        for idx, tokens in sorted(sizes, key=lambda pair: pair[1], reverse=True):
            if total <= limit:
                break
            msg = messages[idx]
            original = msg.content or ""
            replacement = self._spill_to_disk(
                ToolCall(msg.tool_call_id or f"budget_{idx}", msg.name or "tool", {}),
                original,
                preview_chars=preview_chars,
                reason=f"tool-batch budget exceeded ({limit:,} estimated tokens)",
            )
            msg.content = replacement
            total += estimate_tokens(replacement) - tokens
        return messages

    def _scope_paths(self, call: ToolCall) -> list[Path] | None:
        if call.name not in _PATH_SCOPED_TOOLS:
            return []
        raw_paths: list[str] = []
        if call.name == "apply_patch":
            raw_paths = self._edit_paths(call)
        else:
            raw = call.arguments.get("path")
            if isinstance(raw, str) and raw.strip():
                raw_paths = [raw]
        if not raw_paths:
            return None
        scoped: list[Path] = []
        cwd = Path(getattr(self.ctx, "cwd", ".") or ".")
        for raw in raw_paths:
            expanded = Path(str(raw)).expanduser()
            if expanded.is_absolute():
                scoped.append(Path(os.path.abspath(str(expanded))))
            else:
                scoped.append(Path(os.path.abspath(str(cwd / expanded))))
        return scoped

    @staticmethod
    def _paths_overlap(left: Path, right: Path) -> bool:
        left_parts = left.parts
        right_parts = right.parts
        if not left_parts or not right_parts:
            return bool(left_parts) == bool(right_parts) and bool(left_parts)
        common_len = min(len(left_parts), len(right_parts))
        return left_parts[:common_len] == right_parts[:common_len]

    @staticmethod
    def _destructive_command(command: str) -> bool:
        return bool(
            command
            and (_DESTRUCTIVE_COMMAND_RE.search(command) or _REDIRECT_OVERWRITE_RE.search(command))
        )

    def _should_parallelize(self, calls: list[ToolCall]) -> bool:
        """Run only read-only or independent path-scoped batches concurrently."""
        if len(calls) <= 1:
            return False
        reserved: list[Path] = []
        for call in calls:
            name = call.name
            if name in _NEVER_PARALLEL_TOOLS:
                return False
            if name == "bash" and self._destructive_command(str(call.arguments.get("command") or "")):
                return False
            if name in _PATH_SCOPED_TOOLS:
                scoped = self._scope_paths(call)
                if scoped is None:
                    return False
                for path in scoped:
                    if any(self._paths_overlap(path, existing) for existing in reserved):
                        return False
                reserved.extend(scoped)
                continue
            if name.startswith("mcp__"):
                return False
            if name not in _PARALLEL_SAFE_TOOLS:
                return False
        return True

    def _run_one(self, call: ToolCall) -> Message:
        res = self.execute_one_raw(call)
        content = self._maybe_spill(call, redact_secrets(res.content), res.is_error)
        # Wrap results from external/untrusted sources so the model treats them as DATA,
        # not instructions (prompt-injection defense).
        tool = self.registry.get(call.name)
        is_untrusted = call.name.startswith("mcp__") or (tool and "network" in tool.groups)
        if content and not res.is_error and is_untrusted:
            content = (f'<untrusted_tool_result source="{call.name}">\n{content}\n'
                       f"</untrusted_tool_result>")
        # Subdirectory hints: local rule files for any new directory this call entered.
        if not res.is_error:
            try:
                from .subdir_hints import hints_for_call
                hint = hints_for_call(getattr(self.ctx, "agent", None), call.name,
                                      call.arguments, self.ctx.cwd)
                if hint:
                    content = (content or "") + hint
            except Exception:  # noqa: BLE001
                pass
        return Message.tool(call.id, call.name, content)

    def execute(self, calls: list[ToolCall]) -> list[Message]:
        if not calls:
            return []
        if len(calls) == 1:
            return self._enforce_turn_result_budget([self._run_one(calls[0])])
        if not self._should_parallelize(calls):
            return self._enforce_turn_result_budget([self._run_one(c) for c in calls])
        # Preserve order in results while running concurrently.
        results: list[Message | None] = [None] * len(calls)
        with ThreadPoolExecutor(max_workers=min(MAX_PARALLEL_TOOLS, len(calls))) as pool:
            futures = {pool.submit(self._run_one, c): i for i, c in enumerate(calls)}
            for fut in futures:
                results[futures[fut]] = fut.result()
        return self._enforce_turn_result_budget([r for r in results if r is not None])


def _next_in_lineage(title: str) -> str:
    """Auto-number a continuation session title: 'Task' -> 'Task (2)' -> 'Task (3)'."""
    import re
    m = re.match(r"^(.*?) \((\d+)\)$", title or "")
    if m:
        return f"{m.group(1)} ({int(m.group(2)) + 1})"
    return f"{(title or 'session').strip()} (2)"


def _summarizer(agent):
    """Provider used for compaction summaries — the cheap auxiliary model when configured,
    else the main provider. Built once and cached on the agent."""
    try:
        from ..auxiliary import router_for
        return router_for(agent).provider_for("compaction")
    except Exception:  # noqa: BLE001 - never block compaction on aux setup
        return agent.provider


def _drain_steering(agent, session) -> None:
    """Fold any guidance queued via ``agent.steer()`` into the conversation before the next
    model call — appended to the last tool message to preserve role alternation."""
    q = getattr(agent, "steer_queue", None)
    if q is None:
        return
    notes = []
    while not q.empty():
        try:
            notes.append(q.get_nowait())
        except Exception:  # noqa: BLE001
            break
    if not notes:
        return
    text = "\n\n".join(
        "[OUT-OF-BAND USER MESSAGE - direct user steering, not tool output]\n"
        f"{n}\n"
        "[/OUT-OF-BAND USER MESSAGE]"
        for n in notes
    )
    for m in reversed(session.messages):
        if m.role == "tool":
            m.content = (m.content or "") + "\n\n" + text
            return
    session.messages.append(Message.user(text))


def _tail_token_budget(agent, comp: dict, *, tight: bool = False) -> int:
    """Tokens of recent conversation to protect during compaction — a fraction of the
    model's window so the tail scales with the model, not a fixed message count. The
    overflow-recovery path uses a tighter fraction so the request actually shrinks."""
    ctx = getattr(agent.provider, "context_length", 0) or 0
    frac = comp.get("tail_fraction", 0.25)
    if tight:
        frac = min(frac, 0.12)
    return int(ctx * frac) if ctx > 0 else (3000 if tight else 6000)


def _ensure_compression_feasibility(agent, engine, comp: dict, emit: OnEvent | None = None) -> None:
    """Aux compression preflight.

    If the configured compression summarizer has a smaller context window than
    the main model threshold, lower the live default-engine threshold for this
    session so compaction starts while the summarizer can still read the window.
    """
    # Memoize per provider identity, not once per session: a mid-session model
    # switch changes the context window, so the preflight must re-run for the new
    # model instead of staying pinned to the first model's feasibility decision.
    provider_id = (
        getattr(agent.provider, "model", None),
        int(getattr(agent.provider, "context_length", 0) or 0),
    )
    if getattr(agent, "_compression_feasibility_checked", None) == provider_id:
        return
    agent._compression_feasibility_checked = provider_id
    main_ctx = int(getattr(agent.provider, "context_length", 0) or 0)
    if main_ctx <= 0:
        return
    from ..constants import COMPACT_THRESHOLD
    try:
        current_fraction = (
            engine.threshold_fraction() if hasattr(engine, "threshold_fraction")
            else comp.get("threshold", COMPACT_THRESHOLD)
        )
    except Exception:  # noqa: BLE001
        current_fraction = comp.get("threshold", COMPACT_THRESHOLD)
    try:
        threshold_fraction = float(COMPACT_THRESHOLD if current_fraction is None else current_fraction)
    except (TypeError, ValueError):
        threshold_fraction = 0.5
    threshold_tokens = max(1, int(main_ctx * threshold_fraction))
    try:
        summarizer = _summarizer(agent)
        aux_ctx = int(getattr(summarizer, "context_length", 0) or 0)
    except Exception:  # noqa: BLE001
        return
    if aux_ctx <= 0 or aux_ctx >= threshold_tokens:
        return
    new_fraction = max(0.01, min(threshold_fraction, aux_ctx / main_ctx))
    adjusted = False
    try:
        if hasattr(engine, "set_threshold_fraction"):
            engine.set_threshold_fraction(new_fraction)
            adjusted = True
    except Exception:  # noqa: BLE001
        adjusted = False
    rec = {
        "main_context_tokens": main_ctx,
        "old_threshold": threshold_fraction,
        "old_threshold_tokens": threshold_tokens,
        "aux_context_tokens": aux_ctx,
        "new_threshold": new_fraction,
        "new_threshold_tokens": int(main_ctx * new_fraction),
        "adjusted": adjusted,
        "aux_model": str(getattr(summarizer, "model", "") or ""),
        "aux_provider": str(getattr(summarizer, "name", "") or ""),
    }
    try:
        agent.session.meta["compression_feasibility"] = rec
    except Exception:  # noqa: BLE001
        pass
    if emit:
        emit({"type": "compression_feasibility", **rec})


def _memory_pre_compress_context(agent, session) -> str:
    if not agent.memory:
        return ""
    try:
        agent.memory.refresh_snapshot()
        note = agent.memory.on_pre_compress(session.messages)
    except Exception:  # noqa: BLE001
        return ""
    return note.strip() if isinstance(note, str) else ""


def _compression_lock_holder(agent, session) -> str:
    return f"{getattr(session, 'id', 'unknown')}:{new_id('lock')}"


def _acquire_compression_lock(agent, session, emit: OnEvent | None = None) -> tuple[bool, str | None]:
    store = getattr(agent, "store", None)
    sid = getattr(session, "id", "") or ""
    if store is None or not sid:
        return True, None
    holder = _compression_lock_holder(agent, session)
    try:
        acquired = store.try_acquire_compression_lock(sid, holder)
    except Exception:  # noqa: BLE001
        return True, None
    if acquired:
        return True, holder
    existing = None
    try:
        existing = store.get_compression_lock_holder(sid)
    except Exception:  # noqa: BLE001
        pass
    if emit:
        emit({
            "type": "compaction_skipped",
            "reason": "compression_lock_held",
            "session_id": sid,
            "holder": existing or "",
        })
    return False, None


def _release_compression_lock(agent, session, holder: str | None) -> None:
    if not holder:
        return
    store = getattr(agent, "store", None)
    sid = getattr(session, "id", "") or ""
    if store is None or not sid:
        return
    try:
        store.release_compression_lock(sid, holder)
    except Exception:  # noqa: BLE001
        pass


def _emit_session_compress(agent, emit: OnEvent | None, payload: dict[str, Any]) -> None:
    event = {"type": EventType.SESSION_COMPRESS, **payload}
    if emit is not None:
        try:
            emit(event)
        except Exception:  # noqa: BLE001
            pass
    callback = getattr(agent, "event_callback", None)
    if callable(callback):
        try:
            callback(EventType.SESSION_COMPRESS, dict(payload))
        except Exception:  # noqa: BLE001
            pass
    try:
        from ..hooks import run_hooks
        run_hooks(agent.config, EventType.SESSION_COMPRESS, _shell_hook_context(payload))
    except Exception:  # noqa: BLE001
        pass


def _force_compact(agent, session):
    """Compress unconditionally — recovery from a provider context_overflow. Tighter tail than
    the proactive path so the request actually shrinks below the window."""
    acquired, holder = _acquire_compression_lock(agent, session)
    if not acquired:
        agent._compact_stuck = True
        return session
    comp = agent.config.get("agent.compression", {}) or {}
    engine = _engine(agent)
    _ensure_compression_feasibility(agent, engine, comp)
    try:
        pre_compress_context = _memory_pre_compress_context(agent, session)
        try:
            from .context_engine import call_hook
            call_hook(engine, "on_pre_compress", agent, session)
        except Exception:  # noqa: BLE001
            pass
        before_n = len(session.messages)
        before_tok = compaction.estimated_tokens(session.messages)
        session.messages = governance.normalize(engine.compress(
            session.messages, _summarizer(agent),
            preserve_first=comp.get("preserve_first", 3),
            tail_tokens=_tail_token_budget(agent, comp, tight=True),
            max_tool_tokens=min(400, comp.get("max_tool_tokens", 600)),
            pre_compress_context=pre_compress_context,
            abort_on_summary_failure=bool(comp.get("abort_on_summary_failure", False)),
        ))
        after_tok = compaction.estimated_tokens(session.messages)
        from ..util import now_iso
        rec = {
            "at": now_iso(),
            "iteration": getattr(agent.budget, "api_call_count", 0),
            "messages_before": before_n,
            "messages_after": len(session.messages),
            "tokens_before": before_tok,
            "tokens_after": after_tok,
            "reason": "context_overflow",
            "recovery": True,
        }
        _trace_compaction(agent, session, rec)
        _emit_session_compress(agent, None, {
            "platform": getattr(agent, "platform", "") or "",
            "session_id": getattr(session, "id", "") or "",
            "old_session_id": getattr(session, "id", "") or "",
            "compression_count": len(session.meta.get("compactions", []) or []),
            **rec,
        })
        agent.refresh_volatile()
        return session
    except compaction.CompressionAborted:
        agent._compact_stuck = True
        return session
    finally:
        _release_compression_lock(agent, session, holder)


def _engine(agent):
    """The active context engine for this agent (cached)."""
    e = getattr(agent, "_context_engine", None)
    if e is None:
        from .context_engine import get_engine
        e = get_engine(agent.config)
        agent._context_engine = e
    return e


def _maybe_compact(agent, session, schema_tokens: int, budget, emit):
    """Proactively compact BEFORE the next model call if the window is near full. Returns the
    (possibly new child) session. Splits into a child session when a store + split are enabled."""
    # Once a compaction can't meaningfully shrink the window (e.g. the preserved tail itself
    # exceeds the threshold), stop retrying this turn — otherwise we'd burn a model call on a
    # no-op summary every iteration until the budget is exhausted.
    if getattr(agent, "_compact_stuck", False):
        return session
    engine = _engine(agent)
    comp = agent.config.get("agent.compression", {}) or {}
    _ensure_compression_feasibility(agent, engine, comp, emit)
    threshold = (
        engine.threshold_fraction() if hasattr(engine, "threshold_fraction")
        else comp.get("threshold")
    )   # for the record's reason text; the engine applies it
    if not engine.should_compress(session.messages, agent.provider.context_length, schema_tokens):
        return session
    lock_session = session
    acquired, holder = _acquire_compression_lock(agent, lock_session, emit)
    if not acquired:
        agent._compact_stuck = True
        return session
    emit({"type": "compacting"})
    pre_compress_context = _memory_pre_compress_context(agent, session)
    try:
        from .context_engine import call_hook
        call_hook(engine, "on_pre_compress", agent, session)
    except Exception:  # noqa: BLE001
        pass
    before_n = len(session.messages)
    before_tok = compaction.estimated_tokens(session.messages)
    try:
        compressed = engine.compress(
            session.messages, _summarizer(agent),    # summarize on the cheap aux model, not the main one
            preserve_first=comp.get("preserve_first", 3),
            tail_tokens=_tail_token_budget(agent, comp),   # token-budgeted tail, scales with the window
            max_tool_tokens=comp.get("max_tool_tokens", 600),
            pre_compress_context=pre_compress_context,
            abort_on_summary_failure=bool(comp.get("abort_on_summary_failure", False)),
        )
    except compaction.CompressionAborted as exc:
        from ..util import now_iso
        rec = {"at": now_iso(), "iteration": budget.api_call_count,
               "messages_before": before_n, "messages_after": before_n,
               "tokens_before": before_tok, "tokens_after": before_tok,
               "reason": "compression_aborted", "error": str(exc), "aborted": True}
        session.meta.setdefault("compactions", []).append(rec)
        _trace_compaction(agent, session, rec)
        agent._compact_stuck = True
        _release_compression_lock(agent, lock_session, holder)
        emit({"type": "compaction_aborted", **rec})
        return session
    except Exception:
        _release_compression_lock(agent, lock_session, holder)
        raise
    after_tok = compaction.estimated_tokens(compressed)
    # If we're STILL over the threshold after compacting, the preserved tail is the floor —
    # further compaction can't help, so don't retry this turn (avoids per-iteration thrash).
    still_over = engine.should_compress(compressed, agent.provider.context_length, schema_tokens)
    from ..constants import COMPACT_THRESHOLD
    from ..util import now_iso
    pct = int((COMPACT_THRESHOLD if threshold is None else threshold) * 100)
    rec = {"at": now_iso(), "iteration": budget.api_call_count,
           "messages_before": before_n, "messages_after": len(compressed),
           "tokens_before": before_tok, "tokens_after": after_tok,
           "reason": f"context exceeded {pct}% of the window"}
    if still_over:
        agent._compact_stuck = True
        rec["stuck"] = True

    if not still_over and comp.get("split_sessions", True) and agent.store is not None \
            and len(compressed) < before_n:
        from ..session import Session
        parent = session
        child = Session.create(title=_next_in_lineage(parent.title), parent_id=parent.id)
        child.messages = compressed
        _inherit_child_session_meta(parent, child)
        depth = int(parent.meta.get("lineage_depth", 0) or 0) + 1
        root = parent.meta.get("lineage_root") or parent.parent_id or parent.id
        parent.meta["end_reason"] = "compression"
        parent.meta.setdefault("child_sessions", []).append(child.id)
        child.meta["forked_from"] = parent.id
        child.meta["lineage_root"] = root
        child.meta["lineage_depth"] = depth
        child.meta["creator_kind"] = "compression"
        child.meta["reason"] = "context_compaction"
        child.meta["compression_depth"] = depth
        child.meta["parent_end_reason"] = "compression"
        child.meta["summary"] = parent.meta.get("summary", "")
        rec = {**rec, "split": True, "child_session": child.id, "parent_session": parent.id}
        child.meta.setdefault("compactions", []).append(rec)
        try:
            agent.store.save(parent)                    # preserve full parent history + end reason
            agent.store.save(child)                     # make continuation visible immediately
        except Exception:  # noqa: BLE001
            pass
        agent.switch_session(child, reason="compression")
        session = child
        try:
            from .context_engine import call_hook
            call_hook(engine, "on_session_switch", agent, parent, child, reason="compression")
        except Exception:  # noqa: BLE001
            pass
    else:
        session.messages = compressed
        session.meta.setdefault("compactions", []).append(rec)
    _trace_compaction(agent, session, rec)
    _emit_session_compress(agent, emit, {
        "platform": getattr(agent, "platform", "") or "",
        "session_id": getattr(session, "id", "") or "",
        "old_session_id": getattr(lock_session, "id", "") or "",
        "compression_count": len(session.meta.get("compactions", []) or []),
        **rec,
    })
    _release_compression_lock(agent, lock_session, holder)
    emit({"type": "compacted", **rec})
    agent.refresh_volatile()
    return session


def compact_now(agent, session=None, emit: OnEvent | None = None, *,
                reason: str = "manual", focus: str = "", preserve_last: int | None = None):
    """Force a user-requested compaction through the same context-engine lifecycle
    used by automatic compaction. Returns the active session, which may be a child
    session when split compaction is enabled."""
    emit = emit or (lambda _e: None)
    session = session or agent.session
    engine = _engine(agent)
    comp = agent.config.get("agent.compression", {}) or {}
    _ensure_compression_feasibility(agent, engine, comp, emit)
    lock_session = session
    acquired, holder = _acquire_compression_lock(agent, lock_session, emit)
    if not acquired:
        return session
    emit({"type": "compacting", "reason": reason})
    pre_compress_context = _memory_pre_compress_context(agent, session)
    try:
        from .context_engine import call_hook
        call_hook(engine, "on_pre_compress", agent, session)
    except Exception:  # noqa: BLE001
        pass

    before_n = len(session.messages)
    before_tok = compaction.estimated_tokens(session.messages)
    kwargs = {
        "preserve_first": comp.get("preserve_first", 3),
        "max_tool_tokens": comp.get("max_tool_tokens", 600),
    }
    if preserve_last is not None:
        kwargs["preserve_last"] = preserve_last
    else:
        kwargs["tail_tokens"] = _tail_token_budget(agent, comp)
    if focus:
        kwargs["focus"] = focus
    if pre_compress_context:
        kwargs["pre_compress_context"] = pre_compress_context
    kwargs["abort_on_summary_failure"] = bool(comp.get("abort_on_summary_failure", False))
    try:
        compressed = governance.normalize(engine.compress(session.messages, _summarizer(agent), **kwargs))
    except compaction.CompressionAborted as exc:
        from ..util import now_iso
        rec = {"at": now_iso(), "iteration": getattr(agent.budget, "api_call_count", 0),
               "messages_before": before_n, "messages_after": before_n,
               "tokens_before": before_tok, "tokens_after": before_tok,
               "reason": reason, "manual": True, "aborted": True, "error": str(exc)}
        session.meta.setdefault("compactions", []).append(rec)
        _trace_compaction(agent, session, rec)
        _release_compression_lock(agent, lock_session, holder)
        emit({"type": "compaction_aborted", **rec})
        return session
    except Exception:
        _release_compression_lock(agent, lock_session, holder)
        raise
    after_tok = compaction.estimated_tokens(compressed)

    from ..util import now_iso
    rec = {"at": now_iso(), "iteration": getattr(agent.budget, "api_call_count", 0),
           "messages_before": before_n, "messages_after": len(compressed),
           "tokens_before": before_tok, "tokens_after": after_tok,
           "reason": reason, "manual": True}
    if focus:
        rec["focus"] = focus

    if comp.get("split_sessions", True) and agent.store is not None and len(compressed) < before_n:
        from ..session import Session
        parent = session
        child = Session.create(title=_next_in_lineage(parent.title), parent_id=parent.id)
        child.messages = compressed
        _inherit_child_session_meta(parent, child)
        depth = int(parent.meta.get("lineage_depth", 0) or 0) + 1
        root = parent.meta.get("lineage_root") or parent.parent_id or parent.id
        parent.meta["end_reason"] = "manual_compression"
        parent.meta.setdefault("child_sessions", []).append(child.id)
        child.meta["forked_from"] = parent.id
        child.meta["lineage_root"] = root
        child.meta["lineage_depth"] = depth
        child.meta["creator_kind"] = "manual_compression"
        child.meta["reason"] = reason
        child.meta["compression_depth"] = depth
        child.meta["parent_end_reason"] = "manual_compression"
        child.meta["summary"] = parent.meta.get("summary", "")
        rec = {**rec, "split": True, "child_session": child.id, "parent_session": parent.id}
        child.meta.setdefault("compactions", []).append(rec)
        try:
            agent.store.save(parent)
            agent.store.save(child)
        except Exception:  # noqa: BLE001
            pass
        agent.switch_session(child, reason="manual_compression")
        session = child
        try:
            from .context_engine import call_hook
            call_hook(engine, "on_session_switch", agent, parent, child, reason="manual_compression")
        except Exception:  # noqa: BLE001
            pass
    else:
        session.messages = compressed
        session.meta.setdefault("compactions", []).append(rec)
        if agent.store is not None:
            try:
                agent.store.save(session)
            except Exception:  # noqa: BLE001
                pass
    _trace_compaction(agent, session, rec)
    _emit_session_compress(agent, emit, {
        "platform": getattr(agent, "platform", "") or "",
        "session_id": getattr(session, "id", "") or "",
        "old_session_id": getattr(lock_session, "id", "") or "",
        "compression_count": len(session.meta.get("compactions", []) or []),
        **rec,
    })
    _release_compression_lock(agent, lock_session, holder)
    emit({"type": "compacted", **rec})
    agent.refresh_volatile()
    return session


def run_conversation(agent, on_event: OnEvent | None = None) -> Message:
    """Drive one user turn to completion. Returns the final assistant message."""
    emit = on_event or (lambda e: None)
    session = agent.session
    # Memory freshness policy (memory.refresh):
    #   "frozen" (default) / "never" — keep the prompt prefix fixed until an
    #     explicit refresh/rebuild path such as /new, compaction, or a new process.
    #   "session" / "message" — if memory files changed since the last prompt
    #     snapshot, rebuild at the next turn so durable facts are visible.
    refresh_mode = (agent.config.get("memory.refresh", "frozen") or "frozen")
    memory_stale = (
        refresh_mode not in {"frozen", "never"}
        and agent.memory is not None
        and agent.memory.is_stale()
    )
    skills_stale = bool(
        getattr(agent, "skills", None) is not None
        and getattr(agent.skills, "is_stale", lambda: False)()
    )
    if memory_stale or skills_stale:
        agent.refresh_volatile()
    elif session.meta.pop("_rebuild_system_prompt", False):
        agent.refresh_volatile()
    else:
        agent.ensure_system_prompt()
    budget = agent.budget
    budget.reset()
    trace_store = None
    turn_span = None
    trace_id = new_id("trace")
    turn_id = new_id("turn")
    task_id = (
        getattr(agent, "_terminal_task_id", "")
        or getattr(getattr(agent, "tool_context", None), "task_id", "")
        or getattr(session, "id", "")
        or turn_id
    )
    try:
        agent._terminal_task_id = task_id
        agent.tool_context.task_id = task_id
    except Exception:  # noqa: BLE001
        pass
    prompt_meta = _prompt_trace_meta(session)
    from ..tracing import should_trace
    if should_trace(agent.config, trace_id):
        try:
            from ..tracing import TraceStore
            trace_store = TraceStore.from_config(agent.config)
            turn_span = trace_store.start_span(
                trace_id=trace_id,
                session_id=session.id,
                turn_id=turn_id,
                kind="turn",
                provider=getattr(agent.provider, "name", ""),
                model=getattr(agent.provider, "model", ""),
                data={"prompt": prompt_meta},
            )
            agent._trace_store = trace_store
            agent._trace_context = {
                "trace_id": trace_id,
                "turn_id": turn_id,
                "session_id": session.id,
                "turn_span_id": turn_span["span_id"],
            }
        except Exception:  # noqa: BLE001
            trace_store = None
    else:
        trace_id = ""
        agent._trace_store = None
        agent._trace_context = {}

    def _finish_turn(status: str = "ok", **updates) -> None:
        if trace_store and turn_span:
            try:
                trace_store.finish_span(turn_span["span_id"], status=status, **updates)
            except Exception:  # noqa: BLE001
                pass

    available = agent.registry.available(
        agent.config.get("tools.toolsets", ["core"]),
        disabled=agent.config.get("tools.disabled", []),
    )

    def _live_schemas():
        """Schemas for this iteration — deferred tools ship name-only (system-prompt
        index) until tool_search activates them, then their schemas join the wire."""
        deferred = agent.deferred_tool_names(available) if hasattr(agent, "deferred_tool_names") else set()
        return agent.registry.schemas([t for t in available if t.name not in deferred])

    schemas = _live_schemas()
    from .guardrails import ToolLoopGuard
    guard = ToolLoopGuard(
        warn_after=int(agent.config.get("tools.loop_warn_after", 3)),
        same_tool_warn_after=int(agent.config.get(
            "tools.loop_same_tool_warn_after",
            agent.config.get("tools.loop_warn_after", 3),
        )),
        block_after=int(agent.config.get("tools.loop_block_after", 5)),
    )
    executor = ToolExecutor(agent.registry, agent.permissions, agent.tool_context, emit, guard)
    continuations = 0
    empty_nudges = 0
    ultracode_continues = 0
    from ..util import estimate_tokens
    schema_tokens = estimate_tokens(json.dumps(schemas))   # tools count toward the window

    cancel = getattr(agent, "cancel_event", None)

    def _cancelled() -> bool:
        return cancel is not None and cancel.is_set()

    def _cancelled_result() -> Message:
        emit({"type": "cancelled"})
        stop = Message.assistant("[interrupted by user]")
        session.messages.append(stop)
        _finish_turn("cancelled")
        return stop

    while budget.should_continue():
        if _cancelled():
            return _cancelled_result()
        emit({"type": "iteration", "n": budget.api_call_count + 1, "max": budget.max_iterations})
        _drain_steering(agent, session)        # fold in any mid-run /steer guidance
        if len(fresh := _live_schemas()) != len(schemas):   # tool_search activated a deferred tool
            schemas = fresh
            schema_tokens = estimate_tokens(json.dumps(schemas))
        # Compact BEFORE the model call so an over-full window never reaches the provider,
        # then normalize AFTER so a compaction boundary can never ship a broken tool pair.
        session = _maybe_compact(agent, session, schema_tokens, budget, emit)
        session.messages = governance.normalize(session.messages)
        prompt_meta = _prompt_trace_meta(session)
        from ..plugins import fire_hook
        rewritten = fire_hook("pre_llm_call", session.messages, agent)   # in-process Python hook
        if isinstance(rewritten, list):
            session.messages = rewritten

        def delta_cb(text: str) -> None:
            emit({"type": "assistant_delta", "text": text})

        reasoned_live = {"v": False}

        def reasoning_cb(text: str) -> None:
            reasoned_live["v"] = True       # noqa: B023 — consumed within this iteration only
            emit({"type": "reasoning_delta", "text": text})

        # Provider-only volatile context tweaks use COPY messages. The canonical session
        # is never mutated: retrieved memory is wire-only, and persisting stripped
        # thinking blocks would corrupt future Anthropic turns.
        wire_messages = _provider_wire_messages(agent, session.messages)
        provider_span = None
        response_state = _response_state_for_agent(agent, getattr(agent.session, "id", ""))
        _record_response_request_meta(session, response_state)
        api_request_id = new_id("api")
        request_payload = _provider_trace_data(
            agent, wire_messages, schemas, response_state, prompt_meta
        )
        if trace_store and turn_span:
            try:
                provider_span = trace_store.start_span(
                    trace_id=trace_id,
                    session_id=session.id,
                    turn_id=turn_id,
                    parent_span_id=turn_span["span_id"],
                    kind="provider_call",
                    provider=getattr(agent.provider, "name", ""),
                    model=getattr(agent.provider, "model", ""),
                    data=request_payload,
                )
            except Exception:  # noqa: BLE001
                provider_span = None
        observer_base = _provider_observer_base(
            agent,
            api_request_id=api_request_id,
            session=session,
            trace_id=trace_id,
            turn_id=turn_id,
            provider_span=provider_span,
            request=request_payload,
        )
        _fire_provider_observer(agent, "pre_api_request", observer_base)
        fallback_attempts: list[dict[str, Any]] = []
        fallback_observers: dict[int, dict[str, Any]] = {0: observer_base}
        successful_fallback_attempt: int | None = None

        def _fallback_observer_base(
            attempt: dict[str, Any],
            *,
            _fallback_observers=fallback_observers,
            _request_payload=request_payload,
            _session=session,
            _provider_span=provider_span,
        ) -> dict[str, Any]:
            index = int(attempt.get("index", 0) or 0)
            if index not in _fallback_observers:
                request = dict(_request_payload)
                request["fallback_attempt"] = {
                    "index": index,
                    "provider": str(attempt.get("provider", "") or ""),
                    "model": str(attempt.get("model", "") or ""),
                }
                base = _provider_observer_base(
                    agent,
                    api_request_id=new_id("api"),
                    session=_session,
                    trace_id=trace_id,
                    turn_id=turn_id,
                    provider_span=_provider_span,
                    request=request,
                )
                _fallback_observers[index] = base
            base = dict(_fallback_observers[index])
            base.update({
                "provider": str(attempt.get("provider", "") or base.get("provider", "")),
                "model": str(attempt.get("model", "") or base.get("model", "")),
                "api_mode": str(attempt.get("api_mode", "") or base.get("api_mode", "")),
            })
            _fallback_observers[index] = base
            return base

        def _observe_provider_attempt(
            attempt: dict[str, Any],
            *,
            _fallback_attempts=fallback_attempts,
            _fallback_observer_base=_fallback_observer_base,
        ) -> None:
            nonlocal successful_fallback_attempt
            event = str(attempt.get("event", "") or "")
            index = int(attempt.get("index", 0) or 0)
            _fallback_attempts.append(dict(attempt))
            base = _fallback_observer_base(attempt)
            if event == "pre":
                if index > 0:
                    _fire_provider_observer(agent, "pre_api_request", base)
                return
            if event == "error":
                error = dict(attempt.get("error") or {})
                error.setdefault("duration_ms", int(attempt.get("duration_ms", 0) or 0))
                payload = dict(base)
                payload.update({
                    "status": "error",
                    "duration_ms": int(attempt.get("duration_ms", 0) or 0),
                    "error": error,
                })
                _fire_provider_observer(agent, "api_request_error", payload)
                return
            if event == "post":
                successful_fallback_attempt = index

        provider_started = time.perf_counter()
        emit({
            "type": "provider_start",
            "provider": getattr(agent.provider, "name", ""),
            "model": getattr(agent.provider, "model", ""),
            "stream": bool(agent.stream),
            "reasoning": getattr(agent, "reasoning", "off"),
            "reasoning_display": agent.config.get("display.reasoning", "summary"),
            "service_tier": getattr(agent, "service_tier", ""),
        })
        try:
            agent._active_response_id = ""
            agent._active_response_cancelled = ""
            provider_kwargs = {
                "stream": agent.stream,
                "on_delta": delta_cb,
                "reasoning": getattr(agent, "reasoning", "off"),
                "service_tier": getattr(agent, "service_tier", ""),
                "on_reasoning": reasoning_cb,
                "tool_runner": executor.execute_one_raw,
                "approver": getattr(agent.tool_context, "approver", None),
                "cwd": agent.cwd,
                "session_id": getattr(agent.session, "id", None),
                "response_state": response_state,
                "metadata": _provider_metadata(agent),
                "on_provider_attempt": _observe_provider_attempt,
                "on_response_id": lambda rid: setattr(agent, "_active_response_id", str(rid or "")),
            }
            request_max_tokens = _agent_request_max_tokens(agent)
            if request_max_tokens > 0:
                provider_kwargs["max_tokens"] = request_max_tokens
            from ..plugins import fire_middleware

            middleware_payload = fire_middleware(
                "llm_request",
                {
                    "provider": agent.provider,
                    "messages": wire_messages,
                    "tools": schemas,
                    "kwargs": provider_kwargs,
                    "request": request_payload,
                },
                lambda p: p,
                agent,
            )
            if isinstance(middleware_payload, dict):
                wire_messages = middleware_payload.get("messages", wire_messages)
                schemas = middleware_payload.get("tools", schemas)
                if isinstance(middleware_payload.get("kwargs"), dict):
                    provider_kwargs = middleware_payload["kwargs"]

            resp = fire_middleware(
                "llm_execution",
                {
                    "provider": agent.provider,
                    "messages": wire_messages,
                    "tools": schemas,
                    "kwargs": provider_kwargs,
                },
                lambda p: _provider_complete(
                    p.get("provider", agent.provider),
                    p.get("messages", wire_messages),
                    tools=p.get("tools", schemas),
                    **(p.get("kwargs", provider_kwargs) or {}),
                ),
                agent,
            )
            agent._active_response_id = ""
            if _cancelled():
                return _cancelled_result()
        except Exception as e:  # noqa: BLE001
            agent._active_response_id = ""
            from .._log import log_exc
            from ..providers.fallback import (
                classify_provider_error,
                recovery_action,
                reduce_long_context_tier,
            )
            action = recovery_action(classify_provider_error(e))
            provider_duration_ms = int((time.perf_counter() - provider_started) * 1000)
            emit({
                "type": "provider_end",
                "provider": getattr(agent.provider, "name", ""),
                "model": getattr(agent.provider, "model", ""),
                "duration_ms": provider_duration_ms,
                "status": "error",
            })
            error_data = {
                "type": type(e).__name__,
                "message": str(e),
                "recovery": action,
                "duration_ms": provider_duration_ms,
            }
            error_payload = dict(observer_base)
            error_payload.update({
                "status": "error",
                "duration_ms": provider_duration_ms,
                "error": error_data,
            })
            if not any(str(a.get("event") or "") == "error" for a in fallback_attempts):
                _fire_provider_observer(agent, "api_request_error", error_payload)
            # Signed thinking blocks were invalidated upstream -> resend without them (once).
            if action == "strip_thinking" and not getattr(agent, "_strip_thinking", False):
                if trace_store and provider_span:
                    try:
                        trace_store.finish_span(
                            provider_span["span_id"],
                            status="retrying",
                            data={
                                "error": f"{type(e).__name__}: {e}",
                                "error_type": type(e).__name__,
                                "recovery": "strip_thinking",
                                "duration_ms": provider_duration_ms,
                            },
                        )
                    except Exception:  # noqa: BLE001
                        pass
                agent._strip_thinking = True
                emit({"type": "thinking_strip_retry"})
                continue
            # context_overflow -> compact the session and retry once, instead of failing the turn.
            if (action == "compress" and not getattr(agent, "_overflow_retried", False)):
                context_reduction = reduce_long_context_tier(agent.provider, e)
                if trace_store and provider_span:
                    try:
                        data = {
                            "error": f"{type(e).__name__}: {e}",
                            "error_type": type(e).__name__,
                            "recovery": "compress",
                            "duration_ms": provider_duration_ms,
                        }
                        if context_reduction:
                            data["context_reduction"] = context_reduction
                        trace_store.finish_span(
                            provider_span["span_id"],
                            status="retrying",
                            data=data,
                        )
                    except Exception:  # noqa: BLE001
                        pass
                agent._overflow_retried = True
                event = {"type": "compacting", "reason": "context_overflow"}
                if context_reduction:
                    event["context_reduction"] = context_reduction
                emit(event)
                session = _force_compact(agent, session)
                continue
            if trace_store and provider_span:
                try:
                    trace_store.finish_span(
                        provider_span["span_id"],
                        status="error",
                        data={
                            "error": f"{type(e).__name__}: {e}",
                            "error_type": type(e).__name__,
                            "duration_ms": provider_duration_ms,
                        },
                    )
                except Exception:  # noqa: BLE001
                    pass
            log_exc("provider.complete failed")
            msg = f"{type(e).__name__}: {e}"
            try:
                from ..providers.registry import model_validation_message, validate_model_choice
                validation = validate_model_choice(
                    getattr(agent.provider, "name", ""),
                    getattr(agent.provider, "model", ""),
                    agent.config,
                )
                hint = model_validation_message(validation)
            except Exception:  # noqa: BLE001
                hint = ""
            low = str(e).lower()
            if hint:
                msg += f"\n  → {hint}"
            elif "not a chat model" in low or ("model" in low and ("404" in low or "does not exist" in low)):
                msg += ("\n  → That model isn't available on this endpoint/auth. Pick another with "
                        "`aegis model set <provider> <model>` (e.g. gpt-5.2-chat-latest for an API "
                        "key), or use `codex login` + `aegis model set codex gpt-5.5` "
                        "for ChatGPT-subscription models.")
            emit({"type": "error", "message": msg})
            err = Message.assistant(f"[provider error] {msg}")
            session.messages.append(err)
            _finish_turn("error", data={"error": msg})
            return err

        budget.api_call_count += 1
        budget.usage.add(resp.usage)
        provider_duration_ms = int((time.perf_counter() - provider_started) * 1000)
        emit({
            "type": "provider_end",
            "provider": getattr(agent.provider, "name", ""),
            "model": getattr(agent.provider, "model", ""),
            "duration_ms": provider_duration_ms,
            "status": "ok",
        })
        response_payload = _response_trace_data(resp, provider_duration_ms)
        if fallback_attempts:
            response_payload["fallback_attempts"] = fallback_attempts
        final_observer_base = fallback_observers.get(
            successful_fallback_attempt if successful_fallback_attempt is not None else 0,
            observer_base,
        )
        if trace_store and provider_span:
            try:
                final_provider = str(final_observer_base.get("provider", "") or getattr(agent.provider, "name", ""))
                final_model = str(final_observer_base.get("model", "") or getattr(agent.provider, "model", ""))
                trace_store.finish_span(
                    provider_span["span_id"],
                    status="ok",
                    provider=final_provider,
                    model=final_model,
                    cost=_usage_cost_usd(getattr(agent.provider, "model", ""), resp.usage, agent.config),
                    cache_read=getattr(resp.usage, "cache_read", 0),
                    cache_write=getattr(resp.usage, "cache_write", 0),
                    data=response_payload,
                )
            except Exception:  # noqa: BLE001
                pass
        success_payload = dict(final_observer_base)
        success_payload.update({
            "status": "ok",
            "duration_ms": provider_duration_ms,
            "response": response_payload,
        })
        _fire_provider_observer(agent, "post_api_request", success_payload)
        from .governance import strip_reasoning
        resp.text = strip_reasoning(resp.text)   # drop any inlined <think>…</think> blocks
        assistant_msg = resp.to_message()
        for tool_call in assistant_msg.tool_calls:
            tool_call.arguments = redact_secret_values(tool_call.arguments)
        session.messages.append(assistant_msg)
        if resp.reasoning and not reasoned_live["v"]:    # blocking path: emit once at the end
            emit({"type": "reasoning_delta", "text": resp.reasoning})
        reasoned_live["v"] = False
        emit({"type": "assistant_message", "text": resp.text,
              "tool_calls": [tc.to_dict() for tc in assistant_msg.tool_calls]})

        if not resp.tool_calls:
            # Auto-continue a response truncated by the output token limit (up to 3x).
            if resp.finish_reason in ("length", "max_tokens") and continuations < 3:
                continuations += 1
                emit({"type": "continuation", "n": continuations})
                session.messages.append(
                    Message.user("Continue exactly where you left off. Do not repeat or re-introduce."))
                continue
            # Empty reply after using tools = a dead-end turn; nudge it to continue (twice max)
            # rather than handing back nothing.
            if not (resp.text or "").strip() and agent.tools_used > 0 and empty_nudges < 2:
                empty_nudges += 1
                emit({"type": "empty_nudge", "n": empty_nudges})
                session.messages.append(Message.user(
                    "You returned an empty reply after using tools. Continue: take the next "
                    "action, or give the final answer."))
                continue
            # ULTRACODE continuation: the rigorous autonomous loop must not stop while the
            # plan still has open todo items. If the model tries to finish with incomplete
            # todos, push it to keep going (bounded, so it can't loop forever).
            if getattr(agent, "_ultracode_active", False):
                incomplete = [t for t in (session.todos or [])
                              if isinstance(t, dict) and t.get("status") != "completed"]
                if incomplete and ultracode_continues < _ULTRACODE_MAX_CONTINUES:
                    ultracode_continues += 1
                    emit({"type": "ultracode_continue", "n": ultracode_continues,
                          "remaining": len(incomplete)})
                    todo_lines = "\n".join(f"- {t.get('content', '')}" for t in incomplete[:10])
                    session.messages.append(Message.user(
                        "ULTRACODE — you still have incomplete todo items:\n" + todo_lines +
                        "\nDo not stop. Take the next item NOW on the real workspace (edit the "
                        "real files, run the real commands), then verify with real tool output. "
                        "Mark items completed via todo_write only once proven. Continue until "
                        "every item is done and the success criterion is met."))
                    continue
                # Done (or hit the cap): let it finalize and leave ultracode mode.
                agent._ultracode_active = False
            # No manual "save this as a skill" nudge: the forked background review
            # (agent/review.py) already creates skills automatically (learn.auto_apply_skills),
            # so prompting the user to do it by hand would be redundant and contradictory.
            final_text = resp.text
            if not (final_text or "").strip() and agent.tools_used > 0:
                # Nudges exhausted but still empty — hand back the last substantive reply
                # rather than nothing. The empty turn stays in the transcript.
                reused = _last_nonempty_assistant_text(session.messages, exclude=assistant_msg)
                if reused:
                    final_text = reused
                    emit({"type": "empty_reuse"})
            emit({"type": "final", "text": final_text})
            _finish_turn("ok", data={"text": final_text})
            return assistant_msg if final_text == resp.text else Message.assistant(final_text)

        results = executor.execute(resp.tool_calls)
        session.messages.extend(results)
        agent.tools_used += len(resp.tool_calls)
        # Todo staleness nudge: once the model starts a todo list, keep it honest.
        if any(tc.name == "todo_write" for tc in resp.tool_calls):
            session.meta["_last_todo_use"] = agent.tools_used
        elif (session.meta.get("_last_todo_use") is not None and results
                and agent.tools_used - session.meta["_last_todo_use"]
                >= int(agent.config.get("tools.todo_nudge_after", 15))):
            session.meta["_last_todo_use"] = agent.tools_used
            results[-1].content = (results[-1].content or "") + (
                "\n\n<system-reminder>The todo list hasn't been updated in a while. If the "
                "plan changed or items are done, update it with todo_write; if it's stale, "
                "clear it.</system-reminder>")
        # Refund the step for a pure "zero-context-cost" compute turn (only execute_code) so
        # code-heavy runs aren't penalized against the iteration budget.
        if resp.tool_calls and all(tc.name == "execute_code" for tc in resp.tool_calls):
            budget.refund()

        # Incremental persist so a crash mid-turn doesn't lose progress.
        if agent.store is not None:
            try:
                agent.store.save(session)
            except Exception:  # noqa: BLE001
                pass

    # Budget exhausted -> one grace call without tools for a final summary.
    emit({"type": "budget_exhausted"})
    session.messages.append(
        Message.user("You've reached the step limit. Stop and summarize what you accomplished "
                     "and what remains.")
    )
    grace_span = None
    grace_response_state = _response_state_for_agent(agent, getattr(agent.session, "id", ""))
    _record_response_request_meta(session, grace_response_state)
    grace_request = _provider_trace_data(
        agent,
        session.messages,
        [],
        grace_response_state,
        _prompt_trace_meta(session),
    )
    grace_request.update({
        "grace": True,
        "reason": "budget_exhausted",
        "tools_enabled": False,
        "budget_calls": budget.api_call_count,
    })
    if trace_store and turn_span:
        try:
            grace_span = trace_store.start_span(
                trace_id=trace_id,
                session_id=session.id,
                turn_id=turn_id,
                parent_span_id=turn_span["span_id"],
                kind="provider_call",
                provider=getattr(agent.provider, "name", ""),
                model=getattr(agent.provider, "model", ""),
                data=grace_request,
            )
        except Exception:  # noqa: BLE001
            grace_span = None
    grace_observer = _provider_observer_base(
        agent,
        api_request_id=new_id("api"),
        session=session,
        trace_id=trace_id,
        turn_id=turn_id,
        provider_span=grace_span,
        request=grace_request,
    )
    _fire_provider_observer(agent, "pre_api_request", grace_observer)
    grace_started = time.perf_counter()
    try:
        grace = _provider_complete(
            agent.provider,
            session.messages,
            tools=None,
            stream=agent.stream,
            on_delta=lambda t: emit({"type": "assistant_delta", "text": t}),
            reasoning=getattr(agent, "reasoning", "off"),
            service_tier=getattr(agent, "service_tier", ""),
            session_id=getattr(agent.session, "id", None),
            response_state=grace_response_state,
            metadata=_provider_metadata(agent),
            on_response_id=lambda rid: setattr(agent, "_active_response_id", str(rid or "")),
        )
        agent._active_response_id = ""
        if _cancelled():
            return _cancelled_result()
        budget.usage.add(grace.usage)
        grace_duration_ms = int((time.perf_counter() - grace_started) * 1000)
        grace_response = _response_trace_data(grace, grace_duration_ms)
        if trace_store and grace_span:
            try:
                trace_store.finish_span(
                    grace_span["span_id"],
                    status="ok",
                    cost=_usage_cost_usd(getattr(agent.provider, "model", ""), grace.usage, agent.config),
                    cache_read=getattr(grace.usage, "cache_read", 0),
                    cache_write=getattr(grace.usage, "cache_write", 0),
                    data=grace_response,
                )
            except Exception:  # noqa: BLE001
                pass
        success_payload = dict(grace_observer)
        success_payload.update({
            "status": "ok",
            "duration_ms": grace_duration_ms,
            "response": grace_response,
        })
        _fire_provider_observer(agent, "post_api_request", success_payload)
        gm = grace.to_message()
    except Exception as e:  # noqa: BLE001
        agent._active_response_id = ""
        from ..providers.fallback import classify_provider_error, recovery_action
        grace_duration_ms = int((time.perf_counter() - grace_started) * 1000)
        action = recovery_action(classify_provider_error(e))
        error_data = {
            "type": type(e).__name__,
            "message": str(e),
            "recovery": action,
            "duration_ms": grace_duration_ms,
        }
        error_payload = dict(grace_observer)
        error_payload.update({
            "status": "error",
            "duration_ms": grace_duration_ms,
            "error": error_data,
        })
        _fire_provider_observer(agent, "api_request_error", error_payload)
        if trace_store and grace_span:
            try:
                trace_store.finish_span(
                    grace_span["span_id"],
                    status="error",
                    data={
                        "error": f"{type(e).__name__}: {e}",
                        "error_type": type(e).__name__,
                        "recovery": action,
                        "duration_ms": grace_duration_ms,
                    },
                )
            except Exception:  # noqa: BLE001
                pass
        gm = Message.assistant(f"[step limit reached; summary failed: {e}]")
    session.messages.append(gm)
    grace_text = gm.content
    if not (grace_text or "").strip():
        # Grace summary came back empty — reuse the last substantive reply.
        reused = _last_nonempty_assistant_text(session.messages, exclude=gm)
        if reused:
            grace_text = reused
            emit({"type": "empty_reuse"})
    emit({"type": "final", "text": grace_text})
    _finish_turn("budget_exhausted", data={"text": grace_text})
    return Message.assistant(grace_text) if grace_text != gm.content else gm
