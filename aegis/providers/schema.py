"""Provider-facing tool schema sanitizer.

The provider adapters all call :func:`sanitize` on the final tool parameter
schema before sending it to model APIs.  Keep that API small, but make the
repair pass robust enough for MCP/Pydantic-style schemas and stricter local or
OpenAI-compatible backends.
"""

from __future__ import annotations

import copy
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Drop dialect identifiers that provider APIs commonly reject at the final
# tool-schema boundary.  Hermes keeps annotation and validation hints such as
# ``examples``, ``readOnly``, and ``exclusiveMinimum``; AEGIS follows that
# default and only removes these dialect identity fields up front.
_DROP = frozenset({
    "$schema",
    "$id",
    "$comment",
})

_SCHEMA_TYPES = frozenset({
    "array",
    "boolean",
    "integer",
    "null",
    "number",
    "object",
    "string",
})
_REF_FORBIDDEN_SIBLINGS = frozenset({"default"})
_TOP_LEVEL_FORBIDDEN_KEYS = frozenset({"allOf", "anyOf", "oneOf", "enum", "not"})
_STRIP_ON_RECOVERY_KEYS = frozenset({"pattern", "format"})


def sanitize(schema: Any) -> Any:
    """Return a deep-copied, provider-safe version of a tool schema.

    ``schema`` may be the raw AEGIS parameter schema, an AEGIS/Responses style
    ``{"name": ..., "parameters": ...}`` wrapper, an OpenAI function wrapper,
    or an Anthropic/Codex-style ``input_schema`` / ``inputSchema`` wrapper.
    """
    if _is_openai_function_wrapper(schema):
        out = copy.deepcopy(schema)
        fn = out.get("function")
        fn["parameters"] = _sanitize_parameters(
            fn.get("parameters"),
            path=str(fn.get("name") or out.get("name") or "<tool>"),
            force_root=True,
        )
        return out

    if _is_parameters_wrapper(schema):
        out = copy.deepcopy(schema)
        out["parameters"] = _sanitize_parameters(
            out.get("parameters"),
            path=str(out.get("name") or "<tool>"),
            force_root=True,
        )
        return out

    if _is_input_schema_wrapper(schema):
        out = copy.deepcopy(schema)
        key = "input_schema" if "input_schema" in out else "inputSchema"
        out[key] = _sanitize_parameters(
            out.get(key),
            path=str(out.get("name") or "<tool>"),
            force_root=True,
        )
        return out

    return _sanitize_parameters(schema, path="<schema>", force_root=False)


def strip_nullable_unions(
    schema: Any,
    *,
    keep_nullable_hint: bool = True,
) -> Any:
    """Collapse nullable ``anyOf`` / ``oneOf`` unions to their non-null branch."""
    if isinstance(schema, list):
        return [
            strip_nullable_unions(item, keep_nullable_hint=keep_nullable_hint)
            for item in schema
        ]
    if not isinstance(schema, dict):
        return schema

    stripped = {
        key: strip_nullable_unions(value, keep_nullable_hint=keep_nullable_hint)
        for key, value in schema.items()
    }
    for key in ("anyOf", "oneOf"):
        variants = stripped.get(key)
        if not isinstance(variants, list):
            continue
        non_null = [
            item
            for item in variants
            if not (isinstance(item, dict) and item.get("type") == "null")
        ]
        if len(non_null) == 1 and len(non_null) != len(variants):
            replacement = dict(non_null[0]) if isinstance(non_null[0], dict) else {}
            if keep_nullable_hint:
                replacement.setdefault("nullable", True)
            for meta_key in ("title", "description", "default", "examples"):
                if meta_key in stripped and meta_key not in replacement:
                    if meta_key == "default" and "$ref" in replacement:
                        continue
                    replacement[meta_key] = stripped[meta_key]
            return strip_nullable_unions(
                replacement,
                keep_nullable_hint=keep_nullable_hint,
            )
    return stripped


def strip_pattern_and_format(tools: list[dict] | None) -> tuple[list[dict] | None, int]:
    """Strip ``pattern`` and ``format`` schema keywords in-place.

    This is an opt-in recovery helper for backends whose grammar compilers
    reject regex or format hints.  Property names such as ``pattern`` are
    preserved because only schema-node siblings are removed.
    """
    if not tools:
        return tools, 0

    stripped = 0

    def walk(node: Any) -> None:
        nonlocal stripped
        if isinstance(node, dict):
            is_schema_node = any(key in node for key in ("type", "anyOf", "oneOf", "allOf", "$ref"))
            for key in list(node):
                if is_schema_node and key in _STRIP_ON_RECOVERY_KEYS:
                    node.pop(key, None)
                    stripped += 1
                    continue
                walk(node[key])
        elif isinstance(node, list):
            for item in node:
                walk(item)

    for params in _iter_tool_parameter_schemas(tools):
        walk(params)

    if stripped:
        logger.info("schema sanitizer stripped %d pattern/format keyword(s)", stripped)
    return tools, stripped


def strip_slash_enum(tools: list[dict] | None) -> tuple[list[dict] | None, int]:
    """Strip ``enum`` keywords whose string values contain ``/`` in-place."""
    if not tools:
        return tools, 0

    stripped = 0

    def walk(node: Any) -> None:
        nonlocal stripped
        if isinstance(node, dict):
            enum_value = node.get("enum")
            if isinstance(enum_value, list) and any(
                isinstance(value, str) and "/" in value
                for value in enum_value
            ):
                node.pop("enum", None)
                stripped += 1
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    for params in _iter_tool_parameter_schemas(tools):
        walk(params)

    if stripped:
        logger.info("schema sanitizer stripped %d slash-containing enum keyword(s)", stripped)
    return tools, stripped


