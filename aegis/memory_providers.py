"""Pluggable external memory backends implementing the MemoryProvider interface.

Selected via ``memory.provider`` in config. The builtin file memory
(MEMORY.md/USER.md) is always active alongside these providers.
"""

from __future__ import annotations

from dataclasses import dataclass
import importlib.util
import json
import os
from typing import Any

from . import config as cfg
from .memory import MemoryProvider
from .tools.base import Tool
from .util import append_line, read_text


@dataclass(frozen=True)
class MemoryProviderSpec:
    name: str
    display_name: str
    kind: str
    description: str
    config_schema: dict[str, dict[str, Any]]
    package: str = ""
    import_name: str = ""
    env_vars: tuple[str, ...] = ()
    env_any: tuple[str, ...] = ()
    optional_env_vars: tuple[str, ...] = ()
    setup_steps: tuple[str, ...] = ()
    tool_names: tuple[str, ...] = ()

    def metadata(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "known": True,
            "display_name": self.display_name,
            "kind": self.kind,
            "description": self.description,
            "package": self.package,
            "import_name": self.import_name,
            "env_vars": list(self.env_vars),
            "env_any": list(self.env_any),
            "optional_env_vars": list(self.optional_env_vars),
            "config_schema": self.config_schema,
            "setup_steps": list(self.setup_steps),
            "tool_names": list(self.tool_names),
        }


def _http_schema(name: str) -> dict[str, dict[str, Any]]:
    prefix = f"memory.{name}"
    return {
        "memory.provider": {
            "type": "string",
            "const": name,
            "description": f"Select the {name} external memory provider.",
        },
        f"{prefix}.add_url": {
            "type": "string",
            "format": "uri",
            "required": False,
            "description": "Endpoint that accepts completed turns as JSON.",
        },
        f"{prefix}.search_url": {
            "type": "string",
            "format": "uri",
            "required": False,
            "description": "Endpoint that accepts recall queries as JSON.",
        },
        f"{prefix}.headers": {
            "type": "object",
            "required": False,
            "secret_values": True,
            "description": "HTTP headers to send to add/search endpoints.",
        },
        f"{prefix}.result_path": {
            "type": "string",
            "default": "results",
            "required": False,
            "description": "Dot path in the JSON response containing recalled items.",
        },
    }


_COMMON_HTTP_SETUP = (
    "Set memory.provider to this provider name.",
    "Configure memory.<provider>.search_url for recall and/or memory.<provider>.add_url for ingestion.",
    "Put secrets in the matching <PROVIDER>_API_KEY env var or in configured headers; status only reports names.",
)


def _http_spec(name: str, display_name: str, description: str) -> MemoryProviderSpec:
    env_name = f"{name.upper()}_API_KEY"
    return MemoryProviderSpec(
        name=name,
        display_name=display_name,
        kind="http",
        description=description,
        config_schema=_http_schema(name),
        optional_env_vars=(env_name,),
        setup_steps=_COMMON_HTTP_SETUP,
        tool_names=("memory_provider_status", "memory_provider_setup", "memory_provider_recall"),
    )


_HTTP_PROVIDERS = {
    "openviking",
    "supermemory",
    "byterover",
    "hindsight",
    "holographic",
    "retaindb",
}

_PROVIDER_ORDER = (
    "jsonl",
    "mem0",
    "honcho",
    "openviking",
    "supermemory",
    "byterover",
    "hindsight",
    "holographic",
    "retaindb",
    "http",
)

