from __future__ import annotations

from aegis.providers.schema import sanitize, strip_pattern_and_format, strip_slash_enum


def _tool(parameters: dict) -> dict:
    return {"type": "function", "function": {"name": "demo", "parameters": parameters}}


def test_sanitize_deep_copies_and_handles_tool_wrappers():
    original = {
        "name": "demo",
        "description": "Demo tool.",
        "parameters": {
            "type": "object",
            "properties": {"payload": "object"},
        },
    }

    out = sanitize(original)
    openai_out = sanitize(_tool({"type": "object"}))

    assert out is not original
    assert out["parameters"] is not original["parameters"]
    assert original["parameters"]["properties"]["payload"] == "object"
    assert out["parameters"]["properties"]["payload"] == {
        "type": "object",
        "properties": {},
    }
    assert openai_out["function"]["parameters"] == {"type": "object", "properties": {}}


def test_nullable_type_arrays_and_unions_collapse():
    out = sanitize({
        "type": "object",
        "properties": {
            "name": {"type": ["string", "null"]},
            "count": {
                "anyOf": [{"type": "integer"}, {"type": "null"}],
                "default": None,
                "examples": ["3"],
            },
            "payload": {
                "oneOf": [{"$ref": "#/$defs/Payload"}, {"type": "null"}],
                "default": None,
                "examples": [{"q": "README.md"}],
            },
        },
        "$defs": {"Payload": {"type": "object"}},
    })

    assert out["properties"]["name"]["type"] == "string"
    assert out["properties"]["name"]["nullable"] is True
    assert out["properties"]["count"]["type"] == "integer"
    assert out["properties"]["count"]["nullable"] is True
    assert out["properties"]["count"]["default"] is None
    assert out["properties"]["count"]["examples"] == ["3"]
    assert out["properties"]["payload"] == {
        "$ref": "#/$defs/Payload",
        "nullable": True,
        "examples": [{"q": "README.md"}],
    }
    assert out["$defs"]["Payload"] == {"type": "object", "properties": {}}


def test_examples_are_preserved_as_literal_metadata():
    out = sanitize({
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "examples": ["README.md", "docs/guide.md"],
            },
            "payload": {
                "type": "object",
                "properties": {},
                "examples": [{"kind": "demo"}],
            },
        },
    })

    assert out["properties"]["path"]["examples"] == ["README.md", "docs/guide.md"]
    assert out["properties"]["payload"]["examples"] == [{"kind": "demo"}]


def test_annotation_and_validation_hints_are_preserved_like_hermes():
    out = sanitize({
        "type": "object",
        "properties": {
            "count": {
                "type": "integer",
                "exclusiveMinimum": 0,
                "deprecated": True,
            },
            "label": {
                "type": "string",
                "readOnly": True,
                "writeOnly": False,
            },
        },
    })

    assert out["properties"]["count"]["exclusiveMinimum"] == 0
    assert out["properties"]["count"]["deprecated"] is True
    assert out["properties"]["label"]["readOnly"] is True
    assert out["properties"]["label"]["writeOnly"] is False


def test_bad_required_entries_are_pruned_to_declared_properties():
    out = sanitize({
        "type": "object",
        "properties": {
            "kept": {"type": "string"},
            "nested": {
                "type": "object",
                "properties": {"child": {"type": "string"}},
                "required": ["child", "ghost"],
            },
            "empty": {
                "type": "object",
                "properties": {},
                "required": ["ghost"],
            },
        },
        "required": ["kept", "ghost"],
    })

    assert out["required"] == ["kept"]
    assert out["properties"]["nested"]["required"] == ["child"]
    assert "required" not in out["properties"]["empty"]


def test_top_level_combinators_are_stripped_but_nested_ones_survive():
    out = sanitize({
        "type": "object",
        "properties": {
            "config": {
                "type": "object",
                "properties": {"mode": {"type": "string"}},
                "allOf": [{"required": ["mode"]}],
            },
        },
        "required": ["config"],
        "allOf": [{"required": ["config"]}],
        "anyOf": [{"required": ["config"]}],
        "oneOf": [{"required": ["config"]}],
        "enum": ["not-useful-at-root"],
        "not": {"required": ["other"]},
    })

    for key in ("allOf", "anyOf", "oneOf", "enum", "not"):
        assert key not in out
    assert out["properties"]["config"]["allOf"] == [{"required": ["mode"]}]


def test_strip_pattern_and_format_helper_preserves_property_names():
    tools = [_tool({
        "type": "object",
        "properties": {
            "pattern": {"type": "string"},
            "date": {"type": "string", "pattern": "\\d+", "format": "date-time"},
        },
        "required": ["pattern"],
    })]

    result, stripped = strip_pattern_and_format(tools)

    assert result is tools
    assert stripped == 2
    params = tools[0]["function"]["parameters"]
    assert "pattern" in params["properties"]
    assert "pattern" not in params["properties"]["date"]
    assert "format" not in params["properties"]["date"]


def test_strip_slash_enum_helper_only_removes_slash_enums():
    tools = [_tool({
        "type": "object",
        "properties": {
            "model": {"type": "string", "enum": ["owner/model", "local"]},
            "mode": {"type": "string", "enum": ["fast", "slow"]},
        },
    })]

    _, stripped = strip_slash_enum(tools)

    props = tools[0]["function"]["parameters"]["properties"]
    assert stripped == 1
    assert "enum" not in props["model"]
    assert props["mode"]["enum"] == ["fast", "slow"]


def test_mcp_normalization_rewrites_defs_collapses_nullable_and_prunes_required():
    from aegis.mcp.client import _normalize_mcp_input_schema

    original = {
        "type": "object",
        "properties": {
            "payload": {"$ref": "#/definitions/Payload", "default": None},
            "optional": {"type": ["string", "null"]},
            "nested": {
                "type": "object",
                "properties": {"kept": {"type": "string"}},
                "required": ["kept", "ghost"],
            },
        },
        "required": ["payload", "ghost"],
        "definitions": {
            "Payload": {
                "type": "object",
                "properties": {"q": {"type": "string"}},
                "required": ["q", "missing"],
            },
        },
    }

    out = _normalize_mcp_input_schema(original)

    assert "definitions" not in out
    assert out["properties"]["payload"] == {"$ref": "#/$defs/Payload"}
    assert out["properties"]["optional"] == {"type": "string", "nullable": True}
    assert out["required"] == ["payload"]
    assert out["properties"]["nested"]["required"] == ["kept"]
    assert out["$defs"]["Payload"]["required"] == ["q"]
    assert original["properties"]["payload"]["$ref"] == "#/definitions/Payload"
