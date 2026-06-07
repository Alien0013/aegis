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
    def _build_system_prompt(self) -> str:
        skills_index = self.skills.index_block() if self.skills else ""
        memory_block = self.memory.build_context_block() if self.memory else ""
        return self.context_builder.build(skills_index=skills_index, memory_block=memory_block)

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
    def run(self, user_input: str | Message, on_event: OnEvent | None = None) -> Message:
        msg = user_input if isinstance(user_input, Message) else Message.user(user_input)
        self.session.maybe_title_from(msg.content)
        self.session.messages.append(msg)
        self.tool_context.emit = on_event
        if self.memory:
            self.memory.history.append("user", msg.content, self.session.id)

        result = run_conversation(self, on_event)

        if self.memory and result.content:
            self.memory.history.append("assistant", result.content, self.session.id)
        if self.store:
            self.store.save(self.session)
        return result
