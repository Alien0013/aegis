"""Tool-schema sanitizer — strip JSON-Schema annotation keywords that LLM function-calling
ignores but that strict providers (Gemini, some OpenAI-compatible endpoints) reject. This
keeps MCP/plugin tool schemas portable across providers. Conservative on purpose: it removes
only non-structural annotations and normalizes union `type` arrays — it never drops
structural keywords (properties/required/items/$ref), so it can't break a valid schema.
"""

from __future__ import annotations

from typing import Any

# Pure annotations: safe to drop everywhere, commonly rejected by strict validators.
_DROP = frozenset({"$schema", "$id", "$comment", "examples", "exclusiveMinimum",
                   "exclusiveMaximum", "readOnly", "writeOnly", "deprecated"})


def sanitize(schema: Any) -> Any:
    if isinstance(schema, dict):
        out: dict[str, Any] = {}
        for k, v in schema.items():
            if k in _DROP:
                continue
            out[k] = sanitize(v)
        # Strict providers want a single `type`, not a union like ["string","null"].
        t = out.get("type")
        if isinstance(t, list):
            non_null = [x for x in t if x != "null"]
            out["type"] = non_null[0] if non_null else "string"
        return out
    if isinstance(schema, list):
        return [sanitize(x) for x in schema]
    return schema
