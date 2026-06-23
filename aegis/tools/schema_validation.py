"""Validation helpers for model-visible tool schemas.

The provider-facing tool schema contract is intentionally small:
``{"name", "description", "parameters"}``, where ``parameters`` is a JSON
Schema object. This module keeps validation dependency-free so it can run in
doctor/dashboard/CI paths without pulling in a full JSON Schema package.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .base import Tool

_ALLOWED_TYPES = {
    "array",
    "boolean",
    "integer",
    "null",
    "number",
    "object",
    "string",
}
_SCHEMA_KEYS = {
    "$defs",
    "$ref",
    "additionalProperties",
    "anyOf",
    "default",
    "description",
    "enum",
    "format",
    "items",
    "maxItems",
    "maxLength",
    "maximum",
    "minItems",
    "minLength",
    "minimum",
    "oneOf",
    "properties",
    "required",
    "title",
    "type",
}


@dataclass
class ToolSchemaIssue:
    tool: str
    source: str
    path: str
    message: str
    severity: str = "error"

    def to_dict(self) -> dict[str, str]:
        return {
            "tool": self.tool,
            "source": self.source,
            "path": self.path,
            "message": self.message,
            "severity": self.severity,
        }


@dataclass
class ToolSchemaValidation:
    ok: bool
    total: int
    valid: int
    invalid: int
    warnings: int
    issues: list[ToolSchemaIssue] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "total": self.total,
            "valid": self.valid,
            "invalid": self.invalid,
            "warnings": self.warnings,
            "issues": [issue.to_dict() for issue in self.issues],
        }


def _add(
    issues: list[ToolSchemaIssue],
    tool: str,
    source: str,
    path: str,
    message: str,
    severity: str = "error",
) -> None:
    issues.append(ToolSchemaIssue(tool=tool, source=source, path=path, message=message, severity=severity))


def _valid_type(value: Any) -> bool:
    if isinstance(value, str):
        return value in _ALLOWED_TYPES
    if isinstance(value, list) and value:
        return all(isinstance(item, str) and item in _ALLOWED_TYPES for item in value)
    return False


def _unescape_ref_token(value: str) -> str:
    return value.replace("~1", "/").replace("~0", "~")


def _validate_json_schema(
    tool: str,
    source: str,
    schema: Any,
    path: str,
    issues: list[ToolSchemaIssue],
    *,
    root_defs: dict[str, Any] | None = None,
) -> None:
    if not isinstance(schema, dict):
        _add(issues, tool, source, path, "schema node must be an object")
        return
    if root_defs is None and isinstance(schema.get("$defs"), dict):
        root_defs = schema.get("$defs")

    if "type" in schema and not _valid_type(schema.get("type")):
        _add(issues, tool, source, f"{path}.type", "type must be a valid JSON Schema type or non-empty type array")

    ref = schema.get("$ref")
    if ref is not None:
        if not isinstance(ref, str) or not ref:
            _add(issues, tool, source, f"{path}.$ref", "$ref must be a non-empty string")
        elif ref.startswith("#/$defs/"):
            key = _unescape_ref_token(ref.removeprefix("#/$defs/").split("/", 1)[0])
            if not root_defs or key not in root_defs:
                _add(issues, tool, source, f"{path}.$ref", f"local $ref target not found: {ref}")
        elif ref.startswith("#/"):
            _add(issues, tool, source, f"{path}.$ref", "only local #/$defs/... references are supported", severity="warning")

    unknown = sorted(str(key) for key in schema if str(key) not in _SCHEMA_KEYS)
    for key in unknown:
        _add(issues, tool, source, f"{path}.{key}", "unknown JSON Schema keyword", severity="warning")

    props = schema.get("properties")
    if props is not None:
        if not isinstance(props, dict):
            _add(issues, tool, source, f"{path}.properties", "properties must be an object")
        else:
            for name, child in props.items():
                if not isinstance(name, str) or not name:
                    _add(issues, tool, source, f"{path}.properties", "property names must be non-empty strings")
                    continue
                _validate_json_schema(tool, source, child, f"{path}.properties.{name}", issues, root_defs=root_defs)

    required = schema.get("required")
    if required is not None:
        if not isinstance(required, list) or not all(isinstance(item, str) for item in required):
            _add(issues, tool, source, f"{path}.required", "required must be a string array")
        elif isinstance(props, dict):
            missing = sorted(item for item in required if item not in props)
            for item in missing:
                _add(
                    issues,
                    tool,
                    source,
                    f"{path}.required",
                    f"required field '{item}' is not declared in properties",
                )

    items = schema.get("items")
    if items is not None:
        if isinstance(items, list):
            for idx, child in enumerate(items):
                _validate_json_schema(tool, source, child, f"{path}.items[{idx}]", issues, root_defs=root_defs)
        else:
            _validate_json_schema(tool, source, items, f"{path}.items", issues, root_defs=root_defs)

    for key in ("anyOf", "oneOf"):
        variants = schema.get(key)
        if variants is not None:
            if not isinstance(variants, list) or not variants:
                _add(issues, tool, source, f"{path}.{key}", f"{key} must be a non-empty array")
            else:
                for idx, child in enumerate(variants):
                    _validate_json_schema(tool, source, child, f"{path}.{key}[{idx}]", issues, root_defs=root_defs)

    defs = schema.get("$defs")
    if defs is not None:
        if not isinstance(defs, dict):
            _add(issues, tool, source, f"{path}.$defs", "$defs must be an object")
        else:
            for name, child in defs.items():
                _validate_json_schema(tool, source, child, f"{path}.$defs.{name}", issues, root_defs=root_defs)


def validate_tool_schema(tool: Tool) -> list[ToolSchemaIssue]:
    issues: list[ToolSchemaIssue] = []
    name = str(getattr(tool, "name", "") or "")
    label = name or "<unnamed>"
    source = str(getattr(tool, "source", "") or getattr(tool, "toolset", "") or "tool")
    if not name:
        _add(issues, label, source, "name", "tool name is required")
    schema = tool.schema()
    if not isinstance(schema, dict):
        _add(issues, label, source, "schema", "tool.schema() must return an object")
        return issues
    for key in ("name", "description", "parameters"):
        if key not in schema:
            _add(issues, label, source, key, f"schema missing '{key}'")
    if schema.get("name") != name:
        _add(issues, label, source, "name", "schema name must match tool.name")
    if not isinstance(schema.get("description"), str) or not schema.get("description", "").strip():
        _add(issues, label, source, "description", "description must be a non-empty string")
    params = schema.get("parameters")
    _validate_json_schema(label, source, params, "parameters", issues)
    if isinstance(params, dict):
        if params.get("type") != "object":
            _add(issues, label, source, "parameters.type", "tool parameters root must have type 'object'")
        if "properties" not in params:
            _add(issues, label, source, "parameters.properties", "tool parameters root must declare properties")
    return issues


def validate_tool_registry(tools: list[Tool]) -> ToolSchemaValidation:
    all_issues: list[ToolSchemaIssue] = []
    invalid_tools: set[str] = set()
    for tool in tools:
        issues = validate_tool_schema(tool)
        all_issues.extend(issues)
        if any(issue.severity == "error" for issue in issues):
            invalid_tools.add(str(getattr(tool, "name", "") or "<unnamed>"))
    warning_count = sum(1 for issue in all_issues if issue.severity == "warning")
    invalid = len(invalid_tools)
    total = len(tools)
    return ToolSchemaValidation(
        ok=invalid == 0,
        total=total,
        valid=total - invalid,
        invalid=invalid,
        warnings=warning_count,
        issues=all_issues,
    )