_PROVIDER_SPECS: dict[str, MemoryProviderSpec] = {
    "jsonl": MemoryProviderSpec(
        name="jsonl",
        display_name="JSONL local memory",
        kind="local",
        description="Zero-dependency append-only local memory event log.",
        config_schema={
            "memory.provider": {
                "type": "string",
                "const": "jsonl",
                "description": "Select the local JSONL memory provider.",
            },
            "memory.jsonl.max_recent": {
                "type": "integer",
                "minimum": 1,
                "default": 12,
                "description": "Number of recent JSONL notes to render in the prompt.",
            },
        },
        setup_steps=(
            "Set memory.provider to jsonl.",
            "No credentials or packages are required.",
        ),
        tool_names=("memory_provider_status", "memory_provider_setup", "jsonl_memory_recent"),
    ),
    "mem0": MemoryProviderSpec(
        name="mem0",
        display_name="mem0",
        kind="sdk/http",
        description="Semantic/vector memory through the optional mem0ai SDK or a self-hosted mem0 HTTP API.",
        package="mem0ai",
        import_name="mem0",
        optional_env_vars=("MEM0_HOST", "MEM0_API_KEY", "OPENAI_API_KEY", "MEM0_USER_ID", "MEM0_AGENT_ID"),
        config_schema={
            "memory.provider": {
                "type": "string",
                "const": "mem0",
                "description": "Select the mem0 external memory provider.",
            },
            "memory.mem0.user_id": {
                "type": "string",
                "default": "aegis",
                "description": "User identifier passed to mem0 add/search calls.",
            },
            "memory.mem0.agent_id": {
                "type": "string",
                "default": "aegis",
                "description": "Agent identifier passed to mem0 host-mode add calls.",
            },
            "memory.mem0.host": {
                "type": "string",
                "required": False,
                "description": "Optional mem0 OSS/API host. Also read from MEM0_HOST.",
            },
            "memory.mem0.api_key_env": {
                "type": "string",
                "default": "MEM0_API_KEY",
                "required": False,
                "description": "Environment variable containing the mem0 API key.",
            },
            "memory.mem0.timeout": {
                "type": "integer",
                "minimum": 1,
                "default": 20,
                "required": False,
                "description": "HTTP timeout in seconds for mem0 host mode.",
            },
        },
        setup_steps=(
            "Install the optional package: pip install mem0ai.",
            "Set memory.provider to mem0.",
            "Configure MEM0_API_KEY for hosted mem0, or memory.mem0.host/MEM0_HOST for a self-hosted mem0 API.",
            "Configure any embedding/model keys required by your mem0 setup.",
        ),
        tool_names=(
            "memory_provider_status",
            "memory_provider_setup",
            "memory_provider_recall",
            "mem0_search",
            "mem0_add",
            "mem0_update",
            "mem0_delete",
        ),
    ),
    "honcho": MemoryProviderSpec(
        name="honcho",
        display_name="Honcho",
        kind="sdk",
        description="Hosted personal memory through the optional honcho-ai SDK.",
        package="honcho-ai",
        import_name="honcho",
        env_any=("HONCHO_API_KEY", "HONCHO_ENVIRONMENT"),
        config_schema={
            "memory.provider": {
                "type": "string",
                "const": "honcho",
                "description": "Select the Honcho external memory provider.",
            },
            "memory.honcho.user_id": {
                "type": "string",
                "default": "user",
                "description": "Honcho peer id for the human user.",
            },
            "memory.honcho.session_id": {
                "type": "string",
                "default": "aegis",
                "description": "Honcho session id used for conversation ingestion.",
            },
            "memory.honcho.environment": {
                "type": "string",
                "required": False,
                "description": "Optional Honcho environment, such as demo.",
            },
        },
        setup_steps=(
            "Install the optional package: pip install honcho-ai.",
            "Set memory.provider to honcho.",
            "Set HONCHO_API_KEY, or set HONCHO_ENVIRONMENT=demo for Honcho's demo environment.",
        ),
        tool_names=("memory_provider_status", "memory_provider_setup", "memory_provider_recall"),
    ),
    "openviking": _http_spec(
        "openviking",
        "OpenViking",
        "HTTP-backed memory provider using configured OpenViking-compatible endpoints.",
    ),
    "supermemory": _http_spec(
        "supermemory",
        "Supermemory",
        "HTTP-backed memory provider using configured Supermemory-compatible endpoints.",
    ),
    "byterover": _http_spec(
        "byterover",
        "ByteRover",
        "HTTP-backed memory provider using configured ByteRover-compatible endpoints.",
    ),
    "hindsight": _http_spec(
        "hindsight",
        "Hindsight",
        "HTTP-backed memory provider using configured Hindsight-compatible endpoints.",
    ),
    "holographic": _http_spec(
        "holographic",
        "Holographic",
        "HTTP-backed memory provider using configured Holographic-compatible endpoints.",
    ),
    "retaindb": _http_spec(
        "retaindb",
        "RetainDB",
        "HTTP-backed memory provider using configured RetainDB-compatible endpoints.",
    ),
    "http": _http_spec(
        "http",
        "Generic HTTP memory",
        "Generic REST memory provider for custom add/search endpoints.",
    ),
}


def _normalize_provider_name(name: str) -> str:
    return (name or "").strip().lower()


def _spec(name: str) -> MemoryProviderSpec | None:
    return _PROVIDER_SPECS.get(_normalize_provider_name(name))


def memory_provider_metadata(name: str) -> dict[str, Any]:
    spec = _spec(name)
    return spec.metadata() if spec else {"name": _normalize_provider_name(name), "known": False}


def memory_provider_setup(name: str) -> dict[str, Any]:
    spec = _spec(name)
    if not spec:
        return {"name": _normalize_provider_name(name), "known": False, "setup_steps": []}
    meta = spec.metadata()
    return {
        "name": spec.name,
        "display_name": spec.display_name,
        "known": True,
        "setup_steps": meta["setup_steps"],
        "config_schema": meta["config_schema"],
        "env_vars": meta["env_vars"],
        "env_any": meta["env_any"],
        "optional_env_vars": meta["optional_env_vars"],
        "package": meta["package"],
    }


def memory_provider_config_schema(name: str | None = None) -> dict[str, Any]:
    if name:
        spec = _spec(name)
        if not spec:
            return {"name": _normalize_provider_name(name), "known": False, "properties": {}}
        return {
            "name": spec.name,
            "known": True,
            "type": "object",
            "properties": spec.config_schema,
        }
    return {key: memory_provider_config_schema(key) for key in _PROVIDER_ORDER}


def _env_state(names: tuple[str, ...]) -> list[dict[str, Any]]:
    return [{"name": name, "set": bool(os.environ.get(name))} for name in names]


def _dependency_state(spec: MemoryProviderSpec) -> dict[str, Any]:
    if not spec.import_name:
        return {"required": False, "package": "", "import_name": "", "installed": True}
    return {
        "required": True,
        "package": spec.package,
        "import_name": spec.import_name,
        "installed": importlib.util.find_spec(spec.import_name) is not None,
    }


