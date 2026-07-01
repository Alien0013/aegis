"""Higher-order tools: subagent spawning and image generation."""

from __future__ import annotations

import base64
import copy
import logging
import threading
import time

import httpx

from ..types import new_id
from ..util import slugify
from .base import Tool, ToolContext, ToolResult

logger = logging.getLogger(__name__)

_NO_TOOLSETS = ["__none__"]
_DELEGATION_TOOLS = {"spawn_subagent", "delegate_task"}
_CHILD_BLOCKED_TOOLS = {"clarify", "memory", "send_message", "execute_code", "cronjob"}
_LEAF_BLOCKED_TOOLS = _CHILD_BLOCKED_TOOLS | _DELEGATION_TOOLS
_TOOLSET_ALIASES = {
    "default": ["core"],
    "guided/default": ["core"],
}

# Process-global registry of spawned subagents (id -> {status, task}) for observability and a
# bounded view of recent children. Capped so it can't grow without bound.
_REGISTRY: dict[str, dict] = {}
_REG_LOCK = threading.Lock()


def _clear_terminal_backend_override(sid: str) -> None:
    if not sid:
        return
    try:
        from .backends import clear_task_env_overrides

        clear_task_env_overrides(sid)
    except Exception:  # noqa: BLE001
        pass


def _close_registry_entry(sid: str, entry: dict | None) -> None:
    if not entry:
        return
    agent = entry.get("agent")
    if agent is not None:
        try:
            from ..surface import _close_agent
            _close_agent(agent)
        except Exception:  # noqa: BLE001
            pass
    _clear_terminal_backend_override(sid)


def _register(sid: str, **fields) -> None:
    evicted: list[tuple[str, dict]] = []
    with _REG_LOCK:
        _REGISTRY.setdefault(sid, {}).update(fields)
        if len(_REGISTRY) > 200:                       # drop oldest
            for k in list(_REGISTRY)[:len(_REGISTRY) - 200]:
                evicted.append((k, _REGISTRY.pop(k, None) or {}))
    for evicted_sid, entry in evicted:
        _close_registry_entry(evicted_sid, entry)


def list_subagents(*, active_only: bool = False) -> list[dict]:
    with _REG_LOCK:
        rows = []
        for sid, entry in _REGISTRY.items():
            if active_only and entry.get("status") not in {"running", "cancelling"}:
                continue
            rows.append({k: v for k, v in entry.items() if k != "agent"} | {"id": sid})
    return rows


def list_active_subagents() -> list[dict]:
    return list_subagents(active_only=True)


def cancel_subagent(subagent_id: str, reason: str = "cancelled") -> dict:
    needle = str(subagent_id or "").strip()
    if not needle:
        return {"ok": False, "error": "subagent id is required"}
    with _REG_LOCK:
        matches = [(sid, entry) for sid, entry in _REGISTRY.items() if sid.startswith(needle)]
        if not matches:
            return {"ok": False, "error": f"subagent not found: {subagent_id}"}
        sid, entry = matches[0]
        entry["status"] = "cancelling"
        entry["cancel_reason"] = reason
        agent = entry.get("agent")
    if agent is not None:
        try:
            cancel = getattr(agent, "cancel", None)
            if callable(cancel):
                cancel()
            cancel_event = getattr(agent, "cancel_event", None)
            if cancel_event is not None:
                cancel_event.set()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "id": sid, "error": f"cancel failed: {type(exc).__name__}: {exc}"}
    return {"ok": True, "id": sid, "status": "cancelling"}


def _notify_delegation(parent, task: str, result: str) -> None:
    parent_mem = getattr(parent, "memory", None)
    if parent_mem is None:
        return
    try:
        parent_mem.on_delegation(task, result)
    except Exception:  # noqa: BLE001
        pass


def _relay_subagent_stream_event(ctx: ToolContext, identity: dict, event: dict) -> None:
    etype = str((event or {}).get("type") or "")
    if etype == "assistant_delta":
        text = str(event.get("text") or "")
        if text:
            ctx.emit_event(
                type="subagent_text",
                **identity,
                text=text,
            )
        return
    if etype == "reasoning_delta":
        text = str(event.get("text") or "")
        if text:
            ctx.emit_event(
                type="subagent_reasoning",
                **identity,
                text=text,
            )


def _subagent_terminal_backend(config) -> str:
    try:
        backend = str(config.get("tools.subagent_terminal_backend", "") or "").strip().lower()
    except Exception:  # noqa: BLE001
        return ""
    return "" if backend in {"", "inherit", "parent"} else backend


def _register_terminal_backend_override(sid: str, backend: str) -> None:
    if not sid or not backend:
        return
    try:
        from .backends import register_task_env_overrides

        register_task_env_overrides(sid, {"terminal_backend": backend})
    except Exception:  # noqa: BLE001
        pass


