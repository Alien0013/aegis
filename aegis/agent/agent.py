"""The Agent: wires provider + tools + memory + skills + session into the loop."""

from __future__ import annotations

import fnmatch
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .. import config as cfg_paths, model_meta
from ..config import Config, Workspace, context_file_max_chars, drain_context_file_warnings
from ..constants import DEFAULT_MAX_ITERATIONS
from ..memory import MemoryManager
from ..providers import Provider
from ..session import Session, SessionStore
from ..skills import SkillsLoader
from ..tools.base import ToolContext
from ..tools.devtools import is_bridge_or_direct_tool_name
from ..tools.permissions import PermissionEngine
from ..tools.registry import ToolRegistry, default_registry
from ..types import Message, Usage, new_id
from ..util import estimate_tokens
from .context import ContextBuilder, PromptBuild, PromptPart
from .loop import OnEvent, run_conversation


@dataclass
class IterationBudget:
    max_iterations: int = DEFAULT_MAX_ITERATIONS
    api_call_count: int = 0
    usage: Usage = field(default_factory=Usage)

    def reset(self) -> None:
        self.api_call_count = 0

    def refund(self) -> None:
        """Give back one iteration — used for cheap local turns (e.g. execute_code) so a
        code-heavy run isn't penalized against the step budget."""
        self.api_call_count = max(0, self.api_call_count - 1)

    @property
    def remaining(self) -> int:
        return self.max_iterations - self.api_call_count

    def should_continue(self) -> bool:
        return self.remaining > 0


def _tool_source(tool) -> str:
    source = str(getattr(tool, "source", "") or getattr(tool, "_aegis_source", "") or "")
    if source:
        return source
    name = str(getattr(tool, "name", "") or "")
    if name.startswith("mcp__"):
        return "mcp"
    if getattr(tool, "_aegis_plugin", None):
        return "plugin"
    return ""


def _matches_deferred_selector(tool, selectors) -> bool:
    name = str(getattr(tool, "name", "") or "")
    toolset = str(getattr(tool, "toolset", "") or "")
    source = _tool_source(tool)
    for raw in selectors:
        selector = str(raw or "").strip()
        if not selector:
            continue
        if selector == name:
            return True
        if selector.startswith("source:") and source == selector.split(":", 1)[1]:
            return True
        if selector.startswith("toolset:") and toolset == selector.split(":", 1)[1]:
            return True
        if selector.startswith("glob:") and fnmatch.fnmatchcase(name, selector.split(":", 1)[1]):
            return True
        if selector.endswith(":*"):
            prefix = selector[:-2]
            if prefix in {source, toolset} or name.startswith(prefix + "__"):
                return True
        if any(ch in selector for ch in "*?[") and fnmatch.fnmatchcase(name, selector):
            return True
    return False


