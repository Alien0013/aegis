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
    # callback(prompt:str)->bool used when a permission decision needs the user
    approver: Callable[[str], bool] | None = None
    # callback(event:dict) for streaming tool events to the UI
    emit: Callable[[dict], None] | None = None

    def emit_event(self, **event: Any) -> None:
        if self.emit:
            self.emit(event)


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

    def schema(self) -> ToolSchema:
        return {
            "name": self.name,
            "description": self.description.strip(),
            "parameters": self.parameters,
        }