def _delegation_model(config) -> tuple[str, str]:
    """Default (provider, model) for delegated subagents — config delegation.provider /
    delegation.model. Lets subagents run on a cheaper/faster model than the parent."""
    try:
        provider = str(config.get("delegation.provider", "") or "").strip()
        model = str(config.get("delegation.model", "") or "").strip()
    except Exception:  # noqa: BLE001
        return "", ""
    return provider, model


def _clean_toolsets(value) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        value = [value]
    out: list[str] = []
    for item in value:
        if isinstance(item, str):
            clean = item.strip()
            if clean and clean not in out:
                out.append(clean)
    return out


def _expand_toolset_aliases(toolsets: list[str]) -> list[str]:
    out: list[str] = []
    for item in toolsets:
        expanded = _TOOLSET_ALIASES.get(item, [item])
        for name in expanded:
            if name not in out:
                out.append(name)
    return out


def _known_toolsets() -> set[str]:
    names = {"core", "all", "*", *_TOOLSET_ALIASES}
    try:
        from .registry import default_registry

        for tool in default_registry().all():
            toolset = str(getattr(tool, "toolset", "") or "core").strip()
            if toolset:
                names.add(toolset)
    except Exception:  # noqa: BLE001
        pass
    return names


def _parent_available_toolsets(config) -> set[str]:
    parent = _expand_toolset_aliases(_clean_toolsets(config.get("tools.toolsets", []) or ["core"]))
    enabled = set(parent)
    if enabled & {"all", "*"}:
        return _known_toolsets()
    try:
        from .registry import default_registry

        for tool in default_registry().available(
            parent,
            only_usable=False,
            disabled=config.get("tools.disabled", []),
        ):
            toolset = str(getattr(tool, "toolset", "") or "core").strip()
            if toolset:
                enabled.add(toolset)
    except Exception:  # noqa: BLE001
        pass
    return enabled


def _scoped_child_toolsets(config, requested_toolsets) -> list[str]:
    parent = _expand_toolset_aliases(
        _clean_toolsets(config.get("tools.toolsets", []) or ["core"])
    ) or ["core"]
    requested = _expand_toolset_aliases(_clean_toolsets(requested_toolsets))
    if not requested:
        return list(parent)
    parent_enabled = _parent_available_toolsets(config)
    if any(item in {"all", "*"} for item in requested):
        return [item for item in parent if item not in {"all", "*"}] or list(_NO_TOOLSETS)
    known = _known_toolsets()
    child = [item for item in requested if item in parent_enabled and item in known]
    return child or list(_NO_TOOLSETS)


def _child_config_for_toolsets(config, requested_toolsets):
    deleg_provider, deleg_model = _delegation_model(config)
    if not requested_toolsets and not deleg_provider and not deleg_model:
        return config
    try:
        from ..config import Config
    except Exception:  # noqa: BLE001
        return config

    data = copy.deepcopy(getattr(config, "data", {}) or {})
    if requested_toolsets:
        data.setdefault("tools", {})["toolsets"] = _scoped_child_toolsets(config, requested_toolsets)
    if deleg_provider or deleg_model:        # run delegated work on the configured model
        model_cfg = data.setdefault("model", {})
        if deleg_provider:
            model_cfg["provider"] = deleg_provider
        if deleg_model:
            model_cfg["default"] = deleg_model
    return Config(data)


def _child_runtime_config(config, requested_toolsets):
    child = _child_config_for_toolsets(config, requested_toolsets)
    try:
        skip_memory = _truthy(config.get("delegation.skip_memory", True))
    except Exception:  # noqa: BLE001
        skip_memory = True
    if not skip_memory:
        return child
    try:
        from ..config import Config
    except Exception:  # noqa: BLE001
        return child
    data = copy.deepcopy(getattr(child, "data", {}) or {})
    if not requested_toolsets:
        data.setdefault("tools", {})["toolsets"] = _scoped_child_toolsets(config, None)
    data.setdefault("memory", {})["enabled"] = False
    return Config(data)


def _model_provider_from_config(config) -> tuple[str, str]:
    try:
        provider = str(config.get("model.provider", "") or "").strip()
        model = str(config.get("model.default", "") or "").strip()
    except Exception:  # noqa: BLE001
        return "", ""
    return provider, model


def _event_identity(
    *,
    sid: str,
    task: str,
    agent_type: str,
    role: str,
    depth: int,
    parent_session_id: str = "",
    child_session_id: str = "",
    toolsets: list[str] | None = None,
    provider: str = "",
    model: str = "",
    background: bool = False,
) -> dict:
    out = {
        "id": sid,
        "subagent_id": sid,
        "task": task[:240],
        "agent_type": agent_type,
        "role": role,
        "depth": depth,
    }
    if parent_session_id:
        out["parent_session_id"] = parent_session_id
    if child_session_id:
        out["child_session_id"] = child_session_id
        out["session_id"] = child_session_id
    if toolsets is not None:
        out["toolsets"] = list(toolsets)
    if provider:
        out["provider"] = provider
    if model:
        out["model"] = model
    if background:
        out["background"] = True
    return out


