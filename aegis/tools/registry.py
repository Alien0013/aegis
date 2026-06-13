"""Tool registry: registration, toolset gating, schema generation."""

from __future__ import annotations

from ..types import ToolSchema
from .base import Tool


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if not tool.name:
            raise ValueError("Tool must have a name")
        self._tools[tool.name] = tool

    def register_all(self, tools: list[Tool]) -> None:
        for t in tools:
            self.register(t)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def available(self, toolsets: list[str], *, only_usable: bool = True) -> list[Tool]:
        """Tools in the enabled toolsets. With ``only_usable`` (default) also drops tools
        whose environment deps are missing, so the model never sees a tool it can't run."""
        enabled = set(toolsets) or {"core"}
        out = []
        for t in self._tools.values():
            if not (t.toolset in enabled or "all" in enabled):
                continue
            if only_usable and not t.available()[0]:
                continue
            out.append(t)
        return out

    def schemas(self, tools: list[Tool]) -> list[ToolSchema]:
        return [t.schema() for t in tools]


def default_registry(*, include_plugins: bool = True) -> ToolRegistry:
    """Registry pre-loaded with all built-in tools (+ extended + plugin tools)."""
    from .agentic import agentic_tools
    from .aux_tools import aux_tools
    from .browser import browser_tools
    from .builtin import all_builtin_tools
    from .code_exec import code_tools
    from .extra_builtin import extra_tools
    from .cloud import cloud_tools
    from .devtools import dev_tools
    from .lsp import lsp_tools
    from .process import process_tools
    from .kanban_tool import kanban_tools
    from .recall import recall_tools
    from .skill_manage import skill_manage_tools
    from .state import state_tools
    from .voice import voice_tools

    reg = ToolRegistry()
    reg.register_all(all_builtin_tools())
    reg.register_all(extra_tools())
    reg.register_all(aux_tools())
    reg.register_all(agentic_tools())
    reg.register_all(code_tools())
    reg.register_all(browser_tools())
    reg.register_all(voice_tools())
    reg.register_all(lsp_tools())
    reg.register_all(recall_tools())
    reg.register_all(skill_manage_tools())
    reg.register_all(kanban_tools())
    reg.register_all(state_tools())
    reg.register_all(process_tools())
    reg.register_all(dev_tools())
    reg.register_all(cloud_tools())
    if include_plugins:
        try:
            from ..plugins import load_plugins
            reg.register_all(load_plugins(quiet=True).tools)
        except Exception:  # noqa: BLE001
            pass
    return reg
