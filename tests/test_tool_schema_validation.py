"""Tool schema validator: raw registry schemas are checked before provider adapters mutate them."""

from __future__ import annotations

from typing import Any

from aegis.tools.base import Tool, ToolContext, ToolResult
from aegis.tools.registry import ToolRegistry, default_registry
from aegis.tools.schema_validation import validate_tool_registry, validate_tool_schema


class _SyntheticTool(Tool):
    name = "synthetic"
    description = "Synthetic tool for schema validation tests."
    parameters: dict[str, Any] = {"type": "object", "properties": {}}


def test_builtin_tool_schemas_validate_cleanly():
    result = validate_tool_registry(default_registry(include_plugins=False).all())

    assert result.ok is True
    assert result.total >= 95
    assert result.invalid == 0
    assert result.issues == []


def test_hermes_tool_aliases_are_registered_with_provenance():
    reg = default_registry(include_plugins=False)
    aliases = {
        "terminal": "bash",
        "patch": "apply_patch",
        "search_files": "search",
        "x_search": "web_search",
        "delegate_task": "spawn_subagent",
        "todo": "todo_write",
        "image_generate": "generate_image",
        "text_to_speech": "speak",
        "speech_to_text": "transcribe",
        "computer_use": "computer",
        "audio_analyze": "media_analyze",
        "video_analyze": "media_analyze",
        "read_terminal": "process",
        "skills_list": "skill",
        "skill_view": "skill",
        "browser_navigate": "browser",
        "browser_open": "browser",
        "browser_fill": "browser",
        "browser_read": "browser",
        "browser_snapshot": "browser",
        "browser_click": "browser",
        "browser_type": "browser",
        "browser_screenshot": "browser",
        "browser_scroll": "browser",
        "browser_press": "browser",
        "browser_console": "browser",
        "browser_get_images": "browser",
        "browser_vision": "browser",
        "browser_cdp": "browser",
        "browser_dialog": "browser",
        "kanban_create": "kanban",
        "kanban_add": "kanban",
        "kanban_set_status": "kanban",
        "kanban_complete": "kanban",
        "kanban_done": "kanban",
        "kanban_comment": "kanban",
        "kanban_note": "kanban",
    }

    for alias, target in aliases.items():
        tool = reg.get(alias)
        assert tool is not None, alias
        meta = tool.metadata()
        assert meta["source"] == "alias"
        assert meta["manifest_id"] == "hermes-compat"
        assert meta["source_path"] == f"alias://{alias}->{target}"


