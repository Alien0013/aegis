"""Tool registry: registration, toolset gating, schema generation."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from ..types import ToolSchema
from .base import Tool, ToolContext, ToolResult

logger = logging.getLogger(__name__)


class ToolAlias(Tool):
    """Hermes-compatible tool name that delegates to an existing AEGIS tool."""

    source = "alias"
    manifest_id = "hermes-compat"

    def __init__(
        self,
        name: str,
        target: Tool,
        *,
        description: str = "",
        parameters: dict[str, Any] | None = None,
        transform: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> None:
        self.name = name
        self.target = target
        self.description = description or f"Compatibility alias for `{target.name}`."
        self.parameters = parameters if parameters is not None else dict(target.parameters)
        self.transform = transform or (lambda args: dict(args or {}))
        self.groups = list(getattr(target, "groups", []) or [])
        self.toolset = str(getattr(target, "toolset", "") or "core")
        self.source_path = f"alias://{name}->{target.name}"
        self.required_env = list(getattr(target, "required_env", []) or [])
        self.required_auth = list(getattr(target, "required_auth", []) or [])
        self.output_limits = dict(getattr(target, "output_limits", {}) or {})
        self.risk_level = str(getattr(target, "risk_level", "") or "")

    def available(self) -> tuple[bool, str]:
        return self.target.available()

    def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        return self.target.run(self.transform(dict(args or {})), ctx)


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
            target = getattr(t, "target", None)
            if target is not None and getattr(target, "name", None) in deny:
                continue
            if not (t.toolset in enabled or "all" in enabled):
                continue
            if only_usable and not t.available()[0]:
                continue
            out.append(t)
        return out

    def schemas(self, tools: list[Tool]) -> list[ToolSchema]:
        return [t.schema() for t in tools]


def _object_schema(
    properties: dict[str, Any] | None = None,
    *,
    required: list[str] | None = None,
) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object", "properties": dict(properties or {})}
    if required:
        schema["required"] = list(required)
    return schema


def _with_action(action: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def transform(args: dict[str, Any]) -> dict[str, Any]:
        out = dict(args)
        out["action"] = action
        return out

    return transform


def _register_alias(reg: ToolRegistry, alias: str, target: str, **kwargs: Any) -> None:
    tool = reg.get(target)
    if tool is None:
        return
    reg.register(ToolAlias(alias, tool, **kwargs))


def _register_hermes_aliases(reg: ToolRegistry) -> None:
    """Expose Hermes/Codex-familiar tool names without duplicating tool logic."""
    direct = {
        "terminal": "bash",
        "patch": "apply_patch",
        "search_files": "search",
        "x_search": "web_search",
        "delegate_task": "spawn_subagent",
        "todo": "todo_write",
        "image_generate": "generate_image",
        "text_to_speech": "speak",
        "speech_to_text": "transcribe",
        "audio_transcribe": "transcribe",
        "computer_use": "computer",
    }
    for alias, target in direct.items():
        _register_alias(reg, alias, target)

    _register_alias(
        reg,
        "audio_analyze",
        "media_analyze",
        description="Analyze an audio file by transcribing it with the configured STT provider.",
        parameters=_object_schema(
            {
                "path": {"type": "string", "description": "Local audio file path."},
                "prompt": {"type": "string"},
                "model": {"type": "string"},
            },
            required=["path"],
        ),
        transform=lambda args: {**args, "media_type": "audio"},
    )
    _register_alias(
        reg,
        "video_analyze",
        "media_analyze",
        description="Analyze a video by sampling frames with ffmpeg and using the vision model.",
        parameters=_object_schema(
            {
                "path": {"type": "string", "description": "Local video file path."},
                "prompt": {"type": "string"},
                "max_frames": {"type": "integer"},
            },
            required=["path"],
        ),
        transform=lambda args: {**args, "media_type": "video"},
    )

    _register_alias(
        reg,
        "read_terminal",
        "process",
        description="Read output from a long-running terminal/process session.",
        parameters=_object_schema(
            {
                "id": {"type": "string", "description": "Process/session id."},
                "session_id": {"type": "string", "description": "Process/session id alias."},
                "offset": {"type": "integer"},
                "limit": {"type": "integer"},
            }
        ),
        transform=_with_action("logs"),
    )

    _register_alias(
        reg,
        "skills_list",
        "skill",
        description="List available skills.",
        parameters=_object_schema(),
        transform=lambda _args: {"action": "list"},
    )
    _register_alias(
        reg,
        "skill_view",
        "skill",
        description="Load the full body for one skill.",
        parameters=_object_schema(
            {
                "name": {"type": "string", "description": "Skill name."},
                "skill": {"type": "string", "description": "Skill name alias."},
            }
        ),
        transform=lambda args: {"action": "view", "name": args.get("name") or args.get("skill") or ""},
    )

    browser_actions = {
        "browser_navigate": (
            "navigate",
            {"url": {"type": "string"}},
            ["url"],
            "Navigate the browser to a URL.",
        ),
        "browser_open": (
            "navigate",
            {"url": {"type": "string"}},
            ["url"],
            "Open a URL in the browser.",
        ),
        "browser_goto": (
            "navigate",
            {"url": {"type": "string"}},
            ["url"],
            "Go to a URL in the browser.",
        ),
        "browser_click": (
            "click",
            {"selector": {"type": "string"}},
            ["selector"],
            "Click an element by selector.",
        ),
        "browser_type": (
            "type",
            {"selector": {"type": "string"}, "text": {"type": "string"}},
            ["selector", "text"],
            "Type text into an element by selector.",
        ),
        "browser_fill": (
            "type",
            {"selector": {"type": "string"}, "text": {"type": "string"}},
            ["selector", "text"],
            "Fill an element by selector.",
        ),
        "browser_text": ("text", {}, [], "Return readable page text."),
        "browser_read": ("text", {}, [], "Read readable page text."),
        "browser_get_text": ("text", {}, [], "Get readable page text."),
        "browser_snapshot": ("text", {}, [], "Return a compact textual browser snapshot."),
        "browser_html": ("html", {}, [], "Return page HTML."),
        "browser_content": ("html", {}, [], "Return page HTML content."),
        "browser_source": ("html", {}, [], "Return page source HTML."),
        "browser_screenshot": (
            "screenshot",
            {"path": {"type": "string"}},
            [],
            "Save a browser screenshot.",
        ),
        "browser_capture": (
            "screenshot",
            {"path": {"type": "string"}},
            [],
            "Capture a browser screenshot.",
        ),
        "browser_back": ("back", {}, [], "Go back in browser history."),
        "browser_go_back": ("back", {}, [], "Go back in browser history."),
        # AEGIS keeps scroll/key/dialog/CDP controls in the computer/devtools layer.
        # These aliases expose the familiar browser names and return the browser
        # state that is safe to collect without adding new destructive controls.
        "browser_scroll": ("text", {}, [], "Read page text after external/browser scrolling."),
        "browser_press": ("text", {}, [], "Read page text after external/browser key input."),
        "browser_console": ("html", {}, [], "Return page HTML for console/context inspection."),
        "browser_get_images": ("html", {}, [], "Return page HTML so image URLs can be extracted."),
        "browser_vision": ("screenshot", {"path": {"type": "string"}}, [], "Capture a screenshot for vision analysis."),
        "browser_cdp": ("html", {}, [], "Return page HTML from the connected browser context."),
        "browser_dialog": ("text", {}, [], "Return page text after dialog handling by the browser backend."),
    }
    for alias, (action, props, required, description) in browser_actions.items():
        _register_alias(
            reg,
            alias,
            "browser",
            description=description,
            parameters=_object_schema(props, required=required),
            transform=_with_action(action),
        )

    kanban_actions = {
        "kanban_list": (
            "list",
            {"filter_status": {"type": "string"}},
            [],
            "List kanban cards.",
        ),
        "kanban_create": (
            "create",
            {
                "title": {"type": "string"},
                "body": {"type": "string"},
                "priority": {"type": "integer"},
                "assignee": {"type": "string"},
                "parents": {"type": "array", "items": {"type": "string"}},
                "tenant": {"type": "string"},
                "workspace": {"type": "string"},
            },
            ["title"],
            "Create a kanban card.",
        ),
        "kanban_add": (
            "create",
            {
                "title": {"type": "string"},
                "body": {"type": "string"},
                "priority": {"type": "integer"},
                "assignee": {"type": "string"},
                "parents": {"type": "array", "items": {"type": "string"}},
                "tenant": {"type": "string"},
                "workspace": {"type": "string"},
            },
            ["title"],
            "Add a kanban card.",
        ),
        "kanban_show": ("show", {"id": {"type": "string"}}, ["id"], "Show a kanban card."),
        "kanban_get": ("show", {"id": {"type": "string"}}, ["id"], "Get a kanban card."),
        "kanban_view": ("show", {"id": {"type": "string"}}, ["id"], "View a kanban card."),
        "kanban_move": (
            "move",
            {"id": {"type": "string"}, "status": {"type": "string"}},
            ["id", "status"],
            "Move a kanban card to another status.",
        ),
        "kanban_update_status": (
            "move",
            {"id": {"type": "string"}, "status": {"type": "string"}},
            ["id", "status"],
            "Update a kanban card status.",
        ),
        "kanban_set_status": (
            "move",
            {"id": {"type": "string"}, "status": {"type": "string"}},
            ["id", "status"],
            "Set a kanban card status.",
        ),
        "kanban_complete": (
            "complete",
            {
                "id": {"type": "string"},
                "text": {"type": "string"},
                "metadata": {"type": "object"},
                "created_cards": {"type": "array", "items": {"type": "string"}},
            },
            ["id"],
            "Mark a kanban card complete.",
        ),
        "kanban_done": (
            "complete",
            {
                "id": {"type": "string"},
                "text": {"type": "string"},
                "metadata": {"type": "object"},
                "created_cards": {"type": "array", "items": {"type": "string"}},
            },
            ["id"],
            "Mark a kanban card done.",
        ),
        "kanban_finish": (
            "complete",
            {
                "id": {"type": "string"},
                "text": {"type": "string"},
                "metadata": {"type": "object"},
                "created_cards": {"type": "array", "items": {"type": "string"}},
            },
            ["id"],
            "Finish a kanban card.",
        ),
        "kanban_block": (
            "block",
            {"id": {"type": "string"}, "text": {"type": "string"}},
            ["id"],
            "Block a kanban card.",
        ),
        "kanban_unblock": ("unblock", {"id": {"type": "string"}}, ["id"], "Unblock a kanban card."),
        "kanban_comment": (
            "comment",
            {"id": {"type": "string"}, "text": {"type": "string"}},
            ["id", "text"],
            "Comment on a kanban card.",
        ),
        "kanban_note": (
            "comment",
            {"id": {"type": "string"}, "text": {"type": "string"}},
            ["id", "text"],
            "Add a note to a kanban card.",
        ),
        "kanban_heartbeat": (
            "heartbeat",
            {"id": {"type": "string"}, "text": {"type": "string"}},
            ["id"],
            "Record kanban worker heartbeat.",
        ),
        "kanban_link": (
            "link",
            {"parent": {"type": "string"}, "child": {"type": "string"}},
            ["parent", "child"],
            "Link a parent and child kanban card.",
        ),
        "kanban_depend": (
            "link",
            {"parent": {"type": "string"}, "child": {"type": "string"}},
            ["parent", "child"],
            "Link a dependency between kanban cards.",
        ),
        "kanban_runs": ("runs", {"id": {"type": "string"}}, ["id"], "List runs for a kanban card."),
    }
    for alias, (action, props, required, description) in kanban_actions.items():
        _register_alias(
            reg,
            alias,
            "kanban",
            description=description,
            parameters=_object_schema(props, required=required),
            transform=_with_action(action),
        )


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
    _register_hermes_aliases(reg)
    if include_plugins:
        try:
            from ..plugins import load_plugins
            reg.register_all(load_plugins(quiet=True).tools)
        except Exception:  # noqa: BLE001
            pass
    return reg