def _mem0_host(config) -> str:
    if config is None:
        return os.environ.get("MEM0_HOST", "").strip().rstrip("/")
    return str(config.get("memory.mem0.host", "") or os.environ.get("MEM0_HOST", "")).strip().rstrip("/")


def _mem0_user_id(config) -> str:
    return str(os.environ.get("MEM0_USER_ID") or (config.get("memory.mem0.user_id", "aegis") if config else "aegis") or "aegis")


def _mem0_agent_id(config) -> str:
    return str(os.environ.get("MEM0_AGENT_ID") or (config.get("memory.mem0.agent_id", "aegis") if config else "aegis") or "aegis")


def _mem0_api_key_env(config) -> str:
    value = config.get("memory.mem0.api_key_env", "MEM0_API_KEY") if config else "MEM0_API_KEY"
    return str(value or "MEM0_API_KEY")


def _mem0_timeout(config) -> int:
    try:
        return max(1, int(config.get("memory.mem0.timeout", 20) if config else 20))
    except (TypeError, ValueError):
        return 20


def _redacted_config(config, spec: MemoryProviderSpec) -> dict[str, Any]:
    if config is None:
        return {}
    out: dict[str, Any] = {}
    for key, schema in spec.config_schema.items():
        if key == "memory.provider":
            continue
        value = config.get(key)
        if value in (None, "", [], {}):
            continue
        if schema.get("secret_values") and isinstance(value, dict):
            out[key] = {"configured": True, "keys": sorted(str(k) for k in value)}
        elif schema.get("secret"):
            out[key] = {"configured": True}
        else:
            out[key] = value
    return out


def memory_provider_status(name: str, config=None) -> dict[str, Any]:
    name = _normalize_provider_name(name)
    spec = _spec(name)
    if not spec:
        return {
            "name": name,
            "known": False,
            "configured": False,
            "ready": False,
            "ok": False,
            "status": "unknown_provider",
            "problems": ["unknown provider"],
        }

    dependency = _dependency_state(spec)
    if spec.name == "mem0" and _mem0_host(config):
        dependency = {
            "required": False,
            "package": spec.package,
            "import_name": spec.import_name,
            "installed": True,
            "mode": "host",
        }
    env_required = _env_state(spec.env_vars)
    env_any = _env_state(spec.env_any)
    env_optional = _env_state(spec.optional_env_vars)
    problems: list[str] = []

    if dependency["required"] and not dependency["installed"]:
        problems.append(f"install optional package {spec.package}")
    missing_env = [row["name"] for row in env_required if not row["set"]]
    if missing_env:
        problems.append("set " + ", ".join(missing_env))
    if env_any and not any(row["set"] for row in env_any):
        problems.append("set one of " + ", ".join(row["name"] for row in env_any))

    if spec.kind == "http" and config is not None:
        prefix = f"memory.{name}"
        if not (config.get(f"{prefix}.add_url") or config.get(f"{prefix}.search_url")):
            problems.append(f"configure {prefix}.search_url or {prefix}.add_url")
    if spec.name == "mem0":
        host = _mem0_host(config)
        if host:
            dependency["mode"] = "host"
        elif dependency.get("installed"):
            dependency["mode"] = "sdk"

    selected = _normalize_provider_name(config.get("memory.provider", "")) if config else ""
    ready = not problems
    return {
        "name": spec.name,
        "display_name": spec.display_name,
        "known": True,
        "kind": spec.kind,
        "configured": selected == spec.name,
        "ready": ready,
        "ok": ready,
        "status": "ready" if ready else "needs_setup",
        "problems": problems,
        "dependency": dependency,
        "env": {
            "required": env_required,
            "any_of": env_any,
            "optional": env_optional,
        },
        "config": _redacted_config(config, spec),
        "tools": list(spec.tool_names),
    }


def memory_provider_catalog(config=None) -> list[dict[str, Any]]:
    rows = []
    for name in _PROVIDER_ORDER:
        meta = memory_provider_metadata(name)
        status = memory_provider_status(name, config)
        rows.append({**meta, "status": status})
    return rows


def memory_provider_report(config) -> dict[str, Any]:
    active = _normalize_provider_name(config.get("memory.provider", "")) if config else ""
    return {
        "provider": active,
        "active": memory_provider_status(active, config) if active else {
            "name": "",
            "known": True,
            "configured": True,
            "ready": True,
            "ok": True,
            "status": "builtin_only",
            "problems": [],
        },
        "provider_catalog": memory_provider_catalog(config),
        "config_schema": memory_provider_config_schema(),
    }


class _ProviderStatusTool(Tool):
    name = "memory_provider_status"
    description = "Report the active external memory provider status without exposing secrets."
    parameters = {"type": "object", "properties": {}, "additionalProperties": False}
    groups: list[str] = []
    toolset = "core"

    def __init__(self, provider):
        self.provider = provider

    def available(self) -> tuple[bool, str]:
        return True, ""

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }

    def run(self, args: dict[str, Any], ctx):
        from .tools.base import ToolResult

        status = self.provider.status()
        return ToolResult.ok(
            json.dumps(status, indent=2, sort_keys=True),
            display=f"{status.get('name', 'memory')} provider: {status.get('status', 'unknown')}",
            data=status,
        )