def _safe_int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _safe_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _result_provider_model(result) -> tuple[str, str]:
    agent = getattr(result, "agent", None)
    provider = str(getattr(getattr(agent, "provider", None), "name", "") or "")
    model = str(getattr(getattr(agent, "provider", None), "model", "") or "")
    session = getattr(result, "session", None)
    meta = getattr(session, "meta", {}) if session is not None else {}
    if isinstance(meta, dict):
        provider = provider or str(meta.get("provider") or "")
        model = model or str(meta.get("model") or "")
    return provider, model


def _subagent_observability(result) -> dict:
    """Reference-style child completion metadata for activity surfaces."""
    out: dict = {}
    usage = getattr(result, "usage", None)
    if usage is not None:
        provider, model = _result_provider_model(result)
        try:
            from .. import usage_log

            summary = usage_log.usage_summary(provider, model, usage)
        except Exception:  # noqa: BLE001
            input_tokens = _safe_int(getattr(usage, "input_tokens", 0))
            output_tokens = _safe_int(getattr(usage, "output_tokens", 0))
            cache_read = _safe_int(getattr(usage, "cache_read", 0))
            cache_write = _safe_int(getattr(usage, "cache_write", 0))
            summary = {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read": cache_read,
                "cache_write": cache_write,
                "prompt_tokens": input_tokens + cache_read + cache_write,
                "total_tokens": input_tokens + output_tokens + cache_read + cache_write,
            }
        usage_payload = {
            "input_tokens": _safe_int(summary.get("input_tokens")),
            "output_tokens": _safe_int(summary.get("output_tokens")),
            "cache_read": _safe_int(summary.get("cache_read")),
            "cache_write": _safe_int(summary.get("cache_write")),
            "cache_read_tokens": _safe_int(summary.get("cache_read")),
            "cache_write_tokens": _safe_int(summary.get("cache_write")),
            "prompt_tokens": _safe_int(summary.get("prompt_tokens")),
            "total_tokens": _safe_int(summary.get("total_tokens")),
        }
        out.update(usage_payload)
        out["usage"] = dict(usage_payload)

    agent = getattr(result, "agent", None)
    cost = getattr(agent, "_last_turn_cost", None)
    if not isinstance(cost, dict):
        session = getattr(result, "session", None)
        meta = getattr(session, "meta", {}) if session is not None else {}
        usage_meta = meta.get("usage", {}) if isinstance(meta, dict) else {}
        cost = usage_meta.get("last_turn_cost", {}) if isinstance(usage_meta, dict) else {}
    if isinstance(cost, dict) and cost:
        amount = cost.get("amount_usd")
        if amount is None:
            amount = cost.get("cost_usd", cost.get("cost", cost.get("estimated_cost_usd")))
        amount = _safe_float(amount) if amount is not None else None
        if amount is not None:
            out["cost_usd"] = amount
        for key in ("cost_status", "cost_source", "pricing_source", "cost_label"):
            if cost.get(key) is not None:
                out[key] = cost[key]
    elif usage is not None:
        amount = _safe_float(getattr(usage, "cost", None))
        if amount is not None:
            out["cost_usd"] = amount
            out["cost_status"] = "actual"
            out["cost_source"] = "provider_generation_api"
    return out


def _max_spawn_depth(config) -> int:
    try:
        return max(1, int(config.get("agent.max_spawn_depth", 1) or 1))
    except Exception:  # noqa: BLE001
        return 1


def _subagent_concurrency(config) -> int:
    """How many parallel subagents may run at once. Honors delegation.max_concurrent_children
    (legacy name), falling back to the existing agent.subagent_concurrency (default 4)."""
    for key in ("delegation.max_concurrent_children", "agent.subagent_concurrency"):
        try:
            val = config.get(key)
        except Exception:  # noqa: BLE001
            val = None
        if val:
            try:
                return max(1, int(val))
            except (TypeError, ValueError):
                continue
    return 4


def _child_timeout(config) -> float:
    """Per-child wall-clock budget in seconds (delegation.child_timeout_seconds); 0 = unlimited."""
    try:
        return max(0.0, float(config.get("delegation.child_timeout_seconds", 0) or 0))
    except (TypeError, ValueError):
        return 0.0


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _default_background_for_parent(parent) -> bool:
    if parent is None:
        return False
    if getattr(parent, "_depth", 0) > 0:
        return False
    return getattr(parent, "tool_context", None) is not None


def _subagent_auto_approve(config) -> bool:
    """Child approval knob: safe auto-deny by default."""
    try:
        val = config.get("delegation.subagent_auto_approve", None)
    except Exception:  # noqa: BLE001
        val = None
    if val is None:
        try:
            val = config.get("agent.subagent_auto_approve", False)
        except Exception:  # noqa: BLE001
            val = False
    return _truthy(val)


def _subagent_approver(config, sid: str):
    auto_approve = _subagent_auto_approve(config)

    def _approve(prompt: str) -> bool:
        action = "approved" if auto_approve else "denied"
        logger.warning(
            "Subagent %s auto-%s permission prompt: %s",
            sid or "unknown",
            action,
            str(prompt or "")[:300],
        )
        return auto_approve

    return _approve