def _bool_config(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def _float_config(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int_config(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _auth_bearer_token(provider: Provider) -> str:
    try:
        headers = provider.auth.headers()
    except Exception:  # noqa: BLE001
        return ""
    for key, value in headers.items():
        if key.lower() == "authorization":
            text = str(value or "").strip()
            if text.lower().startswith("bearer "):
                return text[7:].strip()
    return ""


def _merge_nested_dict(target: dict, additions: dict) -> None:
    for key, value in additions.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge_nested_dict(target[key], value)
        else:
            target[key] = value


def _provider_extra_body(provider: Provider) -> dict:
    overrides = getattr(provider, "request_overrides", None)
    if not isinstance(overrides, dict):
        overrides = {}
        setattr(provider, "request_overrides", overrides)
    extra_body = overrides.get("extra_body")
    if not isinstance(extra_body, dict):
        extra_body = {}
        overrides["extra_body"] = extra_body
    return extra_body


def _apply_ollama_num_ctx(provider: Provider, num_ctx: int) -> None:
    if num_ctx <= 0:
        return
    _merge_nested_dict(_provider_extra_body(provider), {"options": {"num_ctx": int(num_ctx)}})


def _resolve_ollama_num_ctx(config: Config, provider: Provider) -> int | None:
    override = config.get("model.ollama_num_ctx")
    if override is not None and str(override).strip() != "":
        parsed = _int_config(override, 0)
        return parsed if parsed > 0 else None
    base_url = str(getattr(provider, "base_url", "") or "").strip()
    if not base_url or not model_meta.is_local_endpoint(base_url):
        return None
    detected = model_meta.query_ollama_num_ctx(
        str(getattr(provider, "model", "") or ""),
        base_url,
        api_key=_auth_bearer_token(provider),
    )
    if not detected or detected <= 0:
        return None
    context_cap = _int_config(config.get("model.context_length"), 0)
    if context_cap > 0 and detected > context_cap:
        return context_cap
    return int(detected)


def _auto_defer_source(tool) -> bool:
    source = _tool_source(tool)
    toolset = str(getattr(tool, "toolset", "") or "").strip()
    if source == "mcp" or toolset == "mcp" or toolset.startswith("mcp-"):
        return True
    if source and source not in {"builtin", "tool"}:
        return True
    return bool(toolset and toolset != "core")


def _auto_defer_threshold(config: Config, provider: Provider | None = None) -> int:
    min_tokens = max(1, _int_config(config.get("tools.defer_min_tokens"), 2000))
    ratio = max(0.0, _float_config(config.get("tools.defer_threshold_ratio"), 0.10))
    context_length = 0
    if provider is not None:
        context_length = _int_config(getattr(provider, "context_length", 0), 0)
    if context_length <= 0:
        context_length = _int_config(config.get("model.context_length"), 0)
    if context_length > 0 and ratio > 0:
        return max(min_tokens, int(context_length * ratio))
    return min_tokens


def _schema_tokens(tool) -> int:
    try:
        payload = tool.schema()
    except Exception:  # noqa: BLE001
        payload = {
            "name": str(getattr(tool, "name", "") or ""),
            "description": str(getattr(tool, "description", "") or ""),
            "parameters": getattr(tool, "parameters", {}) or {},
        }
    try:
        body = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    except TypeError:
        body = str(payload)
    return estimate_tokens(body)


def _auto_deferred_tool_names(tools, *, config: Config, provider: Provider | None = None) -> set[str]:
    if not _bool_config(config.get("tools.defer_auto"), True):
        return set()
    candidates = [
        tool for tool in tools
        if _auto_defer_source(tool)
        and not is_bridge_or_direct_tool_name(str(getattr(tool, "name", "") or ""))
    ]
    if not candidates:
        return set()
    token_count = sum(_schema_tokens(tool) for tool in candidates)
    if token_count < _auto_defer_threshold(config, provider):
        return set()
    return {str(getattr(tool, "name", "") or "") for tool in candidates}


_TOOL_SCHEMA_CACHE_MAX = 8


def _freeze_schema_cache_value(value):
    if isinstance(value, dict):
        return tuple(sorted((str(key), _freeze_schema_cache_value(item)) for key, item in value.items()))
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(_freeze_schema_cache_value(item) for item in value)
    try:
        hash(value)
        return value
    except TypeError:
        return repr(value)


def _tool_schema_config_fingerprint(config: Config) -> tuple:
    keys = (
        "tools.toolsets",
        "tools.disabled",
        "tools.disabled_toolsets",
        "tools.defer_schemas",
        "tools.deferred",
        "tools.defer_auto",
        "tools.defer_threshold_ratio",
        "tools.defer_min_tokens",
        "model.context_length",
    )
    return tuple((key, _freeze_schema_cache_value(config.get(key))) for key in keys)


class Agent:
    def __init__(
        self,
        *,
        config: Config,
        provider: Provider,
        session: Session,
        registry: ToolRegistry | None = None,
        memory: MemoryManager | None = None,
        skills: SkillsLoader | None = None,
        cwd: Path | None = None,
        approver: Callable[[str], bool] | None = None,
        secret_capture: Callable[[str, str, dict | None], dict] | None = None,
        store: SessionStore | None = None,
        event_callback: Callable[[str, dict], None] | None = None,
    ):
        self.config = config
        self.provider = provider
        self.session = session
        self.cwd = cwd or Path.cwd()
        self.registry = registry or default_registry()
        self._context_engine = None
        self._context_engine_tool_names: set[str] = set()
        try:                                   # a custom context engine may expose its own tools
            from .context_engine import get_engine
            self._context_engine = get_engine(config)
            for t in self._context_engine.tools():
                tool_name = str(getattr(t, "name", "") or "").strip()
                if not tool_name or self.registry.get(tool_name) is not None:
                    continue
                self.registry.register(t)
                if self.registry.get(tool_name) is t:
                    self._context_engine_tool_names.add(tool_name)
        except Exception:  # noqa: BLE001
            pass
        self.permissions = PermissionEngine(config)
        self.memory = memory if memory is not None else (
            MemoryManager(config) if config.get("memory.enabled", True) else None
        )
        self.skills = skills or SkillsLoader(config, self.cwd, session_id=getattr(session, "id", ""))
        if skills is not None:
            self.skills.session_id = getattr(session, "id", "")
        self.workspace = Workspace(
            self.cwd,
            context_file_max_chars=context_file_max_chars(config),
        )
        self.context_builder = ContextBuilder(config, self.workspace, self.cwd)
        self.store = store
        self.event_callback = event_callback
        self.stream = bool(config.get("agent.stream", True))
        self.reasoning = config.get("agent.reasoning_effort", "medium")
        self._tool_use_enforcement = config.get("agent.tool_use_enforcement", "auto")
        self._task_completion_guidance = _bool_config(config.get("agent.task_completion_guidance"), True)
        self._parallel_tool_call_guidance = _bool_config(config.get("agent.parallel_tool_call_guidance"), True)
        self._environment_probe = _bool_config(config.get("agent.environment_probe"), True)
        self._api_max_retries = max(1, _int_config(config.get("agent.api_max_retries"), 3))
        platform_hints = config.get("platform_hints", {})
        self._platform_hint_overrides = platform_hints if isinstance(platform_hints, dict) else {}
        self._ollama_num_ctx = _resolve_ollama_num_ctx(config, provider)
        if self._ollama_num_ctx:
            _apply_ollama_num_ctx(provider, self._ollama_num_ctx)
        raw_tier = str(config.get("agent.service_tier", "") or "").strip().lower()
        self.service_tier = "priority" if raw_tier in {"fast", "priority", "on", "true", "yes"} else ""
        self.budget = IterationBudget(int(config.get("agent.max_iterations", DEFAULT_MAX_ITERATIONS)))
        self.tools_used = 0
        self.activated_tools: set[str] = set()   # deferred tools loaded via tool_search this session
        self._tool_schema_cache: dict[tuple, list[dict]] = {}
        self.platform: str | None = None   # set by the gateway to the active channel (telegram, …)
        self.chat_id: str | None = None     # set by the gateway to the active conversation id
        import queue
        import threading
        self.cancel_event = threading.Event()   # set by .cancel() to interrupt a run
        self.steer_queue: queue.Queue = queue.Queue()   # mid-run guidance injected via .steer()
        self._current_task_id = getattr(self.session, "id", "")
        self._current_turn_id = ""
        self._current_api_request_id = ""
        self._last_api_request_id = ""
        self._turn_api_request_count = 0
        self._turn_started_user_index: int | None = None
        self._active_response_id = ""
        self._active_response_cancelled = ""
        self._turn_prologue_prepared = False
        self._wire_user_content_target = ""
        self._wire_user_content_override = ""
        self._runtime_selection_active = False
        self._last_activity_ts = time.time()
        self._last_activity_desc = "agent initialized"
        self._current_tool = ""
        self._mcp = None
        self._run_thread_id = None

        if self.memory is not None:
            self.memory.initialize(getattr(self.session, "id", ""))   # provider warm-up
            for t in self.memory.provider_tools():                    # provider-specific tools
                try:
                    self.registry.register(t)
                except Exception:  # noqa: BLE001
                    pass

        self._terminal_task_id = getattr(self.session, "id", "")
        self.tool_context = ToolContext(
            cwd=self.cwd, config=config, memory=self.memory, skills=self.skills,
            session=self.session, agent=self, approver=approver,
            secret_capture=secret_capture,
            task_id=self._terminal_task_id,
        )
        if self._context_engine is not None:
            try:
                from .context_engine import call_hook
                call_hook(
                    self._context_engine,
                    "on_session_start",
                    self,
                    session_id=getattr(self.session, "id", ""),
                    aegis_home=str(cfg_paths.get_home()),
                    platform=self.platform or "cli",
                    model=getattr(self.provider, "model", ""),
                    context_length=int(getattr(self.provider, "context_length", 0) or 0),
                    conversation_id=self.chat_id,
                )
            except Exception:  # noqa: BLE001
                pass

    # -- convenience constructor -------------------------------------------
    @classmethod
    def create(
        cls,
        config: Config,
        *,
        session: Session | None = None,
        model: str | None = None,
        provider_name: str | None = None,
        cwd: Path | None = None,
        approver: Callable[[str], bool] | None = None,
        secret_capture: Callable[[str, str, dict | None], dict] | None = None,
        store: SessionStore | None = None,
        include_mcp: bool = False,
        registry: ToolRegistry | None = None,
        memory: MemoryManager | None = None,
        event_callback: Callable[[str, dict], None] | None = None,
    ) -> "Agent":
        from ..providers.fallback import build_with_fallbacks
        provider = build_with_fallbacks(config, model=model, name=provider_name)
        session = session or Session.create()
        agent = cls(config=config, provider=provider, session=session, cwd=cwd,
                    approver=approver, secret_capture=secret_capture, store=store,
                    registry=registry, memory=memory, event_callback=event_callback)
        if include_mcp:
            agent.load_mcp()
        return agent

    def load_mcp(self) -> None:
        """Connect configured MCP servers and register their tools."""
        try:
            from ..mcp import mcp_tools_from_config
            tools, mgr = mcp_tools_from_config(self.config)
            for t in tools:
                self.registry.register(t)
            self._mcp = mgr
            if tools:
                print(f"  ▸ MCP: {len(tools)} tool(s) from {len(mgr.clients)} server(s)")
        except Exception as e:  # noqa: BLE001
            print(f"  ! MCP load failed: {e}")

    def refresh_mcp_tools(self, on_event: OnEvent | None = None) -> list:
        """Refresh MCP tools for servers that announced a changed catalog."""
        manager = getattr(self, "_mcp", None)
        refresh = getattr(manager, "refresh_changed_tools", None)
        if not callable(refresh):
            return []
        before = {tool.name for tool in self.registry.all()}
        try:
            refreshed = list(refresh(self.registry) or [])
        except Exception as exc:  # noqa: BLE001
            record = {
                "status": "error",
                "error": str(exc),
                "server_count": len(getattr(manager, "clients", []) or []),
            }
            self.session.meta["last_mcp_refresh"] = record
            if on_event is not None:
                on_event({"type": "mcp_tools_refresh_failed", **record})
            return []
        if not refreshed:
            return []
        after = {tool.name for tool in self.registry.all()}
        refreshed_names = sorted({str(getattr(tool, "name", "") or "") for tool in refreshed})
        record = {
            "status": "ok",
            "added": sorted(after - before),
            "removed": sorted(before - after),
            "updated": sorted(name for name in refreshed_names if name in before and name in after),
            "refreshed": refreshed_names,
            "server_count": len(getattr(manager, "clients", []) or []),
        }
        self.session.meta["last_mcp_refresh"] = record
        self.session.meta["_rebuild_system_prompt"] = True
        if on_event is not None:
            on_event({"type": "mcp_tools_refreshed", **record})
        return refreshed

    # -- system prompt ------------------------------------------------------
    def _build_runtime_block(self) -> str:
        api_mode = getattr(self.provider, "api_mode", "")
        api_mode_value = getattr(api_mode, "value", str(api_mode) if api_mode else "unknown")
        auth = getattr(self.provider, "auth", None)
        if auth is None:
            auth_desc = "unknown"
            auth_state = "unknown"
        else:
            try:
                auth_desc = auth.describe()
            except Exception:  # noqa: BLE001
                auth_desc = "unknown"
            try:
                auth_state = "ready" if auth.available() else "missing"
            except Exception:  # noqa: BLE001
                auth_state = "unknown"

        toolsets = list(self.config.get("tools.toolsets", ["core"]) or ["core"])
        enabled_tools = self.registry.available(toolsets, disabled=self.config.get("tools.disabled", []))
        deferred_tools = self.deferred_tool_names(enabled_tools)
        live_schema_count = len([tool for tool in enabled_tools if tool.name not in deferred_tools])
        active_profile = cfg_paths.current_profile()
        profile_label = active_profile or "default"
        profile_home = cfg_paths.profile_home(active_profile)
        if active_profile:
            profile_guidance = (
                f"- active profile: {profile_label} ({profile_home})\n"
                "- This session reads and writes the active profile home above. Do not modify "
                "another profile's skills, plugins, cron jobs, memories, or config unless the "
                "user explicitly directs you to."
            )
        else:
            profile_guidance = (
                f"- active profile: default ({profile_home})\n"
                "- Named profiles, if present, live under the profiles/ directory. Do not modify "
                "another profile's skills, plugins, cron jobs, memories, or config unless the "
                "user explicitly directs you to."
            )
        recall_guidance = ""
        if any(tool.name == "session_search" for tool in enabled_tools):
            recall_guidance = (
                "\n- If the user asks about prior chats, last session, what you remember from "
                "before, or you suspect cross-session context matters, call `session_search` "
                "before answering or asking them to repeat it."
            )
        todo_block = ""
        try:
            from ..tools.builtin import active_todo_injection

            todo_block = active_todo_injection(getattr(self.session, "todos", []) or [])
        except Exception:  # noqa: BLE001 - prompt rebuild must never fail on todo state
            todo_block = ""
        runtime = (
            "# AEGIS runtime\n"
            f"- provider: {getattr(self.provider, 'name', 'unknown')}\n"
            f"- model: {getattr(self.provider, 'model', 'unknown')}\n"
            f"- transport: {api_mode_value}\n"
            f"- auth: {auth_desc} ({auth_state})\n"
            f"- cwd: {self.cwd}\n"
            f"{profile_guidance}\n"
            f"- toolsets: {', '.join(toolsets)}\n"
            f"- model-visible tools: {len(enabled_tools)}/{len(self.registry.all())} "
            f"({live_schema_count} live schemas, {len(deferred_tools)} deferred)\n"
            "- For questions about whether you are using OAuth, API-key auth, or local auth, "
            "use the auth line above as ground truth.\n"
            "- For install, auth, tools, workspace, dashboard, daemon, or system-health checks, "
            "call the `system_status` tool first, then inspect with focused tools if needed."
            f"{recall_guidance}"
        )
        if todo_block:
            runtime = f"{runtime}\n\n{todo_block}"
        return runtime

    def _coding_workspace_block(self) -> str:
        """Coding posture block, captured once per session and cached so the prompt prefix
        stays cache-stable (the working tree changes every edit). Reset on session switch."""
        cached = getattr(self, "_coding_block", None)
        if cached is None:
            try:
                from .coding_context import coding_workspace_block
                cached = coding_workspace_block(self.cwd, self.config)
            except Exception:  # noqa: BLE001 — workspace probing must never break prompt build
                cached = ""
            self._coding_block = cached
        return cached

    def _skill_compact_categories(self) -> frozenset[str]:
        try:
            from .coding_context import coding_compact_skill_categories

            return coding_compact_skill_categories(self.cwd, self.config)
        except Exception:  # noqa: BLE001
            return frozenset()

    def _available_tools_for_defer(self) -> list:
        return self.registry.available(
            self.config.get("tools.toolsets", ["core"]),
            disabled=self.config.get("tools.disabled", []),
        )

    def clear_tool_schema_cache(self) -> None:
        self._tool_schema_cache.clear()

    def provider_tool_schemas(self, available=None) -> list[dict]:
        """Return provider-visible tool schemas with Hermes-style cache invalidation.

        The cache key tracks registry generation, effective tool names, deferral
        config, model context, and session-sticky deferred activations. It avoids
        rebuilding identical schema lists on every loop iteration while still
        invalidating on MCP/plugin registration and tool_search activation.
        """
        tools = list(available) if available is not None else self._available_tools_for_defer()
        key = (
            int(getattr(self.registry, "_generation", 0) or 0),
            tuple(tool.name for tool in tools),
            _tool_schema_config_fingerprint(self.config),
            int(getattr(self.provider, "context_length", 0) or 0),
            tuple(sorted(getattr(self, "activated_tools", set()) or set())),
        )
        cached = self._tool_schema_cache.get(key)
        if cached is not None:
            return list(cached)
        deferred = self.deferred_tool_names(tools)
        schemas = self.registry.schemas([tool for tool in tools if tool.name not in deferred])
        if len(self._tool_schema_cache) >= _TOOL_SCHEMA_CACHE_MAX:
            self._tool_schema_cache.pop(next(iter(self._tool_schema_cache)))
        self._tool_schema_cache[key] = list(schemas)
        return list(schemas)

    def deferred_tool_candidate_names(self, available=None) -> set[str]:
        """Scoped universe of tools eligible for deferred access this session.

        Unlike ``deferred_tool_names()``, this includes tools already activated by
        ``tool_search``/``tool_describe`` so ``tool_call`` can keep validating
        against the original session-scoped candidate set.
        """
        if not self.config.get("tools.defer_schemas", True):
            return set()
        selectors = self.config.get("tools.deferred", []) or []
        tools = list(available) if available is not None else self._available_tools_for_defer()
        explicit = {
            t.name for t in tools
            if _matches_deferred_selector(t, selectors)
            and not is_bridge_or_direct_tool_name(t.name)
        }
        automatic = _auto_deferred_tool_names(
            tools,
            config=self.config,
            provider=self.provider,
        )
        return explicit | automatic

    def deferred_tool_names(self, available=None) -> set[str]:
        """Tools shipped name-only this turn (schema withheld until tool_search loads it).
        Config-driven (tools.deferred); activation via tool_search is session-sticky."""
        return Agent.deferred_tool_candidate_names(self, available) - getattr(self, "activated_tools", set())

    def _deferred_index_block(self) -> str:
        """Stable system-prompt index of deferred tools. Lists ALL configured deferred
        tools (not just inactive ones) so the block never changes mid-session —
        keeping the prompt byte-stable for prefix caching."""
        if not self.config.get("tools.defer_schemas", True):
            return ""
        available = self.registry.available(
            self.config.get("tools.toolsets", ["core"]),
            disabled=self.config.get("tools.disabled", []),
        )
        candidates = Agent.deferred_tool_candidate_names(self, available)
        tools = [t for t in available if t.name in candidates]
        if not tools:
            return ""
        lines = "\n".join(f"- {t.name} — {t.description.splitlines()[0]}"
                          for t in sorted(tools, key=lambda t: t.name))
        return ("# Deferred tools (schemas not loaded)\n"
                "These tools exist but their parameter schemas are not loaded yet. To use one, "
                "call `tool_search` or `tool_describe` for its schema, then use `tool_call` "
                "or call the tool normally after it becomes live:\n" + lines)

    def _build_system_prompt(self, *, include_volatile: bool = True) -> str:
        skills_index = (
            self.skills.index_block(compact_categories=self._skill_compact_categories())
            if self.skills else ""
        )
        memory_block = self.memory.build_context_block() if self.memory and include_volatile else ""
        runtime = self._build_runtime_block()
        deferred = self._deferred_index_block()
        if deferred:
            runtime = f"{runtime}\n\n{deferred}"
        try:
            prompt_tools_available = bool(self._available_tools_for_defer())
        except Exception:  # noqa: BLE001
            prompt_tools_available = True
        built = self.context_builder.build_with_metadata(
            skills_index=skills_index,
            memory_block=memory_block,
            runtime_block=runtime,
            coding_block=self._coding_workspace_block(),
            platform=getattr(self, "platform", None),
            model=str(getattr(self.provider, "model", "") or self.config.get("model.default", "")),
            tools_available=prompt_tools_available,
            include_volatile=include_volatile,
        )
        role_part = self._subagent_role_prompt_part()
        if role_part:
            parts = list(built.parts)
            insert_at = next(
                (i + 1 for i, part in enumerate(parts) if part.name == "agentic_guidance"),
                1,
            )
            parts.insert(insert_at, role_part)
            text = "\n\n---\n\n".join(part.text.strip() for part in parts if part.text.strip())
            built = PromptBuild(text=text, parts=parts)
        metadata = built.metadata()
        warnings = drain_context_file_warnings()
        if warnings:
            metadata["context_file_warnings"] = warnings
        self._last_prompt_metadata = metadata
        return built.text

    def _build_volatile_system_context(self) -> str:
        try:
            memory_block = self.memory.build_context_block() if self.memory else ""
            return self.context_builder.build_volatile_context(memory_block=memory_block).text
        except Exception:  # noqa: BLE001
            return ""

    def _subagent_role_prompt_part(self) -> PromptPart | None:
        role_prompt = str(self.session.meta.get("subagent_role_prompt") or "").strip()
        if not role_prompt:
            return None
        agent_type = str(self.session.meta.get("agent_type") or "subagent").strip() or "subagent"
        return PromptPart(
            "stable",
            f"subagent_role:{agent_type}",
            f"# Subagent role ({agent_type})\n{role_prompt}",
            "subagent runtime",
            f"session:{self.session.id}",
        )

    def ensure_system_prompt(self, force: bool = False) -> None:
        msgs = self.session.messages
        if msgs and msgs[0].role == "system":
            if not force:
                self._record_prompt_metadata(msgs[0].content, current_build=False)
                return
            prompt = self._build_system_prompt(include_volatile=False)
            msgs[0] = Message.system(prompt)
            self._record_prompt_metadata(prompt)
        else:
            prompt = self._build_system_prompt(include_volatile=False)
            msgs.insert(0, Message.system(prompt))
            self._record_prompt_metadata(prompt)
        self.session.meta["system_prompt_volatile_mode"] = "provider_wire"

    def _record_prompt_metadata(self, prompt_text: str | None = None, *, current_build: bool = True) -> None:
        import hashlib
        from ..util import estimate_tokens

        prompt_text = prompt_text if prompt_text is not None else (
            self.session.messages[0].content
            if self.session.messages and self.session.messages[0].role == "system" else ""
        )
        actual = {
            "hash": hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()[:16],
            "chars": len(prompt_text),
            "tokens": estimate_tokens(prompt_text),
        }
        previous_hash = self.session.meta.get("system_prompt_hash")
        meta = getattr(self, "_last_prompt_metadata", None)
        parts = []
        warnings = list(self.session.meta.get("context_file_warnings") or [])
        if current_build and isinstance(meta, dict) and meta.get("hash") == actual["hash"]:
            parts = meta.get("parts", []) or []
            warnings = list(meta.get("context_file_warnings") or [])
            if warnings:
                self.session.meta["context_file_warnings"] = warnings
            else:
                self.session.meta.pop("context_file_warnings", None)
        elif self.session.meta.get("system_prompt_hash") == actual["hash"]:
            parts = self.session.meta.get("prompt_parts", []) or []
        self.session.meta["system_prompt_hash"] = actual["hash"]
        self.session.meta["system_prompt_chars"] = actual["chars"]
        self.session.meta["system_prompt_tokens"] = actual["tokens"]
        self.session.meta["prompt_parts"] = parts
        snapshot = meta.get("snapshot") if current_build and isinstance(meta, dict) else None
        if isinstance(snapshot, dict):
            self.session.meta["prompt_snapshot"] = snapshot
        elif previous_hash == actual["hash"] and self.session.meta.get("prompt_snapshot"):
            snapshot = self.session.meta.get("prompt_snapshot")
        else:
            self.session.meta.pop("prompt_snapshot", None)
        self.session.meta["prompt_audit"] = self._prompt_audit_metadata(actual, parts, warnings)

    def _prompt_audit_metadata(self, actual: dict, parts: list[dict], warnings: list[str]) -> dict:
        import hashlib

        tiers: dict[str, list[str]] = {}
        all_warnings = list(warnings)
        for part in parts:
            tier = str(part.get("tier") or "other")
            name = str(part.get("name") or "")
            tiers.setdefault(tier, []).append(name)
            for warning in part.get("warnings") or []:
                if warning:
                    all_warnings.append(str(warning))
        stable_hashes = [str(p.get("hash") or "") for p in parts if p.get("cache_stable")]
        stable_key = hashlib.sha256("|".join(stable_hashes).encode("utf-8")).hexdigest()[:16] if stable_hashes else ""
        snapshot = self.session.meta.get("prompt_snapshot") if isinstance(self.session.meta, dict) else {}
        return {
            "hash": actual.get("hash", ""),
            "chars": int(actual.get("chars", 0) or 0),
            "tokens": int(actual.get("tokens", 0) or 0),
            "part_count": len(parts),
            "tiers": tiers,
            "warnings": sorted(set(all_warnings)),
            "cache": {
                "stable_part_count": len(stable_hashes),
                "stable_hash": stable_key,
                "snapshot_fingerprint": str((snapshot or {}).get("fingerprint") or ""),
                "skills_fingerprint": str((snapshot or {}).get("skills_fingerprint") or ""),
                "context_fingerprint": str((snapshot or {}).get("context_fingerprint") or ""),
                "volatile_part_count": sum(1 for p in parts if str(p.get("tier") or "") == "volatile"),
                "context_part_count": sum(1 for p in parts if str(p.get("tier") or "") == "context"),
            },
        }

    def refresh_volatile(self) -> None:
        if self.memory:
            self.memory.refresh_snapshot()
        self.ensure_system_prompt(force=True)

    def switch_session(
        self,
        new_session: Session,
        *,
        reason: str = "",
        reset: bool = False,
        rewound: bool = False,
    ) -> None:
        """Move this agent to ``new_session`` and fire the memory session-switch hook
        (resume, compaction split, /new). Keeps tool_context in sync."""
        old_id = getattr(self.session, "id", "")
        self.session = new_session
        self.tool_context.session = new_session
        new_id = getattr(new_session, "id", "")
        if not getattr(self, "_terminal_task_id", "") or self._terminal_task_id == old_id:
            self._terminal_task_id = new_id
        self.tool_context.task_id = self._terminal_task_id or new_id
        if new_id != old_id:
            try:
                delattr(self, "_subdir_hints")
            except AttributeError:
                pass
            self._coding_block = None       # re-snapshot the workspace for the new session
        if self.memory and new_id != old_id:
            try:
                self.memory.on_session_switch(
                    old_id,
                    new_id,
                    parent_session_id=getattr(new_session, "parent_id", "") or old_id,
                    reset=reset,
                    rewound=rewound,
                    reason=reason,
                )
            except Exception:  # noqa: BLE001
                pass

    def end_session(self) -> None:
        """Fire session-end hooks (process exit, agent teardown, /new)."""
        task_id = getattr(self.tool_context, "task_id", "")
        self._maybe_flush_memory_review()
        if self.memory:
            try:
                self.memory.on_session_end(self.session.messages)
            except Exception:  # noqa: BLE001
                pass
        try:
            from ..hooks import run_hooks
            run_hooks(
                self.config,
                "session_stop",
                {"session_id": self.session.id, "message_count": len(self.session.messages)},
            )
        except Exception:  # noqa: BLE001
            pass
        if task_id:
            try:
                from ..tools.process_registry import process_registry

                process_registry.kill_all(task_id=task_id)
            except Exception:  # noqa: BLE001
                pass
            try:
                from ..tools.backends import cleanup_task_environment, clear_task_env_overrides

                cleanup_task_environment(task_id)
                clear_task_env_overrides(task_id)
            except Exception:  # noqa: BLE001
                pass

    def _maybe_flush_memory_review(self) -> None:
        """Run one final memory review before a long session is left behind.

        Periodic reviews fire every few turns, but a CLI crash, /new, or exit can
        otherwise leave recent durable facts unreviewed. This mirrors the reference
        agent's "memory flush" behavior while staying fail-soft.
        """
        if getattr(self, "_no_review", False):
            return
        if not (self.memory and self.provider):
            return
        if not bool(self.config.get("learn.background", False)):
            return
        if not bool(self.config.get("learn.auto_apply", False)):
            return
        min_turns = int(self.config.get("learn.flush_min_turns", 0) or 0)
        if min_turns <= 0:
            return
        user_turns = sum(1 for m in self.session.messages if m.role == "user")
        if user_turns < min_turns:
            return
        meta = self.session.meta
        if meta.get("_memory_flush_reviewed"):
            return
        if int(meta.get("_turns_since_memory", 0) or 0) <= 0:
            return
        meta["_memory_flush_reviewed"] = True
        try:
            from . import review
            review.run_review(self, "memory", on_event=getattr(self.tool_context, "emit", None))
            meta["_turns_since_memory"] = 0
        except Exception:  # noqa: BLE001
            try:
                from .._log import log_exc
                log_exc("session-end memory review failed")
            except Exception:  # noqa: BLE001
                pass

    # -- run ----------------------------------------------------------------
    def _provider_target(self, provider) -> tuple[str, str]:
        return (
            str(getattr(provider, "name", "") or ""),
            str(getattr(provider, "model", "") or ""),
        )

    def _provider_matches(self, provider_name: str, model: str) -> bool:
        targets = [self.provider, getattr(self.provider, "primary", None)]
        for provider in targets:
            if provider is None:
                continue
            cur_provider, cur_model = self._provider_target(provider)
            if (not provider_name or cur_provider == provider_name) and (
                    not model or cur_model == model):
                return True
        return False

    @staticmethod
    def _route_marker_value(marker: dict, key: str) -> str:
        if not isinstance(marker, dict):
            return ""
        value = marker.get(key)
        if value:
            return str(value)
        if key in {"provider", "model"}:
            selected = marker.get("selected")
            if isinstance(selected, dict):
                return str(selected.get(key) or "")
        if key in {"base_provider", "base_model"}:
            base = marker.get("base")
            base_key = "provider" if key == "base_provider" else "model"
            if isinstance(base, dict):
                return str(base.get(base_key) or "")
        return ""

    def _one_turn_selection(self) -> dict:
        meta = self.session.meta if isinstance(self.session.meta, dict) else {}
        selection = meta.get("runtime_selection")
        if not isinstance(selection, dict) or not selection.get("one_turn"):
            return {}
        if selection.get("source") not in {"prompt_route", "budget_downshift"}:
            return {}
        selected = selection.get("selected")
        base = selection.get("base")
        if not isinstance(selected, dict) or not isinstance(base, dict):
            return {}
        if not (selected.get("provider") or selected.get("model")):
            return {}
        if not (base.get("provider") or base.get("model")):
            return {}
        return selection

    def _routing_base_target(self) -> tuple[str, str]:
        meta = self.session.meta if isinstance(self.session.meta, dict) else {}
        controls = meta.get("runtime_controls") if isinstance(meta.get("runtime_controls"), dict) else {}
        marker = meta.get("_prompt_route_runtime") if isinstance(
            meta.get("_prompt_route_runtime"), dict) else {}
        runtime = meta.get("runtime") if isinstance(meta.get("runtime"), dict) else {}
        selection = self._one_turn_selection()
        selected = selection.get("selected") if isinstance(selection.get("selected"), dict) else {}
        base = selection.get("base") if isinstance(selection.get("base"), dict) else {}
        runtime_is_route = bool(
            marker
            and runtime.get("provider") == self._route_marker_value(marker, "provider")
            and runtime.get("model") == self._route_marker_value(marker, "model")
        )
        runtime_is_one_turn = bool(
            selection.get("one_turn")
            and runtime.get("provider") == selected.get("provider")
            and runtime.get("model") == selected.get("model")
        )
        current_provider, current_model = self._provider_target(self.provider)
        current_is_one_turn = bool(
            selection.get("restored")
            and current_provider == selected.get("provider")
            and current_model == selected.get("model")
        )
        persisted_base_provider = str(base.get("provider") or "") if (
            selection.get("restored") and (runtime_is_one_turn or current_is_one_turn)
        ) else ""
        persisted_base_model = str(base.get("model") or "") if (
            selection.get("restored") and (runtime_is_one_turn or current_is_one_turn)
        ) else ""
        provider_name = (
            controls.get("provider")
            or meta.get("provider")
            or self._route_marker_value(marker, "base_provider")
            or persisted_base_provider
            or ("" if runtime_is_route or runtime_is_one_turn else runtime.get("provider", ""))
            or current_provider
            or self.config.get("model.provider", "")
        )
        model = (
            controls.get("model")
            or meta.get("model")
            or self._route_marker_value(marker, "base_model")
            or persisted_base_model
            or ("" if runtime_is_route or runtime_is_one_turn else runtime.get("model", ""))
            or current_model
            or self.config.get("model.default", "")
        )
        return str(provider_name or ""), str(model or "")

    def _restore_prompt_route_base(self) -> None:
        meta = self.session.meta if isinstance(self.session.meta, dict) else {}
        marker = meta.get("_prompt_route_runtime") if isinstance(
            meta.get("_prompt_route_runtime"), dict) else {}
        base_provider = getattr(self, "_prompt_route_base_provider", None)
        if base_provider is not None:
            self.provider = base_provider
            self._prompt_route_base_provider = None
            meta.pop("_prompt_route_runtime", None)
            self._mark_runtime_selection_restored()
            return
        if not marker:
            selection = self._one_turn_selection()
            selected = selection.get("selected") if isinstance(selection.get("selected"), dict) else {}
            selected_provider = str(selected.get("provider") or "")
            selected_model = str(selected.get("model") or "")
            if not (
                selection.get("restored")
                and self._provider_matches(selected_provider, selected_model)
            ):
                return
            provider_name, model = self._routing_base_target()
            if (provider_name or model) and not self._provider_matches(provider_name, model):
                try:
                    from ..providers.fallback import build_with_fallbacks
                    self.provider = build_with_fallbacks(
                        self.config, model=model or None, name=provider_name or None)
                    self._mark_runtime_selection_restored()
                except Exception:  # noqa: BLE001
                    pass
            return
        provider_name, model = self._routing_base_target()
        if not self._provider_matches(provider_name, model):
            try:
                from ..providers.fallback import build_with_fallbacks
                self.provider = build_with_fallbacks(
                    self.config, model=model or None, name=provider_name or None)
            except Exception:  # noqa: BLE001
                pass
        meta.pop("_prompt_route_runtime", None)
        self._mark_runtime_selection_restored()

    def _record_runtime_selection(self, selection: dict) -> None:
        clean = {str(k): v for k, v in selection.items() if v not in (None, "")}
        if clean:
            self.session.meta["runtime_selection"] = clean
            self.session.meta["last_runtime_selection"] = clean
            self._runtime_selection_active = True

    def _runtime_selection(
        self,
        source: str,
        *,
        provider: str,
        model: str,
        base_provider: str,
        base_model: str,
        match: str = "",
        restored: bool = False,
    ) -> dict:
        selection = {
            "source": source,
            "selected": {"provider": provider, "model": model},
            "base": {"provider": base_provider, "model": base_model},
            "one_turn": (
                source in {"prompt_route", "budget_downshift"}
                and (provider, model) != (base_provider, base_model)
            ),
            "restored": bool(restored),
        }
        if match:
            selection["match"] = match
        return selection

    def _mark_runtime_selection_restored(self) -> None:
        selection = self.session.meta.get("runtime_selection")
        if isinstance(selection, dict) and selection.get("one_turn"):
            selection = dict(selection)
            selection["restored"] = True
            selection["restore_reason"] = "base_runtime_restore"
            provider, model = self._provider_target(self.provider)
            selection["restored_to"] = {"provider": provider, "model": model}
            self._record_runtime_selection(selection)

    def _ensure_base_runtime_selection(self) -> None:
        if self._runtime_selection_active:
            return
        provider, model = self._provider_target(self.provider)
        self._record_runtime_selection(self._runtime_selection(
            "base",
            provider=provider,
            model=model,
            base_provider=provider,
            base_model=model,
        ))

    def _apply_routing(self, text: str, on_event: OnEvent | None = None) -> None:
        """Per-prompt provider routing: swap provider/model when a rule matches."""
        import re
        rules = self.config.get("routing", []) or []
        if not rules:
            self._restore_prompt_route_base()
            return
        base_provider, base_model = self._routing_base_target()
        for rule in rules:
            try:
                if not re.search(rule.get("match", ""), text, re.I):
                    continue
                target_provider = str(rule.get("provider") or base_provider or "")
                target_model = str(rule.get("model") or base_model or "")
                replacement = None
                if not self._provider_matches(target_provider, target_model):
                    from ..providers.fallback import build_with_fallbacks
                    replacement = build_with_fallbacks(
                        self.config, model=target_model or None, name=target_provider or None)
                if (target_provider, target_model) != (base_provider, base_model):
                    existing_marker = isinstance(
                        self.session.meta.get("_prompt_route_runtime"), dict)
                    if (getattr(self, "_prompt_route_base_provider", None) is None
                            and not existing_marker):
                        self._prompt_route_base_provider = self.provider
                    self.session.meta["_prompt_route_runtime"] = {
                        "source": "prompt_route",
                        "match": str(rule.get("match", "") or ""),
                        "provider": target_provider,
                        "model": target_model,
                        "base_provider": base_provider,
                        "base_model": base_model,
                    }
                else:
                    self.session.meta.pop("_prompt_route_runtime", None)
                    self._prompt_route_base_provider = None
                if replacement is not None:
                    self.provider = replacement
                if (target_provider, target_model) != (base_provider, base_model):
                    selection = self._runtime_selection(
                        "prompt_route",
                        provider=target_provider,
                        model=target_model,
                        base_provider=base_provider,
                        base_model=base_model,
                        match=str(rule.get("match", "") or ""),
                    )
                    self._record_runtime_selection(selection)
                    if on_event is not None:
                        on_event({
                            "type": "runtime_route",
                            "source": "prompt_route",
                            "provider": target_provider,
                            "model": target_model,
                            "base_provider": base_provider,
                            "base_model": base_model,
                            "match": str(rule.get("match", "") or ""),
                            "runtime_selection": selection,
                        })
                return
            except (re.error, Exception):  # noqa: BLE001
                continue
        self._restore_prompt_route_base()

    def _apply_budget_governor(self, text: str, on_event: OnEvent | None) -> None:
        """Per-turn cost governor: emit a spend warning near/over the cap, and downshift a
        simple turn to the cheap model when ``budget.auto_downshift`` is on. Best-effort and
        only swaps the model when no prompt-routing override already took effect."""
        try:
            from ..governor import budget_status, downshift_model
            st = budget_status(self.config, session_spend=self._session_spend_usd())
            if st.warning and on_event:
                on_event({
                    "type": "budget_warning",
                    "message": st.warning,
                    "over": st.over,
                    "blocked": st.should_block,
                    "daily_spend": st.daily_spend,
                    "daily_cap": st.daily_cap,
                    "session_spend": st.session_spend,
                    "session_cap": st.session_cap,
                    "enforce": st.enforce,
                    "over_daily": st.over_daily,
                    "over_session": st.over_session,
                })
            if st.should_block:
                self.session.meta["_budget_blocked_turn"] = {
                    "message": st.warning,
                    "daily_spend": st.daily_spend,
                    "daily_cap": st.daily_cap,
                    "session_spend": st.session_spend,
                    "session_cap": st.session_cap,
                    "enforce": st.enforce,
                    "over_daily": st.over_daily,
                    "over_session": st.over_session,
                }
                return
            self.session.meta.pop("_budget_blocked_turn", None)
            if self.session.meta.get("_prompt_route_runtime"):
                return                       # an explicit routing rule wins over downshift
            cheap = downshift_model(text, self.config)
            if cheap and str(getattr(self.provider, "model", "")) != cheap:
                from ..providers.fallback import build_with_fallbacks
                base_provider, base_model = self._provider_target(self.provider)
                if getattr(self, "_prompt_route_base_provider", None) is None:
                    self._prompt_route_base_provider = self.provider
                self.provider = build_with_fallbacks(self.config, model=cheap)
                target_provider, target_model = self._provider_target(self.provider)
                selection = self._runtime_selection(
                    "budget_downshift",
                    provider=target_provider,
                    model=target_model or cheap,
                    base_provider=base_provider,
                    base_model=base_model,
                )
                self.session.meta["_prompt_route_runtime"] = {
                    "source": "budget_downshift",
                    "provider": target_provider,
                    "model": target_model or cheap,
                    "base_provider": base_provider,
                    "base_model": base_model,
                }
                self._record_runtime_selection(selection)
                if on_event:
                    on_event({
                        "type": "model_downshift",
                        "model": target_model or cheap,
                        "provider": target_provider,
                        "base_model": base_model,
                        "base_provider": base_provider,
                        "runtime_selection": selection,
                    })
        except Exception:  # noqa: BLE001 — governor must never break a turn
            pass

    def _session_spend_usd(self) -> float:
        """Approximate USD spent on this session so far (from logged turn usage)."""
        try:
            from ..usage_log import _cache_write_mult, _extra_rates, _price, _turn_cost
            u = self.budget.usage
            pin, pout = _price(str(getattr(self.provider, "model", "")), self.config)
            entry = {
                "provider": str(getattr(self.provider, "name", "") or ""),
                "model": str(getattr(self.provider, "model", "") or ""),
                "input": int(getattr(u, "input_tokens", 0) or 0),
                "output": int(getattr(u, "output_tokens", 0) or 0),
                "cache_read": int(getattr(u, "cache_read", 0) or 0),
                "cache_write": int(getattr(u, "cache_write", 0) or 0),
            }
            return _turn_cost(
                entry,
                pin,
                pout,
                _cache_write_mult(self.config),
                _extra_rates(entry["model"], self.config),
            )
        except Exception:  # noqa: BLE001
            return 0.0

    def _touch_activity(self, desc: str) -> None:
        """Record a lightweight heartbeat for status/debug surfaces."""
        try:
            self._last_activity_ts = time.time()
            self._last_activity_desc = str(desc or "activity")
        except Exception:  # noqa: BLE001
            pass

    def get_activity_summary(self) -> dict:
        """Return a reference-style snapshot of the agent's current activity."""
        now = time.time()
        last_ts = float(getattr(self, "_last_activity_ts", 0.0) or now)
        budget = getattr(self, "budget", None)
        budget_used = int(getattr(budget, "api_call_count", 0) or 0)
        budget_max = int(getattr(budget, "max_iterations", 0) or 0)
        turn_api_count = int(getattr(self, "_turn_api_request_count", 0) or 0)
        trace_ctx = getattr(self, "_trace_context", {}) or {}
        session = getattr(self, "session", None)
        summary = {
            "last_activity_ts": last_ts,
            "last_activity_desc": str(getattr(self, "_last_activity_desc", "") or ""),
            "seconds_since_activity": round(max(0.0, now - last_ts), 1),
            "current_tool": str(getattr(self, "_current_tool", "") or ""),
            "current_api_request_id": str(getattr(self, "_current_api_request_id", "") or ""),
            "last_api_request_id": str(getattr(self, "_last_api_request_id", "") or ""),
            "api_call_count": max(budget_used, turn_api_count),
            "completed_api_call_count": budget_used,
            "turn_api_request_count": turn_api_count,
            "max_iterations": budget_max,
            "budget_used": budget_used,
            "budget_remaining": max(0, budget_max - budget_used),
            "budget_max": budget_max,
            "tools_used": int(getattr(self, "tools_used", 0) or 0),
        }
        ids = {
            "session_id": getattr(session, "id", ""),
            "trace_id": trace_ctx.get("trace_id", ""),
            "turn_id": trace_ctx.get("turn_id", "") or getattr(self, "_current_turn_id", ""),
            "run_id": getattr(self, "_surface_run_id", ""),
        }
        summary.update({key: str(value) for key, value in ids.items() if value})
        return summary

    def _event_ids(self) -> dict[str, str]:
        session = getattr(self, "session", None)
        trace_ctx = getattr(self, "_trace_context", {}) or {}
        data = {
            "session_id": getattr(session, "id", ""),
            "trace_id": trace_ctx.get("trace_id", ""),
            "turn_id": trace_ctx.get("turn_id", "") or getattr(self, "_current_turn_id", ""),
            "run_id": getattr(self, "_surface_run_id", ""),
        }
        return {key: str(value) for key, value in data.items() if value}

    def _stamp_event(self, event: dict) -> dict:
        try:
            stamped = dict(event or {})
        except Exception:  # noqa: BLE001
            stamped = {"type": "event", "value": str(event)}
        for key, value in self._event_ids().items():
            if value and not stamped.get(key):
                stamped[key] = value
        return stamped

    def _record_event_activity(self, event: dict) -> None:
        event_type = str(event.get("type") or "")
        if not event_type:
            return
        if event_type == "iteration":
            n = event.get("n")
            max_n = event.get("max")
            self._touch_activity(f"iteration {n}/{max_n}" if max_n is not None else f"iteration {n}")
            return
        if event_type == "provider_start":
            provider = str(event.get("provider") or getattr(self.provider, "name", "") or "provider")
            model = str(event.get("model") or getattr(self.provider, "model", "") or "")
            self._touch_activity(f"calling {provider}/{model}" if model else f"calling {provider}")
            return
        if event_type == "provider_end":
            status = str(event.get("status") or "done")
            self._touch_activity(f"provider call {status}")
            return
        if event_type == "assistant_delta":
            self._touch_activity("streaming assistant response")
            return
        if event_type == "reasoning_delta":
            self._touch_activity("streaming reasoning")
            return
        if event_type == "assistant_message":
            self._touch_activity("assistant message received")
            return
        if event_type == "tool_start":
            name = str(event.get("name") or "tool")
            self._current_tool = name
            self._touch_activity(f"running tool {name}")
            return
        if event_type == "tool_result":
            name = str(event.get("name") or "tool")
            if str(getattr(self, "_current_tool", "") or "") == name:
                self._current_tool = ""
            self._touch_activity(f"tool {name} finished")
            return
        if event_type == "mcp_tools_refreshed":
            self._touch_activity("mcp tools refreshed")
            return
        if event_type == "final":
            self._current_tool = ""
            self._touch_activity("final response emitted")
            return
        if event_type == "error":
            self._touch_activity("error emitted")

    def _notify_event_callback(self, event: dict) -> None:
        if event.get("type") == "session:compress":
            return
        callback = getattr(self, "event_callback", None)
        if not callable(callback):
            return
        event_type = str(event.get("type") or "")
        payload = dict(event)
        payload.pop("type", None)
        try:
            callback(event_type, payload)
        except Exception:  # noqa: BLE001
            pass

    def _make_event_emitter(self, on_event: OnEvent | None = None) -> OnEvent:
        if getattr(on_event, "_aegis_agent_event_emitter", False):
            return on_event

        def emit(event: dict) -> None:
            stamped = self._stamp_event(event)
            self._record_event_activity(stamped)
            self._notify_event_callback(stamped)
            if on_event is not None:
                on_event(stamped)

        emit._aegis_agent_event_emitter = True  # type: ignore[attr-defined]
        return emit

    def cancel(self) -> None:
        """Request the current run to stop at the next safe point (interrupt-aware loop)."""
        self.cancel_event.set()
        run_thread_id = getattr(self, "_run_thread_id", None)
        if run_thread_id is not None:
            try:
                from ..tools.interrupt import set_interrupt

                set_interrupt(True, run_thread_id)
            except Exception:  # noqa: BLE001
                pass
        response_id = str(getattr(self, "_active_response_id", "") or "")
        provider = getattr(self, "provider", None)
        cancel_response = getattr(provider, "cancel_response", None)
        if not (response_id and callable(cancel_response)):
            return
        if getattr(self, "_active_response_cancelled", "") == response_id:
            return
        self._active_response_cancelled = response_id
        try:
            import threading
            threading.Thread(target=lambda: cancel_response(response_id), daemon=True).start()
        except Exception:  # noqa: BLE001
            pass

    def steer(self, text: str) -> bool:
        """Inject guidance into a run in progress; the loop folds it into the next model call
        without restarting the turn. Returns True if queued."""
        if text and text.strip():
            self.steer_queue.put(text.strip())
            return True
        return False

    def _begin_turn_prologue(self) -> None:
        """Reset per-turn state before durable context is assembled."""
        self.cancel_event.clear()
        self._restore_prompt_route_base()
        restore_primary = getattr(self.provider, "restore_primary_runtime", None)
        if callable(restore_primary):
            try:
                restore_primary()
            except Exception:  # noqa: BLE001
                pass
        self._runtime_selection_active = False
        self._current_task_id = self._terminal_task_id or getattr(self.session, "id", "")
        self._current_turn_id = new_id("turn")
        self._current_api_request_id = ""
        self._last_api_request_id = ""
        self._turn_api_request_count = 0
        self._turn_started_user_index = None
        self._active_response_id = ""
        self._active_response_cancelled = ""
        self._compact_stuck = False        # reset the no-progress-compaction guard each turn
        self._overflow_retried = False     # one-shot context_overflow -> compress guard, per turn
        self._output_cap_retried = False   # one-shot max_tokens-too-large -> lower cap retry
        self._ephemeral_max_output_tokens = 0
        self._strip_thinking = False       # one-shot thinking-signature 400 -> resend w/o blocks
        self._retrieved_memory_for_turn = ""
        self._retrieved_memory_user_content = ""
        self._wire_user_content_target = ""
        self._wire_user_content_override = ""
        self._last_turn_usage = Usage()
        self._turn_prologue_prepared = True
        self._touch_activity("turn initialized")

    def _inject_relevant_skills(
        self,
        selection_text: str,
        user_text: str,
        on_event: OnEvent | None = None,
    ) -> str:
        if not self.skills:
            return user_text
        try:
            explicit = self.skills.invocation_from_slash(selection_text)
            if explicit:
                prompt, names = explicit
                self.session.meta["active_skills"] = names
                self.session.meta["active_skills_source"] = "slash"
                if on_event is not None:
                    on_event({
                        "type": "skill_loaded",
                        "names": names,
                        "source": "slash",
                        "summary": "loaded skills: " + ", ".join(names),
                    })
                return prompt
        except Exception:  # noqa: BLE001
            pass
        preloaded_block = ""
        preloaded_names: list[str] = []
        try:
            requested = self.session.meta.pop("pending_skill_preload", []) or []
            source = str(self.session.meta.pop("pending_skill_preload_source", "turn") or "turn")
            if requested:
                max_chars = int(self.config.get("skills.auto_load_max_chars", 24000) or 24000)
                preloaded_block, preloaded_names, missing = self.skills.preload_block(
                    requested,
                    source=source,
                    user_instruction=selection_text,
                    max_chars=max_chars,
                )
                if missing:
                    warning = "[Missing preloaded skills: " + ", ".join(missing) + "]"
                    preloaded_block = f"{preloaded_block}\n\n{warning}".strip()
                if preloaded_names:
                    self.session.meta["active_skills"] = preloaded_names
                    self.session.meta["active_skills_source"] = "preload"
                    if on_event is not None:
                        on_event({
                            "type": "skill_loaded",
                            "names": preloaded_names,
                            "source": "preload",
                            "summary": "preloaded skills: " + ", ".join(preloaded_names),
                        })
        except Exception:  # noqa: BLE001
            preloaded_block = ""
            preloaded_names = []
        if not bool(self.config.get("skills.auto_load", True)):
            return f"{preloaded_block}\n\n[User task]\n{user_text}" if preloaded_block else user_text
        try:
            limit = int(self.config.get("skills.auto_load_limit", 3) or 3)
            min_score = int(self.config.get("skills.auto_load_min_score", 6) or 6)
            max_chars = int(self.config.get("skills.auto_load_max_chars", 24000) or 24000)
            block, names = self.skills.autoload_block(
                selection_text,
                limit=limit,
                min_score=min_score,
                max_chars=max_chars,
                exclude=set(preloaded_names),
            )
            if not block:
                if not preloaded_block:
                    self.session.meta.pop("active_skills", None)
                    self.session.meta.pop("active_skills_source", None)
                    return user_text
                return f"{preloaded_block}\n\n[User task]\n{user_text}"
            all_names = [*preloaded_names, *[name for name in names if name not in preloaded_names]]
            self.session.meta["active_skills"] = all_names
            self.session.meta["active_skills_source"] = "preload+auto" if preloaded_names else "auto"
            if on_event is not None:
                on_event({
                    "type": "skill_loaded",
                    "names": names,
                    "source": "auto",
                    "summary": "auto-loaded skills: " + ", ".join(names),
                })
            blocks = "\n\n".join(part for part in (preloaded_block, block) if part)
            return f"{blocks}\n\n[User task]\n{user_text}"
        except Exception:  # noqa: BLE001
            return f"{preloaded_block}\n\n[User task]\n{user_text}" if preloaded_block else user_text

    def run(self, user_input: str | Message, on_event: OnEvent | None = None) -> Message:
        self._begin_turn_prologue()
        emit = self._make_event_emitter(on_event)
        include_wakeups = not bool(getattr(self, "_skip_wakeups_once", False))
        self._skip_wakeups_once = False
        if not any(m.role != "system" for m in self.session.messages):  # first turn of a session
            from ..plugins import fire_hook
            fire_hook("on_session_start", self)
            try:
                from ..hooks import run_hooks
                run_hooks(
                    self.config,
                    "session_start",
                    {
                        "session_id": self.session.id,
                        "provider": getattr(self.provider, "name", ""),
                        "model": getattr(self.provider, "model", ""),
                        "cwd": str(self.cwd),
                    },
                )
            except Exception:  # noqa: BLE001
                pass
        msg = user_input if isinstance(user_input, Message) else Message.user(user_input)
        original_user_content = msg.content
        self._apply_routing(original_user_content, emit)
        self._apply_budget_governor(original_user_content, emit)
        self._ensure_base_runtime_selection()
        self.session.maybe_title_from(original_user_content)
        provider_query = original_user_content
        provider_user_content = original_user_content
        if self.memory:
            try:
                turn_number = sum(1 for m in self.session.messages if m.role == "user") + 1
                self.memory.on_turn_start(
                    turn_number,
                    provider_query,
                    model=str(getattr(self.provider, "model", "") or ""),
                    provider=str(getattr(self.provider, "name", "") or ""),
                    platform=str(getattr(self, "platform", "") or "cli"),
                    remaining_iterations=self.budget.remaining,
                    tool_count=len(self.registry.all()),
                    cwd=str(self.cwd),
                )
            except Exception:  # noqa: BLE001
                pass
        if include_wakeups:
            try:                           # background work that finished since the last turn
                from .wakeups import wakeup_block
                wb = wakeup_block(session_key=str(getattr(self.session, "id", "") or ""))
                if wb:
                    provider_user_content = f"{wb}\n\n{provider_user_content}"
            except Exception:  # noqa: BLE001
                pass
        if self.skills and self.skills.is_stale():
            self.session.meta["_rebuild_system_prompt"] = True
        provider_user_content = self._inject_relevant_skills(
            original_user_content,
            provider_user_content,
            emit,
        )
        if provider_user_content != original_user_content:
            self._wire_user_content_target = original_user_content
            self._wire_user_content_override = provider_user_content
        if self.memory:                    # provider prefetch relevant to THIS turn (volatile)
            try:
                fetched = self.memory.prefetch(provider_query)
                if fetched:
                    self._retrieved_memory_for_turn = fetched
                    self._retrieved_memory_user_content = original_user_content
                self.memory.queue_prefetch(provider_query)   # warm the next turn in the background
            except Exception:  # noqa: BLE001
                pass
        self.session.messages.append(msg)
        self._turn_started_user_index = len(self.session.messages) - 1
        self.tool_context.emit = emit
        try:
            from ..hooks import run_hooks
            run_hooks(self.config, "user_prompt", {"text": msg.content[:300], "session_id": self.session.id})
        except Exception:  # noqa: BLE001
            pass
        if self.memory:
            self.memory.history.append(
                "user",
                self._clean_gateway_timestamp_content(msg),
                self.session.id,
            )

        before = (
            self.budget.usage.input_tokens,
            self.budget.usage.output_tokens,
            self.budget.usage.cache_read,
            self.budget.usage.cache_write,
        )
        tools_before = self.tools_used
        session_token = approver_token = None
        run_thread_id = None
        try:
            import threading
            from ..tools.interrupt import clear_interrupt
            from ..tools.thread_context import (
                reset_current_approver,
                reset_current_session_key,
                set_current_approver,
                set_current_session_key,
            )

            run_thread_id = threading.current_thread().ident
            self._run_thread_id = run_thread_id
            clear_interrupt(run_thread_id)
            session_token = set_current_session_key(getattr(self.session, "id", ""))
            approver_token = set_current_approver(getattr(self.tool_context, "approver", None))
            result = run_conversation(self, emit)
        finally:
            if approver_token is not None:
                try:
                    reset_current_approver(approver_token)
                except Exception:  # noqa: BLE001
                    pass
            if session_token is not None:
                try:
                    reset_current_session_key(session_token)
                except Exception:  # noqa: BLE001
                    pass
            if run_thread_id is not None:
                try:
                    from ..tools.interrupt import clear_interrupt

                    clear_interrupt(run_thread_id)
                except Exception:  # noqa: BLE001
                    pass
            self._run_thread_id = None
            self._ultracode_active = False   # ultracode mode is scoped to a single turn
        tools_this_turn = self.tools_used - tools_before
        msg.content = self._clean_gateway_timestamp_content(msg)

        from ..types import Usage
        turn = Usage(self.budget.usage.input_tokens - before[0],
                     self.budget.usage.output_tokens - before[1],
                     self.budget.usage.cache_read - before[2],
                     self.budget.usage.cache_write - before[3])
        self._last_turn_usage = turn
        self._last_turn_cost = {}

        # Log this turn's token usage (for `aegis cost` / insights).
        try:
            from .. import usage_log
            trace_ctx = getattr(self, "_trace_context", {}) or {}
            event_ids = self._event_ids()
            self._last_turn_cost = usage_log.cost_evidence(
                self.provider.name,
                self.provider.model,
                turn,
                self.config,
            )
            try:
                usage_log.log(
                    self.provider.name,
                    self.provider.model,
                    turn,
                    session_id=event_ids.get("session_id", ""),
                    turn_id=event_ids.get("turn_id", "") or str(trace_ctx.get("turn_id", "") or ""),
                    trace_id=event_ids.get("trace_id", "") or str(trace_ctx.get("trace_id", "") or ""),
                    run_id=event_ids.get("run_id", ""),
                    config=self.config,
                )
            except TypeError:
                usage_log.log(self.provider.name, self.provider.model, turn)
        except Exception:  # noqa: BLE001
            pass
        self._update_runtime_meta(tools_this_turn)
        self._restore_prompt_route_base()
        self._mark_runtime_selection_restored()

        turn_interrupted = bool(result.meta.get("interrupted")) or (
            str(result.meta.get("turn_status") or "") == "cancelled"
        )
        cleanup_errors = list(result.meta.get("cleanup_errors") or [])
        visible_final = bool((result.content or "").strip())

        def _record_cleanup_error(label: str, exc: Exception) -> None:
            cleanup_errors.append(f"{label}: {exc}")
            result.meta["cleanup_errors"] = cleanup_errors

        if self.store:
            try:
                self.store.save(self.session)
            except Exception as exc:  # noqa: BLE001  (a save failure must not lose the turn's reply)
                _record_cleanup_error("final_session_save", exc)
                from .._log import log_exc
                log_exc("final session save failed")
        if self.memory and not turn_interrupted and visible_final:
            try:
                self.memory.history.append("assistant", result.content, self.session.id)
                self.memory.sync_turn(self.session.messages)   # fan out to the provider, fail-soft
            except Exception as exc:  # noqa: BLE001
                _record_cleanup_error("memory_sync", exc)
                try:
                    from .._log import log_exc
                    log_exc("memory sync failed")
                except Exception:  # noqa: BLE001
                    pass
        if visible_final and not turn_interrupted:
            try:
                from . import review
                review.maybe_review(self, tools_this_turn)   # forked self-improvement
            except Exception as exc:  # noqa: BLE001
                _record_cleanup_error("background_review", exc)
        try:
            from .. import trajectory
            trajectory.capture_turn(self.config, self.session)
        except Exception as exc:  # noqa: BLE001
            _record_cleanup_error("trajectory", exc)
        if not cleanup_errors:
            result.meta.pop("cleanup_errors", None)
        return result

    @staticmethod
    def _clean_gateway_timestamp_content(msg: Message) -> str:
        rendered = str(msg.meta.get("gateway_timestamp_rendered_content") or "")
        has_clean = "gateway_timestamp_clean_content" in msg.meta
        clean = str(msg.meta.get("gateway_timestamp_clean_content") or "")
        content = msg.content
        if rendered and has_clean and rendered in content:
            return content.replace(rendered, clean, 1)
        return content

    def _update_runtime_meta(self, tools_this_turn: int = 0) -> None:
        api_mode = getattr(self.provider, "api_mode", "")
        api_mode_value = getattr(api_mode, "value", str(api_mode) if api_mode else "")
        usage = self.budget.usage
        trace_ctx = getattr(self, "_trace_context", {}) or {}
        controls = self.session.meta.get("runtime_controls")
        controls = controls if isinstance(controls, dict) else {}
        runtime = dict(self.session.meta.get("runtime") or {})
        runtime.update({
            "provider": getattr(self.provider, "name", ""),
            "model": getattr(self.provider, "model", ""),
            "transport": api_mode_value,
            "base_url": getattr(self.provider, "base_url", ""),
            "context_length": getattr(self.provider, "context_length", 0),
            "reasoning_effort": getattr(self, "reasoning", ""),
            "reasoning_display": self.config.get("display.reasoning", "summary"),
            "busy_mode": self.config.get("gateway.busy_mode", "queue"),
            "service_tier": "priority" if getattr(self, "service_tier", "") == "priority" else "normal",
        })
        runtime.update({k: v for k, v in controls.items()
                        if k in {"reasoning_effort", "reasoning_display", "busy_mode", "service_tier"}})
        self.session.meta["runtime"] = runtime
        usage_meta = {
            "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
            "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
            "cache_read": int(getattr(usage, "cache_read", 0) or 0),
            "cache_write": int(getattr(usage, "cache_write", 0) or 0),
        }
        last_turn_cost = getattr(self, "_last_turn_cost", {}) or {}
        if isinstance(last_turn_cost, dict) and last_turn_cost:
            clean_cost = {str(k): v for k, v in last_turn_cost.items() if v is not None}
            usage_meta["last_turn_cost"] = clean_cost
            usage_meta["last_turn_cost_status"] = clean_cost.get("cost_status", "")
            usage_meta["last_turn_cost_source"] = clean_cost.get("cost_source", "")
            self.session.meta["last_turn_cost"] = clean_cost
        self.session.meta["usage"] = usage_meta
        trace_id = str(trace_ctx.get("trace_id", "") or "")
        turn_id = str(trace_ctx.get("turn_id", "") or getattr(self, "_current_turn_id", "") or "")
        if trace_id:
            self.session.meta["trace_id"] = trace_id
            self.session.meta["last_trace_id"] = trace_id
        if turn_id:
            self.session.meta["turn_id"] = turn_id
            self.session.meta["last_turn_id"] = turn_id
        last_api_request_id = str(getattr(self, "_last_api_request_id", "") or "")
        if last_api_request_id:
            self.session.meta["last_api_request_id"] = last_api_request_id
        self.session.meta["last_turn_api_request_count"] = int(
            getattr(self, "_turn_api_request_count", 0) or 0
        )
        self.session.meta["tool_call_count"] = int(self.tools_used)
        self.session.meta["last_turn_tool_count"] = int(tools_this_turn)
        try:
            from ..responses_state import ResponsesStateStore
            request_state = self.session.meta.get("response_state_request")
            request_state = request_state if isinstance(request_state, dict) else {}
            state = ResponsesStateStore().get(self.session.id)
            if state is not None:
                self.session.meta["response_state"] = {
                    "response_id": state.response_id,
                    "provider": state.provider,
                    "model": state.model,
                    "updated_at": state.updated_at,
                    "previous_response_id": request_state.get("previous_response_id", ""),
                    "store": bool(request_state.get("store", True)),
                    "send_previous": bool(request_state.get("send_previous", True)),
                }
            elif request_state:
                self.session.meta.pop("response_state", None)
        except Exception:  # noqa: BLE001
            pass
