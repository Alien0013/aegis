"""The Agent: wires provider + tools + memory + skills + session into the loop."""

from __future__ import annotations

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
from .context import ContextBuilder
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
        try:                                   # a custom context engine may expose its own tools
            from .context_engine import get_engine
            for t in get_engine(config).tools():
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

        self.tool_context = ToolContext(
            cwd=self.cwd, config=config, memory=self.memory, skills=self.skills,
            session=self.session, agent=self, approver=approver,
        )

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
    ) -> "Agent":
        from ..providers.fallback import build_with_fallbacks
        provider = build_with_fallbacks(config, model=model, name=provider_name)
        session = session or Session.create()
        agent = cls(config=config, provider=provider, session=session, cwd=cwd,
                    approver=approver, store=store, registry=registry)
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
        conf = set(self.config.get("tools.deferred", []) or [])
        if available is not None:
            conf &= {t.name for t in available}
        return conf - self.activated_tools - {"tool_search"}

    def _deferred_index_block(self) -> str:
        """Stable system-prompt index of deferred tools. Lists ALL configured deferred
        tools (not just inactive ones) so the block never changes mid-session —
        keeping the prompt byte-stable for prefix caching."""
        if not self.config.get("tools.defer_schemas", True):
            return ""
        conf = set(self.config.get("tools.deferred", []) or [])
        tools = [t for t in self.registry.all() if t.name in conf]
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
        return self.context_builder.build(
            skills_index=skills_index,
            memory_block=memory_block,
            runtime_block=runtime,
            platform=getattr(self, "platform", None),
        )

    def ensure_system_prompt(self, force: bool = False) -> None:
        prompt = self._build_system_prompt()
        msgs = self.session.messages
        if msgs and msgs[0].role == "system":
            if force:
                msgs[0] = Message.system(prompt)
        else:
            msgs.insert(0, Message.system(prompt))

    def refresh_volatile(self) -> None:
        if self.memory:
            self.memory.refresh_snapshot()
        self.ensure_system_prompt(force=True)

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
        self.session.messages.append(msg)
        self.tool_context.emit = on_event
        try:
            from ..hooks import run_hooks
            run_hooks(self.config, "user_prompt", {"text": msg.content[:300], "session_id": self.session.id})
        except Exception:  # noqa: BLE001
            pass
        if self.memory:
            self.memory.history.append("user", msg.content, self.session.id)

        before = (self.budget.usage.input_tokens, self.budget.usage.output_tokens)
        tools_before = self.tools_used
        result = run_conversation(self, on_event)
        tools_this_turn = self.tools_used - tools_before

        # Log this turn's token usage (for `aegis cost` / insights).
        try:
            from ..types import Usage
            from .. import usage_log
            turn = Usage(self.budget.usage.input_tokens - before[0],
                         self.budget.usage.output_tokens - before[1],
                         self.budget.usage.cache_read, self.budget.usage.cache_write)
            usage_log.log(self.provider.name, self.provider.model, turn)
        except Exception:  # noqa: BLE001
            pass

        if self.memory and result.content:
            self.memory.history.append("assistant", result.content, self.session.id)
            if self.memory.external:
                try:
                    self.memory.external.sync_turn(self.session.messages)
                except Exception:  # noqa: BLE001
                    from .._log import log_exc
                    log_exc("external memory sync_turn failed")
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