# Typed subagents: a named type = a tool whitelist + a role preamble. Read-only types
# can fan out aggressively because they cannot modify anything.
_READONLY_TOOLS = {
    "read_file", "list_dir", "glob", "search", "web_fetch", "web_search",
    "session_search", "tool_search", "tool_describe", "skill", "system_status", "lsp",
}


class _ReadOnlySkillTool(Tool):
    name = "skill"
    description = "Read skills only. action: list | view | stats. Cannot create or improve skills."
    parameters = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["list", "view", "stats"]},
            "name": {"type": "string", "description": "skill name for view"},
        },
        "required": ["action"],
    }

    def run(self, args, ctx) -> ToolResult:
        if ctx.skills is None:
            return ToolResult.error("skills are not available.")
        action = args.get("action")
        if action == "list":
            return ToolResult.ok(ctx.skills.index_block() or "(no skills)", display="listed skills")
        if action == "stats":
            usage = ctx.skills.usage()
            if not usage:
                return ToolResult.ok("(no skill usage recorded yet)", display="skill stats")
            rows = sorted(usage.items(), key=lambda kv: -kv[1].get("count", 0))
            return ToolResult.ok("\n".join(f"{n}: used {u['count']}x (last {u.get('last_used','?')})"
                                           for n, u in rows), display="skill stats")
        if action != "view":
            return ToolResult.error("read-only subagents can only list, view, or inspect skill stats.")
        name = args.get("name")
        if not name:
            return ToolResult.error("name is required for view.")
        body = ctx.skills.activate(name)
        if body is None:
            return ToolResult.error(f"skill '{name}' not found.")
        return ToolResult.ok(body, display=f"loaded skill {name}")
AGENT_TYPES: dict[str, dict] = {
    "general": {"tools": None, "preamble": ""},
    "explore": {"tools": _READONLY_TOOLS, "preamble":
                "You are a READ-ONLY explore agent. Locate and report — never modify. "
                "Return only the conclusions (paths, names, facts), not file dumps.\n\n"},
    "plan":    {"tools": _READONLY_TOOLS, "preamble":
                "You are a READ-ONLY planning architect. Investigate, then return a concrete "
                "step-by-step implementation plan: files to touch, order of changes, risks, "
                "and how to verify each step. Do NOT make any change yourself.\n\n"},
    "review":  {"tools": _READONLY_TOOLS, "preamble":
                "You are a READ-ONLY code reviewer. Report every issue you find with "
                "file:line, severity, and a one-line fix suggestion. Do not edit anything.\n\n"},
}


def _role_prompt(agent_type: str, spec: dict) -> str:
    return str(spec.get("preamble") or "").strip()


def _seed_role_prompt(session, agent_type: str, role_prompt: str) -> bool:
    changed = False
    if agent_type and session.meta.get("agent_type") != agent_type:
        session.meta["agent_type"] = agent_type
        changed = True
    if role_prompt and session.meta.get("subagent_role_prompt") != role_prompt:
        session.meta["subagent_role_prompt"] = role_prompt
        changed = True
    return changed


