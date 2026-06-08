"""The Agent: wires provider + tools + memory + skills + session into the loop."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..config import Config, Workspace
from ..constants import DEFAULT_MAX_ITERATIONS
from ..memory import MemoryManager
from ..providers import Provider, build_provider
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
    ) -> "Agent":
        from ..providers.fallback import build_with_fallbacks
        provider = build_with_fallbacks(config, model=model, name=provider_name)
        session = session or Session.create()
        agent = cls(config=config, provider=provider, session=session, cwd=cwd,
                    approver=approver, store=store)
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

    def _build_system_prompt(self) -> str:
        skills_index = self.skills.index_block() if self.skills else ""
        memory_block = self.memory.build_context_block() if self.memory else ""
        return self.context_builder.build(
            skills_index=skills_index,
            memory_block=memory_block,
            runtime_block=self._build_runtime_block(),
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

    def run(self, user_input: str | Message, on_event: OnEvent | None = None) -> Message:
        msg = user_input if isinstance(user_input, Message) else Message.user(user_input)
        self._apply_routing(msg.content)
        self.session.maybe_title_from(msg.content)
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
        result = run_conversation(self, on_event)

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
            self.store.save(self.session)
        try:
            from .. import learn, trajectory
            learn.background_tick(self.config, self.session)
            trajectory.capture_turn(self.config, self.session)
        except Exception:  # noqa: BLE001
            pass
        return result