def _sanitize_parameters(schema: Any, *, path: str, force_root: bool) -> Any:
    sanitized = _sanitize_node(schema, path)
    if not isinstance(sanitized, dict):
        return {"type": "object", "properties": {}} if force_root else sanitized

    sanitized = strip_nullable_unions(sanitized, keep_nullable_hint=True)
    if force_root or _is_object_like_root(sanitized):
        sanitized = _ensure_object_root(sanitized)
        sanitized = _strip_top_level_combinators(sanitized, path=path)
    return _strip_ref_siblings(sanitized)


def _sanitize_node(node: Any, path: str) -> Any:
    if isinstance(node, str):
        if node in _SCHEMA_TYPES:
            if node == "object":
                return {"type": "object", "properties": {}}
            return {"type": node}
        return {"type": "object", "properties": {}}

    if isinstance(node, list):
        return [_sanitize_node(item, f"{path}[{idx}]") for idx, item in enumerate(node)]

    if not isinstance(node, dict):
        return node

    out: dict[str, Any] = {}
    for key, value in node.items():
        if key in _DROP:
            continue

        if key == "type" and isinstance(value, list):
            non_null = [item for item in value if item != "null"]
            if len(non_null) == 1 and isinstance(non_null[0], str):
                out["type"] = non_null[0]
                if "null" in value:
                    out.setdefault("nullable", True)
                continue
            first_type = next(
                (item for item in non_null if isinstance(item, str)),
                None,
            )
            out["type"] = first_type or "object"
            continue

        if key in {"properties", "$defs", "definitions"} and isinstance(value, dict):
            out[key] = {
                prop_name: _sanitize_node(prop_schema, f"{path}.{key}.{prop_name}")
                for prop_name, prop_schema in value.items()
            }
            continue

        if key in {"items", "additionalProperties"}:
            out[key] = value if isinstance(value, bool) else _sanitize_node(value, f"{path}.{key}")
            continue

        if key in {"anyOf", "oneOf", "allOf"} and isinstance(value, list):
            out[key] = [
                _sanitize_node(item, f"{path}.{key}[{idx}]")
                for idx, item in enumerate(value)
            ]
            continue

        if key in {"required", "enum", "const", "default", "examples"}:
            out[key] = copy.deepcopy(value)
            continue

        out[key] = _sanitize_node(value, f"{path}.{key}") if isinstance(value, (dict, list)) else value

    if not out.get("type") and "properties" in out:
        out["type"] = "object"
    if out.get("type") == "object":
        _repair_object_node(out)
    return out


def _repair_object_node(node: dict[str, Any]) -> None:
    if not isinstance(node.get("properties"), dict):
        node["properties"] = {}

    required = node.get("required")
    if isinstance(required, list):
        props = node.get("properties") or {}
        valid = [name for name in required if isinstance(name, str) and name in props]
        if valid:
            node["required"] = valid
        else:
            node.pop("required", None)


def _ensure_object_root(schema: dict[str, Any]) -> dict[str, Any]:
    out = dict(schema)
    if not out.get("type") or out.get("type") != "object":
        out["type"] = "object"
    _repair_object_node(out)
    return out


def _is_object_like_root(schema: dict[str, Any]) -> bool:
    if schema.get("type") == "object":
        return True
    if not schema.get("type") and (
        "properties" in schema
        or "required" in schema
        or any(key in schema for key in _TOP_LEVEL_FORBIDDEN_KEYS)
    ):
        return True
    return False


def _strip_top_level_combinators(params: dict[str, Any], *, path: str) -> dict[str, Any]:
    out = dict(params)
    for key in _TOP_LEVEL_FORBIDDEN_KEYS:
        if key in out:
            logger.debug("schema sanitizer[%s] stripped top-level %s", path, key)
            out.pop(key, None)
    return out


def _strip_ref_siblings(node: Any) -> Any:
    if isinstance(node, list):
        return [_strip_ref_siblings(item) for item in node]
    if not isinstance(node, dict):
        return node

    out = {key: _strip_ref_siblings(value) for key, value in node.items()}
    if "$ref" in out:
        for key in _REF_FORBIDDEN_SIBLINGS:
            out.pop(key, None)
    return out


def _iter_tool_parameter_schemas(tools: list[dict]):
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        fn = tool.get("function")
        if isinstance(fn, dict) and isinstance(fn.get("parameters"), dict):
            yield fn["parameters"]
            continue
        if isinstance(tool.get("parameters"), dict):
            yield tool["parameters"]
            continue
        for key in ("input_schema", "inputSchema"):
            if isinstance(tool.get(key), dict):
                yield tool[key]
                break


def _is_openai_function_wrapper(schema: Any) -> bool:
    return isinstance(schema, dict) and isinstance(schema.get("function"), dict)


def _is_parameters_wrapper(schema: Any) -> bool:
    return (
        isinstance(schema, dict)
        and "parameters" in schema
        and (
            "name" in schema
            or schema.get("type") == "function"
            or "description" in schema
        )
    )


def _is_input_schema_wrapper(schema: Any) -> bool:
    return (
        isinstance(schema, dict)
        and ("input_schema" in schema or "inputSchema" in schema)
        and (
            "name" in schema
            or schema.get("type") == "function"
            or "description" in schema
        )
    )