class SubagentTool(Tool):
    name = "spawn_subagent"
    description = (
        "Delegate self-contained sub-task(s) to fresh child agents, each with its own context. "
        "Pass `task` for one, or `tasks` (array) to run several IN PARALLEL (bounded). "
        "agent_type picks a specialist: explore/plan/review are READ-ONLY (safe to fan out), "
        "general (default) has normal child tools except shared side-effect/recurse tools "
        "(clarify, memory, send_message, execute_code, and nested spawn unless orchestrator). "
        "Pass continue_id to follow up with a previous subagent (it keeps its context). "
        "Returns each child's final answer."
    )
    groups = ["automation"]
    extra_toolsets = ["delegation"]
    parameters = {
        "type": "object",
        "properties": {
            "task": {"type": "string", "description": "One complete, self-contained instruction."},
            "tasks": {"type": "array", "items": {"type": "string"},
                      "description": "Several self-contained instructions, run in parallel."},
            "agent_type": {"type": "string", "enum": ["general", "explore", "plan", "review"],
                           "description": "Specialist type (explore/plan/review are read-only)."},
            "role": {"type": "string", "enum": ["leaf", "orchestrator"],
                     "description": "leaf cannot spawn children; orchestrator may if depth budget allows."},
            "continue_id": {"type": "string",
                            "description": "id of a previous subagent — sends `task` to it as a "
                                           "follow-up with its context intact."},
            "toolsets": {"type": "array", "items": {"type": "string"},
                         "description": "Toolsets the children may use (default: core)."},
            "background": {"type": "boolean",
                           "description": "Return immediately and run the task in the background; "
                                          "results are announced back when done."},
        },
    }

    def run(self, args, ctx: ToolContext) -> ToolResult:
        from ..agent.agent import Agent
        from ..session import Session

        parent = ctx.agent
        depth = (getattr(parent, "_depth", 0) if parent else 0) + 1
        config = ctx.config
        if config is None:
            return ToolResult.error("no config available for subagent.")
        max_depth = _max_spawn_depth(config)
        if depth > max_depth:
            return ToolResult.error(f"subagent depth limit reached (max {max_depth}).")
        tasks = list(args.get("tasks") or ([] if args.get("task") is None else [args["task"]]))
        tasks = [t for t in tasks if isinstance(t, str) and t.strip()]
        if not tasks:
            return ToolResult.error("provide `task` (string) or `tasks` (array of strings).")
        toolsets = args.get("toolsets")
        atype = args.get("agent_type") or "general"
        spec = AGENT_TYPES.get(atype)
        if spec is None:
            return ToolResult.error(f"unknown agent_type '{atype}' "
                                    f"(use {', '.join(AGENT_TYPES)})")
        role = str(args.get("role") or "leaf").strip().lower()
        if role not in {"leaf", "orchestrator"}:
            role = "leaf"
        background_arg = args.get("background")
        background = (
            _default_background_for_parent(parent)
            if background_arg is None
            else _truthy(background_arg)
        )

        if args.get("continue_id"):                       # follow-up to a previous child
            with _REG_LOCK:
                entry = _REGISTRY.get(args["continue_id"], {})
                child = entry.get("agent")
            if child is None:
                return ToolResult.error(f"no continuable subagent '{args['continue_id']}' "
                                        "(it may have been evicted)")
            try:
                from ..surface import SurfaceRunner, apply_session_runtime, inherit_session_runtime

                role_type = str(entry.get("type") or atype)
                role_spec = AGENT_TYPES.get(role_type, spec)
                terminal_backend = str(entry.get("terminal_backend") or _subagent_terminal_backend(config))
                _register_terminal_backend_override(args["continue_id"], terminal_backend)
                child_session = getattr(child, "session", None)
                inherit_session_runtime(getattr(parent, "session", None), child_session)
                apply_session_runtime(child)
                try:
                    child.tool_context.approver = _subagent_approver(config, args["continue_id"])
                except Exception:  # noqa: BLE001
                    pass
                if child_session is not None and _seed_role_prompt(
                    child_session, role_type, _role_prompt(role_type, role_spec)
                ):
                    try:
                        child.refresh_volatile()
                    except Exception:  # noqa: BLE001
                        pass
                identity = _event_identity(
                    sid=args["continue_id"],
                    task=tasks[0],
                    agent_type=role_type,
                    role=str(entry.get("role") or "leaf"),
                    depth=int(entry.get("depth") or getattr(child, "_depth", 0) or 0),
                    parent_session_id=str(entry.get("parent_session_id") or ""),
                    child_session_id=str(getattr(child_session, "id", "") or entry.get("session_id") or ""),
                    toolsets=list(entry.get("toolsets") or []),
                    provider=str(entry.get("provider") or ""),
                    model=str(entry.get("model") or ""),
                )
                runner = SurfaceRunner(config, cwd=ctx.cwd, include_mcp=True)
                result = runner.run_prompt(
                    tasks[0],
                    session=child_session,
                    agent=child,
                    surface="subagent",
                    meta={
                        "subagent_id": args["continue_id"],
                        "agent_type": role_type,
                        "continuation": True,
                    },
                    on_event=lambda event: _relay_subagent_stream_event(ctx, identity, event),
                )
                _register(
                    args["continue_id"],
                    status="done",
                    run_id=result.run_id,
                    session_id=result.session.id,
                    trace_id=result.trace_id,
                    turn_id=result.turn_id,
                )
                out = result.text or "(no output)"
                _notify_delegation(parent, tasks[0], out)
                return ToolResult.ok(out, display=f"continued {args['continue_id']}")
            except Exception as e:  # noqa: BLE001
                _notify_delegation(parent, tasks[0], f"[subagent error] {e}")
                return ToolResult.error(f"subagent continuation failed: {e}")

        def _restricted_registry(allowed: set[str], *, allow_delegation: bool = False):
            from .registry import ToolRegistry, default_registry
            reg = ToolRegistry()
            for t in default_registry().all():
                if t.name in allowed and (allow_delegation or t.name not in _DELEGATION_TOOLS):
                    reg.register(_ReadOnlySkillTool() if t.name == "skill" else t)
            return reg

        def _child_registry(spec: dict, *, allow_delegation: bool):
            from .registry import ToolRegistry, default_registry
            if spec["tools"] is not None:
                return _restricted_registry(spec["tools"], allow_delegation=allow_delegation)
            blocked = set(_CHILD_BLOCKED_TOOLS if allow_delegation else _LEAF_BLOCKED_TOOLS)
            reg = ToolRegistry()
            for tool in default_registry().all():
                if tool.name not in blocked:
                    reg.register(tool)
            return reg

        if background and depth == 1:
            return self._spawn_background(
                tasks, ctx, config,
                agent_type=atype,
                spec=spec,
                role=role,
                toolsets=toolsets,
                child_registry=_child_registry,
                depth=depth,
                max_depth=max_depth,
            )

        def _one(task: str) -> tuple[str, str]:
            sid = new_id("sub")
            role_prompt = _role_prompt(atype, spec)
            terminal_backend = _subagent_terminal_backend(config)
            effective_role = "orchestrator" if role == "orchestrator" and depth < max_depth else "leaf"
            _register_terminal_backend_override(sid, terminal_backend)
            _register(sid, status="running", task=task[:80], type=atype,
                      role=effective_role, depth=depth, role_prompt=role_prompt,
                      terminal_backend=terminal_backend)
            child = None
            try:
                allow_delegation = effective_role == "orchestrator"
                kwargs = {}
                registry = _child_registry(spec, allow_delegation=allow_delegation)
                if registry is not None:
                    kwargs["registry"] = registry
                child_config = _child_runtime_config(config, toolsets)
                child_toolsets = list(child_config.get("tools.toolsets", []) or [])
                child_provider, child_model = _model_provider_from_config(child_config)
                parent_session = getattr(parent, "session", None)
                parent_session_id = getattr(parent_session, "id", "") or ""
                child_session = Session.create(title=f"subagent {sid}", parent_id=parent_session_id or None)
                from ..surface import apply_session_runtime, inherit_session_runtime
                inherit_session_runtime(parent_session, child_session)
                child_session.meta["creator_kind"] = "subagent"
                child_session.meta["subagent_id"] = sid
                child_session.meta["agent_type"] = atype
                child_session.meta["role"] = effective_role
                child_session.meta["depth"] = depth
                child_session.meta["max_spawn_depth"] = max_depth
                child_session.meta["toolsets"] = child_toolsets
                child_session.meta["model"] = child_model
                child_session.meta["provider"] = child_provider
                if parent_session_id:
                    child_session.meta["parent_session_id"] = parent_session_id
                    child_session.meta["_delegate_from"] = parent_session_id
                _seed_role_prompt(child_session, atype, role_prompt)
                identity = _event_identity(
                    sid=sid,
                    task=task,
                    agent_type=atype,
                    role=effective_role,
                    depth=depth,
                    parent_session_id=parent_session_id,
                    child_session_id=child_session.id,
                    toolsets=child_toolsets,
                    provider=child_provider,
                    model=child_model,
                )
                ctx.emit_event(type="subagent_start", **identity)
                from ..surface import _agent_create
                child = _agent_create(
                    Agent,
                    child_config,
                    session=child_session,
                    cwd=ctx.cwd,
                    memory=None,
                    approver=_subagent_approver(config, sid),
                    **kwargs,
                )
                _register(
                    sid,
                    agent=child,
                    session_id=child_session.id,
                    child_session_id=child_session.id,
                    parent_session_id=parent_session_id,
                    toolsets=child_toolsets,
                    provider=child_provider,
                    model=child_model,
                )
                if spec["tools"] is None:
                    try:
                        child.load_mcp()
                    except Exception:  # noqa: BLE001
                        pass
                apply_session_runtime(child)
                child._depth = depth  # type: ignore[attr-defined]
                from ..surface import SurfaceRunner

                runner = SurfaceRunner(child_config, cwd=ctx.cwd, include_mcp=True)
                result = runner.run_prompt(
                    task,
                    session=child_session,
                    agent=child,
                    surface="subagent",
                    meta={
                        "subagent_id": sid,
                        "agent_type": atype,
                        "role": effective_role,
                        "depth": depth,
                        "toolsets": child_toolsets,
                        "model": child_model,
                        "provider": child_provider,
                        "parent_session_id": parent_session_id,
                        "creator_kind": "subagent",
                    },
                    on_event=lambda event: _relay_subagent_stream_event(ctx, identity, event),
                )
                out = result.text or "(no output)"
                observability = _subagent_observability(result)
                _register(
                    sid,
                    status="done",
                    agent=child,   # kept for continue_id follow-ups
                    run_id=result.run_id,
                    session_id=result.session.id,
                    child_session_id=result.session.id,
                    trace_id=result.trace_id,
                    turn_id=result.turn_id,
                    **observability,
                )
                done_identity = {
                    **identity,
                    "session_id": result.session.id,
                    "child_session_id": result.session.id,
                }
                ctx.emit_event(
                    type="subagent_done",
                    **done_identity,
                    status="done",
                    run_id=result.run_id,
                    trace_id=result.trace_id,
                    turn_id=result.turn_id,
                    **observability,
                )
                _notify_delegation(parent, task, out)
                return sid, out
            except Exception as e:  # noqa: BLE001 - isolate one child's failure
                _register(sid, status="error")
                if child is not None:
                    try:
                        from ..surface import _close_agent
                        _close_agent(child)
                    except Exception:  # noqa: BLE001
                        pass
                _clear_terminal_backend_override(sid)
                try:
                    identity
                except UnboundLocalError:
                    identity = _event_identity(
                        sid=sid,
                        task=task,
                        agent_type=atype,
                        role=effective_role,
                        depth=depth,
                    )
                ctx.emit_event(type="subagent_done", **identity, status="error")
                out = f"[subagent error] {e}"
                _notify_delegation(parent, task, out)
                return sid, out

        if len(tasks) == 1:
            sid, out = _one(tasks[0])
            return ToolResult.ok(f"{out}\n\n(subagent id: {sid} — pass continue_id to follow up)",
                                 display=f"{atype} subagent finished")
        cap = _subagent_concurrency(config)
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(cap, len(tasks))) as ex:
            results = list(ex.map(_one, tasks))
        body = "\n\n".join(f"## subagent {i + 1} ({sid})\n{r}" for i, (sid, r) in enumerate(results))
        return ToolResult.ok(body, display=f"{len(tasks)} {atype} subagents finished")

    def _spawn_background(self, tasks, ctx, config, *, agent_type: str, spec: dict,
                          role: str, toolsets, child_registry, depth: int,
                          max_depth: int) -> ToolResult:
        """Fire-and-forget delegation: the child runs after this turn ends and its
        result is announced into the originating chat (gateway) or kept for /tasks (CLI)."""
        from ..background import BackgroundCapacityError, get_manager
        platform = getattr(ctx.agent, "platform", None)
        chat_id = getattr(ctx.agent, "chat_id", None)
        effective_role = "orchestrator" if role == "orchestrator" and depth < max_depth else "leaf"
        allow_delegation = effective_role == "orchestrator"
        registry = child_registry(spec, allow_delegation=allow_delegation)
        child_config = _child_runtime_config(config, toolsets)
        child_toolsets = list(child_config.get("tools.toolsets", []) or [])
        child_provider, child_model = _model_provider_from_config(child_config)
        role_prompt = _role_prompt(agent_type, spec)
        include_mcp = spec["tools"] is None
        parent_session = getattr(ctx.agent, "session", None)
        parent_session_id = str(getattr(parent_session, "id", "") or "")
        runtime_meta = {}
        try:
            from ..surface import runtime_controls_meta, session_runtime_controls

            runtime_meta = runtime_controls_meta(session_runtime_controls(parent_session))
        except Exception:  # noqa: BLE001
            runtime_meta = {}
        runtime_controls = runtime_meta.get("runtime_controls") if isinstance(runtime_meta, dict) else {}
        if isinstance(runtime_controls, dict):
            child_provider = str(runtime_controls.get("provider") or child_provider)
            child_model = str(runtime_controls.get("model") or child_model)
        session_meta = {
            **runtime_meta,
            "agent_type": agent_type,
            "role": effective_role,
            "subagent_role_prompt": role_prompt,
            "parent_session_id": parent_session_id,
            "creator_kind": "subagent",
            "depth": depth,
            "max_depth": max_depth,
            "max_spawn_depth": max_depth,
            "toolsets": child_toolsets,
            "provider": child_provider,
            "model": child_model,
        }

        def _announce(task) -> None:
            _notify_delegation(ctx.agent, task.prompt, task.result or task.error)
            text = (f"✅ background task done:\n{task.result}" if task.status == "done"
                    else f"⚠ background task failed: {task.error}")
            from ..agent.wakeups import add_wakeup     # parent agent learns it next turn
            add_wakeup("subagent", f"{task.id}: {task.prompt[:80]}",
                       task.result or task.error,
                       session_key=str(getattr(getattr(ctx.agent, "session", None), "id", "") or ""))
            from ..eventbus import BUS              # else surface on the live dashboard feed
            BUS.publish({"type": "background_done", "platform": platform or "cli",
                         "chat_id": chat_id, "text": text[:2000],
                         "id": task.id, "status": task.status, "run_id": task.run_id,
                         "agent_type": agent_type, "background": True})

        try:
            manager = get_manager()
            delivery = {
                "platform": platform or "",
                "chat_id": chat_id or "",
                "user_id": getattr(ctx.agent, "user_id", "") or "",
                "user_name": getattr(ctx.agent, "user_name", "") or "",
                "thread_id": getattr(ctx.agent, "thread_id", "") or "",
                "message_id": getattr(ctx.agent, "message_id", "") or "",
            }
            spawn_kwargs = {
                "cwd": ctx.cwd,
                "on_done": _announce,
                "parent_session": parent_session,
                "registry": registry,
                "include_mcp": include_mcp,
                "session_meta": session_meta,
                "approver": _subagent_approver(config, "background"),
                "delivery": delivery,
            }
            spawn_many = getattr(manager, "spawn_many", None)
            if callable(spawn_many):
                ids = spawn_many(child_config, tasks, **spawn_kwargs)
            else:
                require_capacity = getattr(manager, "require_capacity", None)
                if callable(require_capacity):
                    require_capacity(child_config, len(tasks))
                ids = [manager.spawn(child_config, t, **spawn_kwargs) for t in tasks]
        except BackgroundCapacityError as e:
            return ToolResult.error(str(e))
        return ToolResult.ok(
            f"started {len(ids)} background task(s): {', '.join(ids)}. I'll report the "
            "result(s) here when they finish — continuing with your other work meanwhile.",
            display=f"{len(ids)} background task(s) started")


