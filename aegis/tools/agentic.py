"""Higher-order tools: subagent spawning and image generation."""

from __future__ import annotations

import base64
import copy
import threading
import time

import httpx

from ..types import new_id
from ..util import slugify
from .base import Tool, ToolContext, ToolResult

_NO_TOOLSETS = ["__none__"]

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


def _notify_delegation(parent, task: str, result: str) -> None:
    parent_mem = getattr(parent, "memory", None)
    if parent_mem is None:
        return
    try:
        parent_mem.on_delegation(task, result)
    except Exception:  # noqa: BLE001
        pass


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


def _child_config_for_toolsets(config, requested_toolsets):
    if not requested_toolsets:
        return config
    try:
        from ..config import Config
    except Exception:  # noqa: BLE001
        return config

    requested = [
        str(item).strip()
        for item in requested_toolsets
        if isinstance(item, str) and str(item).strip()
    ]
    if not requested:
        requested = _NO_TOOLSETS
    parent_toolsets = [
        str(item).strip()
        for item in (config.get("tools.toolsets", []) or ["core"])
        if isinstance(item, str) and str(item).strip()
    ]
    parent_enabled = set(parent_toolsets)
    child_toolsets = requested if "all" in parent_enabled else [
        item for item in requested if item in parent_enabled
    ]
    data = copy.deepcopy(getattr(config, "data", {}) or {})
    data.setdefault("tools", {})["toolsets"] = child_toolsets or list(_NO_TOOLSETS)
    return Config(data)


def _max_spawn_depth(config) -> int:
    try:
        return max(1, int(config.get("agent.max_spawn_depth", 1) or 1))
    except Exception:  # noqa: BLE001
        return 1


# Typed subagents: a named type = a tool whitelist + a role preamble. Read-only types
# can fan out aggressively because they cannot modify anything.
_READONLY_TOOLS = {
    "read_file", "list_dir", "glob", "search", "web_fetch", "web_search",
    "session_search", "tool_search", "skill", "system_status", "lsp",
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
        "general (default) has full tools. Pass continue_id to follow up with a previous "
        "subagent (it keeps its context). Returns each child's final answer."
    )
    groups = ["automation"]
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
                                          "its result is announced back when done (single task only)."},
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
                if child_session is not None and _seed_role_prompt(
                    child_session, role_type, _role_prompt(role_type, role_spec)
                ):
                    try:
                        child.refresh_volatile()
                    except Exception:  # noqa: BLE001
                        pass
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
                if t.name in allowed and (allow_delegation or t.name != "spawn_subagent"):
                    reg.register(_ReadOnlySkillTool() if t.name == "skill" else t)
            return reg

        def _child_registry(spec: dict, *, allow_delegation: bool):
            from .registry import ToolRegistry, default_registry
            if spec["tools"] is not None:
                return _restricted_registry(spec["tools"], allow_delegation=allow_delegation)
            if allow_delegation:
                return None
            reg = ToolRegistry()
            for tool in default_registry().all():
                if tool.name != "spawn_subagent":
                    reg.register(tool)
            return reg

        if args.get("background") and depth == 1:
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
            _register_terminal_backend_override(sid, terminal_backend)
            _register(sid, status="running", task=task[:80], type=atype,
                      role_prompt=role_prompt, terminal_backend=terminal_backend)
            ctx.emit_event(type="subagent_start", id=sid, task=task[:80])
            child = None
            try:
                allow_delegation = role == "orchestrator" and depth < max_depth
                kwargs = {}
                registry = _child_registry(spec, allow_delegation=allow_delegation)
                if registry is not None:
                    kwargs["registry"] = registry
                child_config = _child_config_for_toolsets(config, toolsets)
                child_session = Session.create()
                from ..surface import apply_session_runtime, inherit_session_runtime
                inherit_session_runtime(getattr(parent, "session", None), child_session)
                _seed_role_prompt(child_session, atype, role_prompt)
                from ..surface import _agent_create
                child = _agent_create(
                    Agent,
                    child_config,
                    session=child_session,
                    cwd=ctx.cwd,
                    **kwargs,
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
                        "parent_session_id": getattr(getattr(parent, "session", None), "id", ""),
                    },
                )
                out = result.text or "(no output)"
                _register(
                    sid,
                    status="done",
                    agent=child,   # kept for continue_id follow-ups
                    run_id=result.run_id,
                    session_id=result.session.id,
                    trace_id=result.trace_id,
                    turn_id=result.turn_id,
                )
                ctx.emit_event(type="subagent_done", id=sid, status="done")
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
                ctx.emit_event(type="subagent_done", id=sid, status="error")
                out = f"[subagent error] {e}"
                _notify_delegation(parent, task, out)
                return sid, out

        if len(tasks) == 1:
            sid, out = _one(tasks[0])
            return ToolResult.ok(f"{out}\n\n(subagent id: {sid} — pass continue_id to follow up)",
                                 display=f"{atype} subagent finished")
        cap = max(1, int(config.get("agent.subagent_concurrency", 4) or 1))
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
        from ..background import get_manager
        platform = getattr(ctx.agent, "platform", None)
        chat_id = getattr(ctx.agent, "chat_id", None)
        allow_delegation = role == "orchestrator" and depth < max_depth
        registry = child_registry(spec, allow_delegation=allow_delegation)
        child_config = _child_config_for_toolsets(config, toolsets)
        role_prompt = _role_prompt(agent_type, spec)
        session_meta = {
            "agent_type": agent_type,
            "subagent_role_prompt": role_prompt,
        } if role_prompt or agent_type else {}
        include_mcp = spec["tools"] is None

        def _announce(task) -> None:
            _notify_delegation(ctx.agent, task.prompt, task.result or task.error)
            text = (f"✅ background task done:\n{task.result}" if task.status == "done"
                    else f"⚠ background task failed: {task.error}")
            from ..agent.wakeups import add_wakeup     # parent agent learns it next turn
            add_wakeup("subagent", f"{task.id}: {task.prompt[:80]}",
                       task.result or task.error,
                       session_key=str(getattr(getattr(ctx.agent, "session", None), "id", "") or ""))
            if platform and chat_id:                 # announce back into the chat via the outbox
                try:
                    from ..gateway.queue import DeliveryQueue
                    DeliveryQueue().enqueue(platform, chat_id, text[:3500])
                    return
                except Exception:  # noqa: BLE001
                    pass
            from ..eventbus import BUS              # else surface on the live dashboard feed
            BUS.publish({"type": "background_done", "platform": platform or "cli",
                         "chat_id": chat_id, "text": text[:2000]})

        parent_session = getattr(ctx.agent, "session", None)
        ids = [
            get_manager().spawn(
                child_config, t, cwd=ctx.cwd, on_done=_announce,
                parent_session=parent_session, registry=registry,
                include_mcp=include_mcp, session_meta=session_meta,
            )
            for t in tasks
        ]
        return ToolResult.ok(
            f"started {len(ids)} background task(s): {', '.join(ids)}. I'll report the "
            "result(s) here when they finish — continuing with your other work meanwhile.",
            display=f"{len(ids)} background task(s) started")


class ImageGenTool(Tool):
    name = "generate_image"
    description = "Generate an image from a text prompt (OpenAI-compatible /images/generations). Saves a PNG and returns its path."
    groups = ["network"]
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