def test_action_style_tool_aliases_rewrite_arguments(monkeypatch):
    reg = default_registry(include_plugins=False)
    ctx = ToolContext()

    browser_alias = reg.get("browser_navigate")
    assert browser_alias is not None
    browser_seen: dict[str, Any] = {}

    def fake_browser(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        browser_seen.update(args)
        return ToolResult.ok("ok")

    monkeypatch.setattr(browser_alias.target, "run", fake_browser)
    assert browser_alias.run({"url": "https://example.test"}, ctx).is_error is False
    assert browser_seen == {"url": "https://example.test", "action": "navigate"}

    kanban_alias = reg.get("kanban_complete")
    assert kanban_alias is not None
    kanban_seen: dict[str, Any] = {}

    def fake_kanban(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        kanban_seen.update(args)
        return ToolResult.ok("ok")

    monkeypatch.setattr(kanban_alias.target, "run", fake_kanban)
    assert kanban_alias.run({"id": "K-1", "text": "done"}, ctx).is_error is False
    assert kanban_seen == {"id": "K-1", "text": "done", "action": "complete"}

    skill_alias = reg.get("skill_view")
    assert skill_alias is not None
    skill_seen: dict[str, Any] = {}

    def fake_skill(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        skill_seen.update(args)
        return ToolResult.ok("ok")

    monkeypatch.setattr(skill_alias.target, "run", fake_skill)
    assert skill_alias.run({"skill": "python"}, ctx).is_error is False
    assert skill_seen == {"action": "view", "name": "python"}


def test_patch_alias_uses_apply_patch_edit_scope():
    from aegis.agent.loop import ToolExecutor
    from aegis.types import ToolCall

    call = ToolCall(
        "call_test",
        "patch",
        {"patch": "--- a/example.txt\n+++ b/example.txt\n@@\n-old\n+new\n"},
    )

    assert ToolExecutor._edit_paths(call) == ["example.txt"]


def test_audio_analyze_alias_delegates_to_media_tool(monkeypatch, tmp_path):
    from aegis.tools.voice import TranscribeTool

    media = tmp_path / "sample.wav"
    media.write_bytes(b"RIFF")
    reg = default_registry(include_plugins=False)
    alias = reg.get("audio_analyze")
    assert alias is not None

    seen: dict[str, Any] = {}

    def fake_transcribe(_self, args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        seen.update(args)
        return ToolResult.ok("hello audio")

    monkeypatch.setattr(TranscribeTool, "run", fake_transcribe)

    result = alias.run({"path": str(media)}, ToolContext())

    assert result.is_error is False
    assert "hello audio" in result.content
    assert seen["path"] == str(media)


def test_schema_validator_reports_required_field_mismatches():
    class BadRequired(_SyntheticTool):
        parameters = {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["missing"],
        }

    issues = validate_tool_schema(BadRequired())

    assert any(issue.path == "parameters.required" and "missing" in issue.message for issue in issues)


def test_schema_validator_reports_bad_local_refs_and_root_shape():
    class BadRef(_SyntheticTool):
        parameters = {
            "type": "object",
            "properties": {"item": {"$ref": "#/$defs/Missing"}},
            "$defs": {"Present": {"type": "string"}},
        }

    class BadRoot(_SyntheticTool):
        name = "bad_root"
        parameters = {"type": "array", "items": {"type": "string"}}

    ref_issues = validate_tool_schema(BadRef())
    root_issues = validate_tool_schema(BadRoot())

    assert any("not found" in issue.message for issue in ref_issues)
    assert any(issue.path == "parameters.type" and "root" in issue.message for issue in root_issues)


def test_registry_rejects_duplicates_unless_explicit_override():
    class First(_SyntheticTool):
        name = "dupe"

    class Shadow(_SyntheticTool):
        name = "dupe"
        description = "Shadow tool."

    class Override(_SyntheticTool):
        name = "dupe"
        description = "Override tool."
        allow_shadow = True

    reg = ToolRegistry(enforce_schema=True)
    first = First()
    reg.register(first)
    reg.register(Shadow())

    assert reg.get("dupe") is first
    assert reg.rejections()[0]["reason"].startswith("duplicate name")

    override = Override()
    reg.register(override)

    assert reg.get("dupe") is override


def test_registry_rejects_invalid_schema_when_enforced():
    class Invalid(_SyntheticTool):
        name = "bad_schema"
        parameters = {"type": "object", "required": ["missing"], "properties": {}}

    reg = ToolRegistry(enforce_schema=True)
    reg.register(Invalid())

    assert reg.get("bad_schema") is None
    assert reg.rejections()[0]["reason"] == "invalid schema"
    assert any(issue["path"] == "parameters.required" for issue in reg.rejections()[0]["issues"])


def test_tool_metadata_includes_mcp_provenance_without_secret_values():
    from aegis.mcp.client import MCPClient, MCPTool

    client = MCPClient(
        "fixture",
        command="server",
        env={"MCP_TOKEN": "secret-token"},
        headers={"Authorization": "Bearer secret-token"},
    )
    tool = MCPTool(client, {
        "name": "remote_search",
        "description": "Remote search.",
        "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}}},
    })

    meta = tool.metadata()

    assert meta["source"] == "mcp"
    assert meta["manifest_id"] == "fixture"
    assert meta["source_path"] == "mcp://fixture/remote_search"
    assert meta["required_env"] == ["MCP_TOKEN"]
    assert meta["required_auth"] == ["headers"]
    assert "secret-token" not in str(meta)
