"""Tool system: base ABC, capability-gated permissions, registry, built-ins."""

from .base import Tool, ToolContext, ToolResult
from .permissions import Decision, ExecMode, PermissionEngine
from .registry import ToolRegistry, default_registry

__all__ = [
    "Tool",
    "ToolContext",
    "ToolResult",
    "Decision",
    "ExecMode",
    "PermissionEngine",
    "ToolRegistry",
    "default_registry",
]
