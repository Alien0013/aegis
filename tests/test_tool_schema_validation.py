"""Tool schema validator: raw registry schemas are checked before provider adapters mutate them."""

from __future__ import annotations

from typing import Any

from aegis.tools.base import Tool
from aegis.tools.registry import ToolRegistry, default_registry
from aegis.tools.schema_validation import validate_tool_registry, validate_tool_schema


class _SyntheticTool(Tool):
    name = "synthetic"
    description = "Synthetic tool for schema validation tests."
    parameters: dict[str, Any] = {"type": "object", "properties": {}}


def test_builtin_tool_schemas_validate_cleanly():
    result = validate_tool_registry(default_registry(include_plugins=False).all())

    assert result.ok is True
    assert result.total >= 40
    assert result.invalid == 0
    assert result.issues == []


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
