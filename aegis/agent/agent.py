"""The Agent: wires provider + tools + memory + skills + session into the loop."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..config import Config, Workspace
from ..constants import DEFAULT_MAX_ITERATIONS
from ..memory import MemoryManager
from ..providers import Provider
from ..session import Session, SessionStore
from ..skills import SkillsLoader
from ..tools.base import ToolContext
from ..tools.permissions import PermissionEngine
from ..tools.registry import ToolRegistry, default_registry
from ..types import Message, Usage
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
        store: SessionStore | None = None,
    ):
        self.config = config
        self.provider = provider
        self.session = session
        self.cwd = cwd or Path.cwd()
        self.registry = registry or default_registry()
        self._context_engine = None
        try:                                   # a custom context engine may expose its own tools
            from .context_engine import get_engine
            self._context_engine = get_engine(config)
            for t in self._context_engine.tools():
                self.registry.register(t)
        except Exception:  # noqa: BLE001
            pass
        self.permissions = PermissionEngine(config)
        self.memory = memory if memory is not None else (
            MemoryManager(config) if config.get("memory.enabled", True) else None
        )
        self.skills = skills or SkillsLoader(config, self.cwd)
        self.workspace = Workspace(self.cwd)
        self.context_builder = ContextBuilder(config, self.workspace, self.cwd)
        self.store = store
        self.stream = bool(config.get("agent.stream", True))
        self.reasoning = config.get("agent.reasoning_effort", "off")
        self.budget = IterationBudget(int(config.get("agent.max_iterations", DEFAULT_MAX_ITERATIONS)))
        self.tools_used = 0
        self.activated_tools: set[str] = set()   # deferred tools loaded via tool_search this session
        self.platform: str | None = None   # set by the gateway to the active channel (telegram, …)
        self.chat_id: str | None = None     # set by the gateway to the active conversation id
        import queue
        import threading
        self.cancel_event = threading.Event()   # set by .cancel() to interrupt a run
        self.steer_queue: queue.Queue = queue.Queue()   # mid-run guidance injected via .steer()

        if self.memory is not None:
            self.memory.initialize(getattr(self.session, "id", ""))   # provider warm-up
            for t in self.memory.provider_tools():                    # provider-specific tools
                try:
                    self.registry.register(t)
                except Exception:  # noqa: BLE001
                    pass

        self.tool_context = ToolContext(
            cwd=self.cwd, config=config, memory=self.memory, skills=self.skills,
            session=self.session, agent=self, approver=approver,
        )
        if self._context_engine is not None:
            try:
                from .context_engine import call_hook
                call_hook(self._context_engine, "on_session_start", self)
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
        store: SessionStore | None = None,
        include_mcp: bool = False,
        registry: ToolRegistry | None = None,
        memory: MemoryManager | None = None,
    ) -> "Agent":
        from ..providers.fallback import build_with_fallbacks
        provider = build_with_fallbacks(config, model=model, name=provider_name)
        session = session or Session.create()
        agent = cls(config=config, provider=provider, session=session, cwd=cwd,
                    approver=approver, store=store, registry=registry, memory=memory)
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
        enabled_tools = self.registry.available(toolsets)
        return (
            "# AEGIS runtime\n"
            f"- provider: {getattr(self.provider, 'name', 'unknown')}\n"
            f"- model: {getattr(self.provider, 'model', 'unknown')}\n"
            f"- transport: {api_mode_value}\n"
            f"- auth: {auth_desc} ({auth_state})\n"
            f"- cwd: {self.cwd}\n"
            f"- toolsets: {', '.join(toolsets)}\n"
            f"- model-visible tools: {len(enabled_tools)}/{len(self.registry.all())}\n"
            "- For questions about whether you are using OAuth, API-key auth, or local auth, "
            "use the auth line above as ground truth.\n"
            "- For install, auth, tools, workspace, dashboard, daemon, or system-health checks, "
            "call the `system_status` tool first, then inspect with focused tools if needed."
        )

    def deferred_tool_names(self, available=None) -> set[str]:
        """Tools shipped name-only this turn (schema withheld until tool_search loads it).
        Config-driven (tools.deferred); activation via tool_search is session-sticky."""
        if not self.config.get("tools.defer_schemas", True):
            return set()
        selectors = self.config.get("tools.deferred", []) or []
        tools = list(available) if available is not None else self.registry.all()
        names = {t.name for t in tools if _matches_deferred_selector(t, selectors)}
        return names - self.activated_tools - {"tool_search"}

    def _deferred_index_block(self) -> str:
        """Stable system-prompt index of deferred tools. Lists ALL configured deferred
        tools (not just inactive ones) so the block never changes mid-session —
        keeping the prompt byte-stable for prefix caching."""
        if not self.config.get("tools.defer_schemas", True):
            return ""
        selectors = self.config.get("tools.deferred", []) or []
        tools = [t for t in self.registry.all() if _matches_deferred_selector(t, selectors)]
        if not tools:
            return ""
        lines = "\n".join(f"- {t.name} — {t.description.splitlines()[0]}"
                          for t in sorted(tools, key=lambda t: t.name))
        return ("# Deferred tools (schemas not loaded)\n"
                "These tools exist but their parameter schemas are not loaded yet. To use one, "
                "first call `tool_search` with its name — that loads the schema; then call the "
                "tool normally:\n" + lines)

    def _build_system_prompt(self) -> str:
        skills_index = self.skills.index_block() if self.skills else ""
        memory_block = self.memory.build_context_block() if self.memory else ""
        runtime = self._build_runtime_block()
        deferred = self._deferred_index_block()
        if deferred:
            runtime = f"{runtime}\n\n{deferred}"
        built = self.context_builder.build_with_metadata(
            skills_index=skills_index,
            memory_block=memory_block,
            runtime_block=runtime,
            platform=getattr(self, "platform", None),
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
        self._last_prompt_metadata = built.metadata()
        return built.text

    def _subagent_role_prompt_part(self) -> PromptPart | None:
        role_prompt = str(self.session.meta.get("subagent_role_prompt") or "").strip()
        if not role_prompt:
            return None
        agent_type = str(self.session.meta.get("agent_type") or "subagent").strip() or "subagent"
        return PromptPart(
            "stable",
            f"subagent_role:{agent_type}",
            f"# Subagent role ({agent_type})\n{role_prompt}",
        )

    def ensure_system_prompt(self, force: bool = False) -> None:
        prompt = self._build_system_prompt()
        msgs = self.session.messages
        if msgs and msgs[0].role == "system":
            if force:
                msgs[0] = Message.system(prompt)
                self._record_prompt_metadata(prompt)
            else:
                self._record_prompt_metadata(msgs[0].content, current_build=False)
        else:
            msgs.insert(0, Message.system(prompt))
            self._record_prompt_metadata(prompt)

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
        meta = getattr(self, "_last_prompt_metadata", None)
        parts = []
        if current_build and isinstance(meta, dict) and meta.get("hash") == actual["hash"]:
            parts = meta.get("parts", []) or []
        elif self.session.meta.get("system_prompt_hash") == actual["hash"]:
            parts = self.session.meta.get("prompt_parts", []) or []
        self.session.meta["system_prompt_hash"] = actual["hash"]
        self.session.meta["system_prompt_chars"] = actual["chars"]
        self.session.meta["system_prompt_tokens"] = actual["tokens"]
        self.session.meta["prompt_parts"] = parts

    def refresh_volatile(self) -> None:
        if self.memory:
            self.memory.refresh_snapshot()
        self.ensure_system_prompt(force=True)

    def switch_session(self, new_session: Session) -> None:
        """Move this agent to ``new_session`` and fire the memory session-switch hook
        (resume, compaction split, /new). Keeps tool_context in sync."""
        old_id = getattr(self.session, "id", "")
        self.session = new_session
        self.tool_context.session = new_session
        if self.memory and getattr(new_session, "id", "") != old_id:
            try:
                self.memory.on_session_switch(old_id, new_session.id)
            except Exception:  # noqa: BLE001
                pass

    def end_session(self) -> None:
        """Fire the memory session-end hook (process exit, agent teardown, /new)."""
        if self.memory:
            try:
                self.memory.on_session_end(self.session.messages)
            except Exception:  # noqa: BLE001
                pass

    # -- run ----------------------------------------------------------------
    def _apply_routing(self, text: str) -> None:
        """Per-prompt provider routing: swap provider/model when a rule matches."""
        import re
        rules = self.config.get("routing", []) or []
        for rule in rules:
            try:
                if re.search(rule.get("match", ""), text, re.I):
                    from ..providers.fallback import build_with_fallbacks
                    self.provider = build_with_fallbacks(
                        self.config, model=rule.get("model"), name=rule.get("provider"))
                    return
            except (re.error, Exception):  # noqa: BLE001
                continue

    def cancel(self) -> None:
        """Request the current run to stop at the next safe point (interrupt-aware loop)."""
        self.cancel_event.set()
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

    def run(self, user_input: str | Message, on_event: OnEvent | None = None) -> Message:
        self.cancel_event.clear()
        self._compact_stuck = False        # reset the no-progress-compaction guard each turn
        self._overflow_retried = False     # one-shot context_overflow -> compress guard, per turn
        self._strip_thinking = False       # one-shot thinking-signature 400 -> resend w/o blocks
        self._retrieved_memory_for_turn = ""
        self._retrieved_memory_user_content = ""
        if not self.session.messages:      # first turn of a session
            from ..plugins import fire_hook
            fire_hook("on_session_start", self)
        msg = user_input if isinstance(user_input, Message) else Message.user(user_input)
        self._apply_routing(msg.content)
        self.session.maybe_title_from(msg.content)
        try:                               # background work that finished since the last turn
            from .wakeups import wakeup_block
            wb = wakeup_block()
            if wb:
                msg.content = f"{wb}\n\n{msg.content}"
        except Exception:  # noqa: BLE001
            pass
        if self.memory:                    # provider prefetch relevant to THIS turn (volatile)
            try:
                fetched = self.memory.prefetch(msg.content)
                if fetched:
                    self._retrieved_memory_for_turn = fetched
                    self._retrieved_memory_user_content = msg.content
                self.memory.queue_prefetch(msg.content)   # warm the next turn in the background
            except Exception:  # noqa: BLE001
                pass
        self.session.messages.append(msg)
        self.tool_context.emit = on_event
        try:
            from ..hooks import run_hooks
            run_hooks(self.config, "user_prompt", {"text": msg.content[:300], "session_id": self.session.id})
        except Exception:  # noqa: BLE001
            pass
        if self.memory:
            self.memory.history.append("user", msg.content, self.session.id)

        before = (
            self.budget.usage.input_tokens,
            self.budget.usage.output_tokens,
            self.budget.usage.cache_read,
            self.budget.usage.cache_write,
        )
        tools_before = self.tools_used
        result = run_conversation(self, on_event)
        tools_this_turn = self.tools_used - tools_before
        self._update_runtime_meta(tools_this_turn)

        # Log this turn's token usage (for `aegis cost` / insights).
        try:
            from ..types import Usage
            from .. import usage_log
            turn = Usage(self.budget.usage.input_tokens - before[0],
                         self.budget.usage.output_tokens - before[1],
                         self.budget.usage.cache_read - before[2],
                         self.budget.usage.cache_write - before[3])
            usage_log.log(self.provider.name, self.provider.model, turn)
        except Exception:  # noqa: BLE001
            pass

        if self.memory and result.content:
            self.memory.history.append("assistant", result.content, self.session.id)
            self.memory.sync_turn(self.session.messages)   # fan out to the provider, fail-soft
        if self.store:
            try:
                self.store.save(self.session)
            except Exception:  # noqa: BLE001  (a save failure must not lose the turn's reply)
                from .._log import log_exc
                log_exc("final session save failed")
        try:
            from .. import trajectory
            from . import review
            review.maybe_review(self, tools_this_turn)   # forked self-improvement
            trajectory.capture_turn(self.config, self.session)
        except Exception:  # noqa: BLE001
            pass
        return result

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
        })
        runtime.update({k: v for k, v in controls.items()
                        if k in {"reasoning_effort", "reasoning_display", "busy_mode"}})
        self.session.meta["runtime"] = runtime
        self.session.meta["usage"] = {
            "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
            "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
            "cache_read": int(getattr(usage, "cache_read", 0) or 0),
            "cache_write": int(getattr(usage, "cache_write", 0) or 0),
        }
        if trace_ctx:
            self.session.meta["trace_id"] = str(trace_ctx.get("trace_id", ""))
            self.session.meta["turn_id"] = str(trace_ctx.get("turn_id", ""))
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