class _ProviderSetupTool(Tool):
    name = "memory_provider_setup"
    description = "Show setup steps and config keys for the active external memory provider."
    parameters = {"type": "object", "properties": {}, "additionalProperties": False}
    groups: list[str] = []
    toolset = "core"

    def __init__(self, provider):
        self.provider = provider

    def available(self) -> tuple[bool, str]:
        return True, ""

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }

    def run(self, args: dict[str, Any], ctx):
        from .tools.base import ToolResult

        setup = self.provider.setup()
        return ToolResult.ok(
            json.dumps(setup, indent=2, sort_keys=True),
            display=f"{setup.get('name', 'memory')} provider setup",
            data=setup,
        )


class _ProviderRecallTool(Tool):
    name = "memory_provider_recall"
    description = "Fetch provider-backed memory relevant to a query."
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The current question or topic to recall memory for.",
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    }
    toolset = "core"

    def __init__(self, provider, *, network: bool = False):
        self.provider = provider
        self.groups = ["network"] if network else []

    def available(self) -> tuple[bool, str]:
        can_recall = getattr(self.provider, "can_recall", None)
        if callable(can_recall) and not can_recall():
            return False, "provider recall endpoint is not configured"
        return True, ""

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }

    def run(self, args: dict[str, Any], ctx):
        from .tools.base import ToolResult

        query = str(args.get("query") or "").strip()
        if not query:
            return ToolResult.error("query is required")
        try:
            session_id = getattr(getattr(ctx, "session", None), "id", "")
            text = self.provider.prefetch(query, session_id=session_id)
        except Exception:  # noqa: BLE001
            text = ""
        if not isinstance(text, str) or not text.strip():
            return ToolResult.ok(
                f"No memory returned by {getattr(self.provider, 'name', 'provider')}.",
                display="no provider memory returned",
                data={"provider": getattr(self.provider, "name", "")},
            )
        return ToolResult.ok(
            text.strip(),
            display=f"recalled memory from {getattr(self.provider, 'name', 'provider')}",
            data={"provider": getattr(self.provider, "name", ""), "query": query},
        )


class _ProviderAddTool(Tool):
    name = "memory_provider_add"
    description = "Add one provider-backed memory item."
    parameters = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Memory text to add."},
        },
        "required": ["text"],
        "additionalProperties": False,
    }
    toolset = "core"
    groups = ["network"]

    def __init__(self, provider):
        self.provider = provider

    def available(self) -> tuple[bool, str]:
        return (True, "") if callable(getattr(self.provider, "add_memory", None)) else (False, "provider cannot add memories")

    def schema(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "parameters": self.parameters}

    def run(self, args: dict[str, Any], ctx):
        from .tools.base import ToolResult

        text = str(args.get("text") or "").strip()
        if not text:
            return ToolResult.error("text is required")
        try:
            data = self.provider.add_memory(text)
        except Exception as exc:  # noqa: BLE001
            return ToolResult.error(f"memory add failed: {exc}")
        return ToolResult.ok("memory added", display="memory added", data={"provider": self.provider.name, "result": data})


class _ProviderUpdateTool(Tool):
    name = "memory_provider_update"
    description = "Update one provider-backed memory item by id."
    parameters = {
        "type": "object",
        "properties": {
            "memory_id": {"type": "string", "description": "Provider memory id."},
            "text": {"type": "string", "description": "Replacement memory text."},
        },
        "required": ["memory_id", "text"],
        "additionalProperties": False,
    }
    toolset = "core"
    groups = ["network"]

    def __init__(self, provider):
        self.provider = provider

    def available(self) -> tuple[bool, str]:
        return (True, "") if callable(getattr(self.provider, "update_memory", None)) else (False, "provider cannot update memories")

    def schema(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "parameters": self.parameters}

    def run(self, args: dict[str, Any], ctx):
        from .tools.base import ToolResult

        memory_id = str(args.get("memory_id") or "").strip()
        text = str(args.get("text") or "").strip()
        if not memory_id or not text:
            return ToolResult.error("memory_id and text are required")
        try:
            data = self.provider.update_memory(memory_id, text)
        except Exception as exc:  # noqa: BLE001
            return ToolResult.error(f"memory update failed: {exc}")
        return ToolResult.ok("memory updated", display="memory updated", data={"provider": self.provider.name, "result": data})


class _ProviderDeleteTool(Tool):
    name = "memory_provider_delete"
    description = "Delete one provider-backed memory item by id."
    parameters = {
        "type": "object",
        "properties": {
            "memory_id": {"type": "string", "description": "Provider memory id."},
        },
        "required": ["memory_id"],
        "additionalProperties": False,
    }
    toolset = "core"
    groups = ["network"]

    def __init__(self, provider):
        self.provider = provider

    def available(self) -> tuple[bool, str]:
        return (True, "") if callable(getattr(self.provider, "delete_memory", None)) else (False, "provider cannot delete memories")

    def schema(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "parameters": self.parameters}

    def run(self, args: dict[str, Any], ctx):
        from .tools.base import ToolResult

        memory_id = str(args.get("memory_id") or "").strip()
        if not memory_id:
            return ToolResult.error("memory_id is required")
        try:
            data = self.provider.delete_memory(memory_id)
        except Exception as exc:  # noqa: BLE001
            return ToolResult.error(f"memory delete failed: {exc}")
        return ToolResult.ok("memory deleted", display="memory deleted", data={"provider": self.provider.name, "result": data})