class ImageGenTool(Tool):
    name = "generate_image"
    description = "Generate an image from a text prompt (OpenAI-compatible /images/generations). Saves a PNG and returns its path."
    groups = ["network"]
    extra_toolsets = ["image_gen"]
    parameters = {
        "type": "object",
        "properties": {
            "prompt": {"type": "string"},
            "size": {"type": "string", "description": "e.g. 1024x1024"},
            "model": {"type": "string", "description": "image model (default gpt-image-1)."},
        },
        "required": ["prompt"],
    }

    def run(self, args, ctx: ToolContext) -> ToolResult:
        from ..providers import build_provider

        try:
            provider = build_provider(ctx.config)
            headers = {"Content-Type": "application/json", **provider.auth.headers()}
            payload = {
                "model": args.get("model", "gpt-image-1"),
                "prompt": args["prompt"],
                "size": args.get("size", "1024x1024"),
                "n": 1,
            }
            with httpx.Client(timeout=180) as c:
                r = c.post(f"{provider.base_url}/images/generations", headers=headers, json=payload)
                r.raise_for_status()
                data = r.json()["data"][0]
        except Exception as e:  # noqa: BLE001
            return ToolResult.error(f"image generation failed (provider must support images): {e}")

        out_dir = ctx.cwd / "aegis_images"
        out_dir.mkdir(exist_ok=True)
        path = out_dir / f"{slugify(args['prompt'], 30)}-{int(time.time())}.png"
        if data.get("b64_json"):
            path.write_bytes(base64.b64decode(data["b64_json"]))
        elif data.get("url"):
            with httpx.Client(timeout=120) as c:
                path.write_bytes(c.get(data["url"]).content)
        else:
            return ToolResult.error("no image data returned.")
        return ToolResult.ok(f"saved image to {path}", display=f"image -> {path.name}")


