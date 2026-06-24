"""Tool registry: registration, toolset gating, schema generation."""

from __future__ import annotations

import logging

from ..types import ToolSchema
from .base import Tool

logger = logging.getLogger(__name__)


class ToolRegistry:
    def __init__(self, *, enforce_schema: bool = False):
        self._tools: dict[str, Tool] = {}
        self.enforce_schema = enforce_schema
        self._rejections: list[dict[str, object]] = []

    def _reject(self, tool: Tool, reason: str, *, issues: list[dict] | None = None) -> None:
        record = {
            "tool": str(getattr(tool, "name", "") or "<unnamed>"),
            "source": str(getattr(tool, "source", "") or getattr(tool, "toolset", "") or "tool"),
            "toolset": str(getattr(tool, "toolset", "") or "core"),
            "reason": reason,
            "issues": issues or [],
        }
        self._rejections.append(record)
        logger.warning("Tool registration rejected: %s (%s)", record["tool"], reason)

    def register(self, tool: Tool) -> None:
        if not tool.name:
            raise ValueError("Tool must have a name")
        if self.enforce_schema:
            from .schema_validation import validate_tool_schema

            issues = validate_tool_schema(tool)
            errors = [issue for issue in issues if issue.severity == "error"]
            if errors:
                self._reject(
                    tool,
                    "invalid schema",
                    issues=[issue.to_dict() for issue in issues],
                )
                return
        existing = self._tools.get(tool.name)
        if existing is not None:
            allow_shadow = bool(getattr(tool, "allow_shadow", False))
            if not allow_shadow:
                existing_source = str(getattr(existing, "source", "") or "")
                self._reject(
                    tool,
                    (
                        f"duplicate name shadows existing {existing_source or 'tool'} "
                        f"from toolset '{existing.toolset}'"
                    ),
                )
                return
        self._tools[tool.name] = tool

    def register_all(self, tools: list[Tool]) -> None:
        for t in tools:
            self.register(t)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def rejections(self) -> list[dict[str, object]]:
        return list(self._rejections)

    def available(self, toolsets: list[str], *, only_usable: bool = True,
                  disabled: list[str] | set[str] | None = None) -> list[Tool]:
        """Tools in the enabled toolsets. With ``only_usable`` (default) also drops tools
        whose environment deps are missing, so the model never sees a tool it can't run.
        ``disabled`` is a per-tool denylist (config ``tools.disabled``) that hides individual
        tools even when their toolset is active — the dashboard's per-tool on/off switch."""
        enabled = set(toolsets) or {"core"}
        deny = set(disabled or ())
        out = []
        for t in self._tools.values():
            if t.name in deny:
                continue
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
    from .ui_verify import web_verify_tools
    from .builtin import all_builtin_tools
    from .code_exec import code_tools
    from .extra_builtin import extra_tools
    from .cloud import cloud_tools
    from .devtools import dev_tools
    from .lsp import lsp_tools
    from .process import process_tools
    from .kanban_tool import kanban_tools
    from .code_search_tool import code_search_tools
    from .recall import recall_tools
    from .repomap_tool import repomap_tools
    from .skill_manage import skill_manage_tools
    from .state import state_tools
    from .voice import voice_tools

    reg = ToolRegistry(enforce_schema=True)
    reg.register_all(all_builtin_tools())
    reg.register_all(extra_tools())
    reg.register_all(aux_tools())
    reg.register_all(agentic_tools())
    reg.register_all(code_tools())
    reg.register_all(browser_tools())
    reg.register_all(web_verify_tools())
    reg.register_all(voice_tools())
    reg.register_all(lsp_tools())
    reg.register_all(recall_tools())
    reg.register_all(repomap_tools())
    reg.register_all(code_search_tools())
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