class _Mem0SearchTool(Tool):
    name = "mem0_search"
    description = "Search mem0 memory for relevant items."
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 8},
        },
        "required": ["query"],
        "additionalProperties": False,
    }
    toolset = "core"
    groups = ["network"]

    def __init__(self, provider):
        self.provider = provider

    def available(self) -> tuple[bool, str]:
        return True, ""

    def schema(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "parameters": self.parameters}

    def run(self, args: dict[str, Any], ctx):
        from .tools.base import ToolResult

        query = str(args.get("query") or "").strip()
        if not query:
            return ToolResult.error("query is required")
        try:
            limit = max(1, min(50, int(args.get("limit", 8) or 8)))
        except (TypeError, ValueError):
            limit = 8
        try:
            items = list(self.provider._search_items(query, limit))
        except Exception as exc:  # noqa: BLE001
            return ToolResult.error(f"mem0 search failed: {exc}")
        texts = [self.provider._item_text(item) for item in items]
        body = "\n".join(f"- {text}" for text in texts if text) or "(empty)"
        return ToolResult.ok(
            body,
            display=f"{len(items)} mem0 result(s)",
            data={"provider": "mem0", "query": query, "results": items},
        )


class _Mem0AddTool(_ProviderAddTool):
    name = "mem0_add"
    description = "Add one mem0 memory item."


class _Mem0UpdateTool(_ProviderUpdateTool):
    name = "mem0_update"
    description = "Update one mem0 memory item by id."


class _Mem0DeleteTool(_ProviderDeleteTool):
    name = "mem0_delete"
    description = "Delete one mem0 memory item by id."


class _JSONLRecentTool(Tool):
    name = "jsonl_memory_recent"
    description = "Read recent entries from the local JSONL memory provider."
    parameters = {
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "default": 12,
                "description": "Maximum number of recent JSONL notes to read.",
            },
        },
        "additionalProperties": False,
    }
    groups: list[str] = []
    toolset = "core"

    def __init__(self, provider):
        self.provider = provider

    def available(self) -> tuple[bool, str]:
        return True, ""

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }

    def run(self, args: dict[str, Any], ctx):
        from .tools.base import ToolResult

        raw_limit = args.get("limit", self.provider.max_recent)
        try:
            limit = max(1, min(100, int(raw_limit)))
        except (TypeError, ValueError):
            limit = self.provider.max_recent
        notes = self.provider.recent_notes(limit)
        body = "\n".join(f"- {note}" for note in notes) if notes else "(empty)"
        return ToolResult.ok(
            body,
            display=f"{len(notes)} jsonl memory note(s)",
            data={"provider": "jsonl", "notes": notes, "path": str(self.provider.path)},
        )


class ProviderSurfaceMixin:
    name = "memory-provider"

    def initialize(self, session_id: str = "", **_kw) -> None:
        self._session_id = session_id

    def metadata(self) -> dict[str, Any]:
        return memory_provider_metadata(self.name)

    def config_schema(self) -> dict[str, Any]:
        return memory_provider_config_schema(self.name)

    def setup(self) -> dict[str, Any]:
        return memory_provider_setup(self.name)

    def status(self) -> dict[str, Any]:
        status = memory_provider_status(self.name, getattr(self, "config", None))
        status["initialized"] = True
        return status

    def _provider_tools(self) -> list:
        return []

    def tools(self) -> list:
        return [
            _ProviderStatusTool(self),
            _ProviderSetupTool(self),
            *self._provider_tools(),
        ]

    def on_session_switch(self, *, old_session_id: str, new_session_id: str, **_kw) -> None:
        self._session_id = new_session_id

    def _prefetch_lock(self):
        import threading

        lock = getattr(self, "_prefetch_cache_lock", None)
        if lock is None:
            lock = threading.Lock()
            self._prefetch_cache_lock = lock
        return lock

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        """Non-blocking warm recall. Providers still expose synchronous
        ``prefetch``; this caches a best-effort result that a later turn can consume."""
        query = str(query or "")
        session_id = session_id or str(getattr(self, "_session_id", "") or "")
        if not query.strip():
            return
        import threading

        def _run() -> None:
            try:
                text = self.prefetch(query, session_id=session_id)
            except Exception:  # noqa: BLE001
                text = ""
            with self._prefetch_lock():
                self._prefetch_cache = {
                    "query": query,
                    "session_id": session_id,
                    "text": text if isinstance(text, str) else "",
                }

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()

    def consume_prefetch(self, query: str, *, session_id: str = "") -> str:
        session_id = session_id or str(getattr(self, "_session_id", "") or "")
        with self._prefetch_lock():
            cached = getattr(self, "_prefetch_cache", None)
            if not isinstance(cached, dict):
                return ""
            if cached.get("query") != str(query or ""):
                return ""
            if session_id and cached.get("session_id") not in ("", session_id):
                return ""
            self._prefetch_cache = None
            return str(cached.get("text") or "")