class MixtureTool(Tool):
    name = "mixture_of_agents"
    description = (
        "Fan ONE prompt across SEVERAL models in parallel and synthesize their answers into "
        "one. Use for high-stakes questions where cross-model agreement matters (design "
        "decisions, tricky bugs, fact checks). models: list like ['gpt-5.5', "
        "'openrouter/google/gemini-2.5-pro'] — a bare model id uses the current provider; "
        "'provider/model' picks the provider. Costs one call per model plus a synthesis call."
    )
    groups = ["automation"]
    parameters = {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "The question to put to every model."},
            "models": {"type": "array", "items": {"type": "string"},
                       "description": "2–5 model specs ('model' or 'provider/model')."},
            "synthesize": {"type": "boolean",
                           "description": "Merge answers into one (default true)."},
        },
        "required": ["prompt", "models"],
    }

    def run(self, args, ctx: ToolContext) -> ToolResult:
        from concurrent.futures import ThreadPoolExecutor
        from ..providers.fallback import build_with_fallbacks
        from ..types import Message
        config = ctx.config
        if config is None:
            return ToolResult.error("no config available")
        specs = [m for m in (args.get("models") or []) if isinstance(m, str) and m.strip()][:5]
        if len(specs) < 2:
            return ToolResult.error("provide at least 2 model specs")
        prompt = args["prompt"]

        def _ask(spec: str) -> tuple[str, str]:
            try:
                prov_name, model = None, spec
                if "/" in spec:
                    head, rest = spec.split("/", 1)
                    from ..providers.registry import list_providers
                    if head in list_providers() and head != "openrouter":
                        prov_name, model = head, rest
                    elif head == "openrouter":
                        prov_name, model = head, rest
                p = build_with_fallbacks(config, model=model, name=prov_name)
                resp = p.complete([Message.user(prompt)], tools=None, stream=False)
                return spec, (resp.text or "").strip() or "(empty)"
            except Exception as e:  # noqa: BLE001 - one model failing must not sink the mix
                return spec, f"[error] {e}"

        with ThreadPoolExecutor(max_workers=len(specs)) as ex:
            answers = list(ex.map(_ask, specs))
        body = "\n\n".join(f"## {spec}\n{ans}" for spec, ans in answers)
        if args.get("synthesize", True):
            try:
                p = build_with_fallbacks(config)
                syn = p.complete(
                    [Message.system("You are synthesizing several models' answers to the same "
                                    "question. Produce ONE best answer; note real disagreements."),
                     Message.user(f"QUESTION:\n{prompt}\n\nANSWERS:\n{body}")],
                    tools=None, stream=False).text or ""
                body = f"# Synthesis\n{syn.strip()}\n\n# Individual answers\n{body}"
            except Exception as e:  # noqa: BLE001
                body = f"(synthesis failed: {e})\n\n{body}"
        return ToolResult.ok(body, display=f"mixture of {len(specs)} models")


def agentic_tools() -> list[Tool]:
    return [SubagentTool(), ImageGenTool(), MixtureTool()]
