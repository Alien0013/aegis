"""Tool ABC, execution context, and result type."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..types import ToolSchema


@dataclass
class ToolResult:
    content: str
    is_error: bool = False
    # short one-line summary for the UI (defaults to first line of content)
    display: str | None = None
    data: Any = None

    @property
    def summary(self) -> str:
        if self.display:
            return self.display
        first = self.content.strip().splitlines()[0] if self.content.strip() else ""
        return first[:120]

    @property
    def classification(self) -> str:
        """success | error | refused | truncated | partial — a learning signal."""
        c = (self.content or "").lower()
        if self.is_error:
            if any(w in c for w in ("permission denied", "rejected", "blocked", "not authorized",
                                    "refused")):
                return "refused"
            return "error"
        if "[truncated]" in c or "…[truncated]" in c or "…[truncated]…" in self.content:
            return "truncated"
        if not self.content.strip() or "(no output)" in c:
            return "partial"
        return "success"

    @classmethod
    def ok(cls, content: str, display: str | None = None, data: Any = None) -> "ToolResult":
        return cls(content=content, display=display, data=data)

    @classmethod
    def error(cls, message: str) -> "ToolResult":
        return cls(content=message, is_error=True, display=f"error: {message[:100]}")


@dataclass
class ToolContext:
    """Everything a tool may need at run time. Loosely typed to avoid cycles."""

    cwd: Path = field(default_factory=Path.cwd)
    config: Any = None
    memory: Any = None          # MemoryManager
    skills: Any = None          # SkillsLoader
    session: Any = None         # Session
    agent: Any = None           # Agent (for subagent spawn)
    task_id: str = ""           # stable execution-environment id for this run/task
    fs: Any = None              # optional filesystem delegate (ACP: read/write via the editor)
    # callback(prompt:str)->bool used when a permission decision needs the user
    approver: Callable[[str], bool] | None = None
    # callback(question:str, choices:list[str])->str for the clarify tool (CLI prompts inline,
    # other surfaces may leave it None — clarify then returns the question for the next turn)
    asker: Callable[[str, list[str]], str] | None = None
    # callback(var_name, prompt, metadata)->dict for local hidden secret capture.
    # The secret value must not be returned to the model or transcript.
    secret_capture: Callable[[str, str, dict[str, Any] | None], dict[str, Any]] | None = None
    # callback(event:dict) for streaming tool events to the UI
    emit: Callable[[dict], None] | None = None

    def emit_event(self, **event: Any) -> None:
        if self.emit:
            self.emit(event)

    def dispatch_tool(self, name: str, arguments: dict[str, Any] | None = None, *,
                      registry: Any = None, permissions: Any = None) -> ToolResult:
        """Run another registered tool through the normal permission/middleware path.

        Plugin tools use this as their Hermes-style ``ctx.dispatch_tool`` helper. It
        intentionally reuses the current agent's registry and permission engine when
        available so plugin dispatch cannot bypass the harness' tool policy.
        """
        from ..types import ToolCall, new_id
        from .permissions import PermissionEngine
        from .registry import default_registry

        agent = self.agent
        reg = registry or getattr(agent, "registry", None) or default_registry()
        perms = permissions or getattr(agent, "permissions", None) or PermissionEngine(self.config)
        call = ToolCall(new_id("plugin_tool"), str(name or ""), dict(arguments or {}))

        def emit(event: dict[str, Any]) -> None:
            self.emit_event(**event)

        from ..agent.loop import ToolExecutor

        return ToolExecutor(reg, perms, self, emit).execute_one_raw(call)

    def inject_message(self, role: str, content: str, *, metadata: dict[str, Any] | None = None,
                       persist: bool = True) -> dict[str, Any]:
        """Append a message to the active session and optionally persist it.

        This gives plugins a small, explicit equivalent of Hermes' message-injection
        hook without exposing session internals as a stable API surface.
        """
        from ..types import Message

        clean_role = str(role or "user").strip().lower()
        if clean_role == "assistant":
            message = Message.assistant(str(content or ""))
        elif clean_role == "system":
            message = Message.system(str(content or ""))
        elif clean_role == "tool":
            message = Message.tool("plugin_inject", "plugin", str(content or ""))
        else:
            clean_role = "user"
            message = Message.user(str(content or ""))
        if metadata:
            message.meta.update(dict(metadata))
        session = self.session
        if session is None:
            return message.to_dict()
        session.messages.append(message)
        if persist:
            store = getattr(self.agent, "store", None)
            save = getattr(store, "save", None)
            if callable(save):
                save(session)
        self.emit_event(type="plugin_message_injected", role=clean_role, length=len(message.content or ""))
        return message.to_dict()

    def llm(self, prompt: str | list[Any], *, system: str = "", model: str | None = None,
            reasoning: str | None = None, stream: bool = False,
            on_delta: Callable[[str], None] | None = None):
        """Call the configured provider from trusted plugin code.

        Returns the normalized ``LLMResponse`` so plugins can inspect text, tool calls,
        usage, and provider raw data when present.
        """
        from ..providers.fallback import build_with_fallbacks
        from ..types import Message

        provider = getattr(self.agent, "provider", None) or build_with_fallbacks(self.config, model=model)
        if isinstance(prompt, list):
            messages = list(prompt)
        else:
            messages = []
            if system:
                messages.append(Message.system(system))
            messages.append(Message.user(str(prompt or "")))
        effort = reasoning if reasoning is not None else getattr(self.agent, "reasoning", "off")
        return provider.complete(
            messages,
            tools=None,
            stream=stream,
            on_delta=on_delta,
            model=model,
            reasoning=effort,
            cwd=self.cwd,
        )


class Tool:
    """Base class. Subclasses set name/description/parameters and implement run()."""

    name: str = ""
    description: str = ""
    parameters: dict[str, Any] = {"type": "object", "properties": {}}
    # danger groups gate permissions: "fs", "runtime", "network", "automation"
    groups: list[str] = []
    # which toolset this belongs to (enabled via config.tools.toolsets)
    toolset: str = "core"

    def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:  # pragma: no cover
        raise NotImplementedError

    def available(self) -> tuple[bool, str]:
        """Whether the tool is usable in this environment. Override for dep-gated tools so
        the model is never offered a tool it can't run. Returns (ok, reason-if-not)."""
        return True, ""

    def schema(self) -> ToolSchema:
        return {
            "name": self.name,
            "description": self.description.strip(),
            "parameters": self.parameters,
        }