class JSONLMemoryProvider(ProviderSurfaceMixin, MemoryProvider):
    """Zero-dependency external memory: appends turn notes to ext_memory.jsonl."""

    name = "jsonl"

    def __init__(self, max_recent: int = 12, config=None):
        self.config = config
        self.path = cfg.sub("ext_memory.jsonl")
        self.max_recent = max_recent

    def recent_notes(self, limit: int | None = None) -> list[str]:
        raw = read_text(self.path)
        if not raw.strip():
            return []
        max_notes = limit or self.max_recent
        notes: list[str] = []
        for ln in raw.strip().splitlines():
            try:
                note = json.loads(ln).get("note", "")
            except json.JSONDecodeError:
                continue
            if note:
                notes.append(str(note))
        return notes[-max_notes:]

    def system_prompt_block(self) -> str:
        notes = ["- " + note for note in self.recent_notes(self.max_recent)]
        return "# Recalled context\n" + "\n".join(notes) if notes else ""

    def status(self) -> dict[str, Any]:
        status = super().status()
        status["path"] = str(self.path)
        status["note_count"] = len(self.recent_notes(10_000))
        return status

    def _provider_tools(self) -> list:
        return [_JSONLRecentTool(self)]

    def sync_turn(self, messages) -> None:
        # store the last user/assistant exchange as a compact note
        last_user = next((m.content for m in reversed(messages) if m.role == "user"), "")
        last_asst = next((m.content for m in reversed(messages) if m.role == "assistant"), "")
        if last_user:
            note = f"user asked: {last_user[:160]}"
            if last_asst:
                note += f" | replied: {last_asst[:160]}"
            append_line(self.path, json.dumps({"note": note}))

    def on_memory_write(self, *, action: str, target: str, content: str = "",
                        old_text: str = "", result: str = "",
                        session_id: str = "", **kw) -> None:
        if action in {"add", "replace"} and content:
            note = f"{target} {action}: {content[:240]}"
        elif action == "remove" and old_text:
            note = f"{target} remove: {old_text[:240]}"
        else:
            note = f"{target} {action}".strip()
        append_line(self.path, json.dumps({
            "event": "memory_write",
            "action": action,
            "target": target,
            "note": note,
            "content": content,
            "old_text": old_text,
            "result": result,
            "session_id": session_id,
        }))

    def on_session_end(self, messages) -> None:
        last_user = next((m.content for m in reversed(messages) if getattr(m, "role", "") == "user"), "")
        if last_user:
            append_line(self.path, json.dumps({
                "event": "session_end",
                "note": f"session ended after: {last_user[:240]}",
            }))

    def on_pre_compress(self, messages) -> str:
        notes = self.recent_notes(min(5, self.max_recent))
        return "\n".join(f"- {note}" for note in notes) if notes else ""


class Mem0Provider(ProviderSurfaceMixin, MemoryProvider):
    """Vector memory via the `mem0ai` package or a self-hosted mem0 HTTP API."""

    name = "mem0"

    def __init__(self, user_id: str = "aegis", agent_id: str = "aegis",
                 host: str = "", api_key_env: str = "MEM0_API_KEY",
                 timeout: int = 20, config=None):
        self.config = config
        self.user_id = user_id
        self.agent_id = agent_id
        self.host = str(host or "").strip().rstrip("/")
        self.api_key_env = api_key_env or "MEM0_API_KEY"
        self.timeout = timeout
        self._mem = None
        if not self.host:
            try:
                from mem0 import Memory
            except ImportError as e:  # noqa: BLE001
                raise RuntimeError("mem0 provider needs `pip install mem0ai` or memory.mem0.host/MEM0_HOST") from e
            try:
                self._mem = Memory()
            except Exception as e:  # noqa: BLE001
                raise RuntimeError("mem0 provider could not initialize; check mem0 configuration") from e
        self._last_query = ""

    @staticmethod
    def _item_text(item) -> str:
        if isinstance(item, dict):
            return str(item.get("memory") or item.get("text") or "")
        return str(item or "")

    def _headers(self) -> dict[str, str]:
        key = os.environ.get(self.api_key_env, "").strip()
        return {"X-API-Key": key} if key else {}

    def _url(self, path: str) -> str:
        return f"{self.host}/{path.lstrip('/')}"

    def _request(self, method: str, path: str, *, json_body: dict[str, Any] | None = None):
        import httpx

        response = httpx.request(
            method,
            self._url(path),
            json=json_body,
            headers=self._headers(),
            timeout=self.timeout,
        )
        raise_for_status = getattr(response, "raise_for_status", None)
        if callable(raise_for_status):
            raise_for_status()
        try:
            return response.json()
        except Exception:  # noqa: BLE001
            return getattr(response, "text", "")

    def _search_items(self, query: str, limit: int = 8):
        query = query or "recent context"
        if self.host:
            results = self._request("POST", "/search", json_body={
                "query": query,
                "filters": {"user_id": self.user_id},
                "top_k": limit,
            })
        else:
            results = self._mem.search(query, user_id=self.user_id, limit=limit)
        items = results.get("results", results) if isinstance(results, dict) else results
        return items or []

    def _search(self, query: str, limit: int = 8) -> list[str]:
        items = self._search_items(query, limit)
        return [text for text in (self._item_text(r) for r in (items or [])) if text]

    def add_memory(self, text: str):
        payload = {
            "messages": [{"role": "user", "content": text}],
            "user_id": self.user_id,
            "agent_id": self.agent_id,
            "infer": False,
        }
        if self.host:
            return self._request("POST", "/memories", json_body=payload)
        return self._mem.add(payload["messages"], user_id=self.user_id)

    def update_memory(self, memory_id: str, text: str):
        if self.host:
            return self._request("PUT", f"/memories/{memory_id}", json_body={"text": text})
        updater = getattr(self._mem, "update", None) or getattr(self._mem, "update_memory", None)
        if not callable(updater):
            raise RuntimeError("mem0 SDK client does not expose update")
        try:
            return updater(memory_id=memory_id, data=text)
        except TypeError:
            return updater(memory_id, text)

    def delete_memory(self, memory_id: str):
        if self.host:
            return self._request("DELETE", f"/memories/{memory_id}")
        deleter = getattr(self._mem, "delete", None) or getattr(self._mem, "delete_memory", None)
        if not callable(deleter):
            raise RuntimeError("mem0 SDK client does not expose delete")
        try:
            return deleter(memory_id=memory_id)
        except TypeError:
            return deleter(memory_id)

    def system_prompt_block(self) -> str:
        try:
            mems = self._search(self._last_query or "recent context")
            return "# Long-term memory (mem0)\n" + "\n".join(f"- {m}" for m in mems if m) if mems else ""
        except Exception:  # noqa: BLE001
            return ""

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        try:
            self._last_query = query
            mems = self._search(query)
            return "# Long-term memory (mem0)\n" + "\n".join(f"- {m}" for m in mems if m) if mems else ""
        except Exception:  # noqa: BLE001
            return ""

    def _provider_tools(self) -> list:
        return [
            _ProviderRecallTool(self, network=True),
            _Mem0SearchTool(self),
            _Mem0AddTool(self),
            _Mem0UpdateTool(self),
            _Mem0DeleteTool(self),
        ]

    def sync_turn(self, messages) -> None:
        try:
            self._last_query = next((m.content for m in reversed(messages) if m.role == "user"), "")
            wire = [{"role": m.role, "content": m.content} for m in messages[-6:]
                    if m.role in ("user", "assistant") and m.content]
            if wire:
                if self.host:
                    self._request("POST", "/memories", json_body={
                        "messages": wire,
                        "user_id": self.user_id,
                        "agent_id": self.agent_id,
                        "infer": True,
                    })
                else:
                    self._mem.add(wire, user_id=self.user_id)
        except Exception:  # noqa: BLE001
            pass

    def on_session_end(self, messages) -> None:
        self.sync_turn(messages)

    def on_pre_compress(self, messages) -> str:
        query = next((m.content for m in reversed(messages)
                      if getattr(m, "role", "") == "user" and getattr(m, "content", "")), "")
        return self.prefetch(query or self._last_query or "recent context")


class HonchoProvider(ProviderSurfaceMixin, MemoryProvider):
    """Personal memory via Honcho (plastic-labs). Optional dep: `honcho-ai`.

    Set HONCHO_API_KEY (or HONCHO_ENVIRONMENT=demo for the public demo). Messages
    are added to a Honcho session; recall uses the dialectic `peer.chat()` endpoint.
    """

    name = "honcho"

    def __init__(self, user_id: str = "user", session_id: str = "aegis",
                 environment: str = "", config=None):
        try:
            from honcho import Honcho
        except ImportError as e:  # noqa: BLE001
            raise RuntimeError("honcho provider needs `pip install honcho-ai`") from e
        self.config = config
        kwargs = {}
        environment = environment or os.environ.get("HONCHO_ENVIRONMENT", "")
        if environment:
            kwargs["environment"] = environment
        try:
            self._honcho = Honcho(**kwargs)
            self._user = self._honcho.peer(user_id)
            self._assistant = self._honcho.peer("assistant")
            self._session = self._honcho.session(session_id)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError("honcho provider could not initialize; check Honcho credentials") from e
        self._last_query = ""

    @staticmethod
    def _response_text(resp) -> str:
        return resp if isinstance(resp, str) else getattr(resp, "content", "") or str(resp)

    def system_prompt_block(self) -> str:
        try:
            q = self._last_query or "What do you know about this user that's relevant right now?"
            resp = self._user.chat(q)
            text = self._response_text(resp)
            return "# Personal memory (Honcho)\n" + text.strip() if text and text.strip() else ""
        except Exception:  # noqa: BLE001
            return ""

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        try:
            self._last_query = query
            text = self._response_text(self._user.chat(query))
            return "# Personal memory (Honcho)\n" + text.strip() if text and text.strip() else ""
        except Exception:  # noqa: BLE001
            return ""

    def _provider_tools(self) -> list:
        return [_ProviderRecallTool(self, network=True)]

    def sync_turn(self, messages) -> None:
        try:
            self._last_query = next((m.content for m in reversed(messages) if m.role == "user"), "")
            batch = []
            for m in messages[-4:]:
                if m.role == "user" and m.content:
                    batch.append(self._user.message(m.content))
                elif m.role == "assistant" and m.content:
                    batch.append(self._assistant.message(m.content))
            if batch:
                self._session.add_messages(batch)
        except Exception:  # noqa: BLE001
            pass

    def on_session_end(self, messages) -> None:
        self.sync_turn(messages)

    def on_pre_compress(self, messages) -> str:
        query = next((m.content for m in reversed(messages)
                      if getattr(m, "role", "") == "user" and getattr(m, "content", "")), "")
        return self.prefetch(query or self._last_query or "recent context")


class HTTPMemoryProvider(ProviderSurfaceMixin, MemoryProvider):
    """Generic HTTP memory backend — wires any REST memory service via config.

    Used for providers without a bundled SDK (openviking, supermemory, byterover,
    hindsight, holographic, retaindb, …). Configure under ``memory.<name>``:
      add_url, search_url (POST JSON {messages}/{query}), headers, result_path.
    """

    def __init__(self, name: str, config):
        self.name = name
        self.config = config
        node = config.get(f"memory.{name}", {}) or {}
        self.add_url = node.get("add_url")
        self.search_url = node.get("search_url")
        self.headers = dict(node.get("headers", {}) or {})
        # allow an API key from env: <NAME>_API_KEY -> Authorization: Bearer
        key = os.environ.get(f"{name.upper()}_API_KEY")
        if key and "Authorization" not in self.headers:
            self.headers["Authorization"] = f"Bearer {key}"
        self.result_path = node.get("result_path", "results")
        self._last_query = ""

    @staticmethod
    def _path_get(data, path: str):
        node = data
        for part in (path or "").split("."):
            if not part:
                continue
            if isinstance(node, dict):
                node = node.get(part)
            else:
                return None
        return node

    def _extract_texts(self, data) -> list[str]:
        items = self._path_get(data, self.result_path) if isinstance(data, dict) else data
        if items is None and isinstance(data, dict):
            items = data
        if isinstance(items, dict):
            items = items.get("results") or items.get("items") or items.get("memories") or []
        if isinstance(items, str):
            items = [items]
        texts = []
        for item in items or []:
            if isinstance(item, dict):
                text = item.get("memory") or item.get("text") or item.get("content") or ""
            else:
                text = str(item)
            if text:
                texts.append(str(text))
        return texts[:8]

    def can_recall(self) -> bool:
        return bool(self.search_url)

    def system_prompt_block(self) -> str:
        return self.prefetch(self._last_query or "recent context")

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        if not self.search_url:
            return ""
        try:
            import httpx
            self._last_query = query
            payload = {"query": query or "recent context"}
            session_id = session_id or str(getattr(self, "_session_id", "") or "")
            if session_id:
                payload["session_id"] = session_id
            r = httpx.post(self.search_url, json=payload,
                           headers=self.headers, timeout=20)
            data = r.json()
            texts = self._extract_texts(data)
            return f"# Memory ({self.name})\n" + "\n".join(f"- {t}" for t in texts) if texts else ""
        except Exception:  # noqa: BLE001
            return ""

    def status(self) -> dict[str, Any]:
        status = super().status()
        status.update({
            "add_url_configured": bool(self.add_url),
            "search_url_configured": bool(self.search_url),
            "headers_configured": sorted(self.headers.keys()),
            "result_path": self.result_path,
        })
        return status

    def _provider_tools(self) -> list:
        return [_ProviderRecallTool(self, network=True)]

    def sync_turn(self, messages) -> None:
        if not self.add_url:
            return
        try:
            import httpx
            self._last_query = next((m.content for m in reversed(messages) if m.role == "user"), "")
            wire = [{"role": m.role, "content": m.content} for m in messages[-6:]
                    if m.role in ("user", "assistant") and m.content]
            payload = {"messages": wire}
            session_id = str(getattr(self, "_session_id", "") or "")
            if session_id:
                payload["session_id"] = session_id
            httpx.post(self.add_url, json=payload, headers=self.headers, timeout=20)
        except Exception:  # noqa: BLE001
            pass

    def on_session_end(self, messages) -> None:
        if not self.add_url:
            return
        try:
            import httpx
            wire = [{"role": m.role, "content": m.content} for m in messages[-12:]
                    if m.role in ("user", "assistant") and m.content]
            payload = {"event": "session_end", "messages": wire}
            session_id = str(getattr(self, "_session_id", "") or "")
            if session_id:
                payload["session_id"] = session_id
            httpx.post(self.add_url, json=payload, headers=self.headers, timeout=20)
        except Exception:  # noqa: BLE001
            pass

    def on_pre_compress(self, messages) -> str:
        query = next((m.content for m in reversed(messages)
                      if getattr(m, "role", "") == "user" and getattr(m, "content", "")), "")
        return self.prefetch(query or self._last_query or "recent context")


def build_memory_provider(name: str, config) -> MemoryProvider | None:
    name = (name or "").strip().lower()
    if name == "jsonl":
        try:
            max_recent = max(1, int(config.get("memory.jsonl.max_recent", 12) or 12))
        except (TypeError, ValueError):
            max_recent = 12
        return JSONLMemoryProvider(max_recent=max_recent, config=config)
    if name == "mem0":
        try:
            return Mem0Provider(
                user_id=_mem0_user_id(config),
                agent_id=_mem0_agent_id(config),
                host=_mem0_host(config),
                api_key_env=_mem0_api_key_env(config),
                timeout=_mem0_timeout(config),
                config=config,
            )
        except RuntimeError as e:
            print(f"  ! {e}")
            return None
    if name == "honcho":
        try:
            return HonchoProvider(
                user_id=config.get("memory.honcho.user_id", "user") or "user",
                session_id=config.get("memory.honcho.session_id", "aegis") or "aegis",
                environment=config.get("memory.honcho.environment", "") or "",
                config=config,
            )
        except RuntimeError as e:
            print(f"  ! {e}")
            return None
    if name in _HTTP_PROVIDERS or name == "http":
        return HTTPMemoryProvider(name, config)
    return None
