"""Tool ABC, execution context, and result type."""

from __future__ import annotations

import hashlib
import inspect
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..types import ToolSchema


@dataclass
class ToolResult:
    content: str
    is_error: bool = False
    # short one-line summary for the UI (defaults to first line of content)
    display: str | None = None
    data: Any = None

    @property
    def summary(self) -> str:
        if self.display:
            return self.display
        first = self.content.strip().splitlines()[0] if self.content.strip() else ""
        return first[:120]

    @property
    def classification(self) -> str:
        """success | error | refused | truncated | partial — a learning signal."""
        c = (self.content or "").lower()
        if self.is_error:
            if any(w in c for w in ("permission denied", "rejected", "blocked", "not authorized",
                                    "refused")):
                return "refused"
            return "error"
        if "<persisted-output>" in c or "truncated to protect context" in c:
            return "truncated"
        if "[truncated]" in c or "…[truncated]" in c or "…[truncated]…" in self.content:
            return "truncated"
        if not self.content.strip() or "(no output)" in c:
            return "partial"
        return "success"

    @classmethod
    def ok(cls, content: str, display: str | None = None, data: Any = None) -> "ToolResult":
        return cls(content=content, display=display, data=data)

    @classmethod
    def error(cls, message: str) -> "ToolResult":
        return cls(content=message, is_error=True, display=f"error: {message[:100]}")


@dataclass
class ToolContext:
    """Everything a tool may need at run time. Loosely typed to avoid cycles."""

    cwd: Path = field(default_factory=Path.cwd)
    config: Any = None
    memory: Any = None          # MemoryManager
    skills: Any = None          # SkillsLoader
    session: Any = None         # Session
    agent: Any = None           # Agent (for subagent spawn)
    task_id: str = ""           # stable execution-environment id for this run/task
    result_storage_env: Any = None  # optional execution environment for large result persistence
    fs: Any = None              # optional filesystem delegate (ACP: read/write via the editor)
    # callback(prompt:str)->bool used when a permission decision needs the user
    approver: Callable[[str], bool] | None = None
    # callback(question:str, choices:list[str])->str for the clarify tool (CLI prompts inline,
    # other surfaces may leave it None — clarify then returns the question for the next turn)
    asker: Callable[[str, list[str]], str] | None = None
    # callback(var_name, prompt, metadata)->dict for local hidden secret capture.
    # The secret value must not be returned to the model or transcript.
    secret_capture: Callable[[str, str, dict[str, Any] | None], dict[str, Any]] | None = None
    # callback(event:dict) for streaming tool events to the UI
    emit: Callable[[dict], None] | None = None

    def emit_event(self, **event: Any) -> None:
        if self.emit:
            self.emit(event)

    def dispatch_tool(self, name: str, arguments: dict[str, Any] | None = None, *,
                      registry: Any = None, permissions: Any = None) -> ToolResult:
        """Run another registered tool through the normal permission/middleware path.

        Plugin tools use this as their reference-style ``ctx.dispatch_tool`` helper. It
        intentionally reuses the current agent's registry and permission engine when
        available so plugin dispatch cannot bypass the harness' tool policy.
        """
        from ..types import ToolCall, new_id
        from .permissions import PermissionEngine
        from .registry import default_registry

        agent = self.agent
        reg = registry or getattr(agent, "registry", None) or default_registry()
        perms = permissions or getattr(agent, "permissions", None) or PermissionEngine(self.config)
        call = ToolCall(new_id("plugin_tool"), str(name or ""), dict(arguments or {}))

        def emit(event: dict[str, Any]) -> None:
            self.emit_event(**event)

        from ..agent.loop import ToolExecutor

        return ToolExecutor(reg, perms, self, emit).execute_one_raw(call)

    def inject_message(self, role: str, content: str, *, metadata: dict[str, Any] | None = None,
                       persist: bool = True) -> dict[str, Any]:
        """Append a message to the active session and optionally persist it.

        This gives plugins a small, explicit equivalent of AEGIS' message-injection
        hook without exposing session internals as a stable API surface.
        """
        from ..types import Message

        clean_role = str(role or "user").strip().lower()
        if clean_role == "assistant":
            message = Message.assistant(str(content or ""))
        elif clean_role == "system":
            message = Message.system(str(content or ""))
        elif clean_role == "tool":
            message = Message.tool("plugin_inject", "plugin", str(content or ""))
        else:
            clean_role = "user"
            message = Message.user(str(content or ""))
        if metadata:
            message.meta.update(dict(metadata))
        session = self.session
        if session is None:
            return message.to_dict()
        session.messages.append(message)
        if persist:
            store = getattr(self.agent, "store", None)
            save = getattr(store, "save", None)
            if callable(save):
                save(session)
        self.emit_event(type="plugin_message_injected", role=clean_role, length=len(message.content or ""))
        return message.to_dict()

    def llm(self, prompt: str | list[Any], *, system: str = "", model: str | None = None,
            reasoning: str | None = None, stream: bool = False,
            on_delta: Callable[[str], None] | None = None):
        """Call the configured provider from trusted plugin code.

        Returns the normalized ``LLMResponse`` so plugins can inspect text, tool calls,
        usage, and provider raw data when present.
        """
        from ..providers.fallback import build_with_fallbacks
        from ..types import Message

        provider = getattr(self.agent, "provider", None) or build_with_fallbacks(self.config, model=model)
        if isinstance(prompt, list):
            messages = list(prompt)
        else:
            messages = []
            if system:
                messages.append(Message.system(system))
            messages.append(Message.user(str(prompt or "")))
        effort = reasoning if reasoning is not None else getattr(self.agent, "reasoning", "off")
        return provider.complete(
            messages,
            tools=None,
            stream=stream,
            on_delta=on_delta,
            model=model,
            reasoning=effort,
            cwd=self.cwd,
        )


class Tool:
    """Base class. Subclasses set name/description/parameters and implement run()."""

    name: str = ""
    description: str = ""
    parameters: dict[str, Any] = {"type": "object", "properties": {}}
    # danger groups gate permissions: "fs", "runtime", "network", "automation"
    groups: list[str] = []
    # which toolset this belongs to (enabled via config.tools.toolsets)
    toolset: str = "core"
    # provenance and policy hints surfaced by the dashboard/API.
    source: str = "builtin"
    source_path: str = ""
    manifest_id: str = ""
    required_env: list[str] = []
    required_auth: list[str] = []
    output_limits: dict[str, Any] = {}
    max_result_size_chars: int | float | None = None
    risk_level: str = ""

    def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:  # pragma: no cover
        raise NotImplementedError

    def available(self) -> tuple[bool, str]:
        """Whether the tool is usable in this environment. Override for dep-gated tools so
        the model is never offered a tool it can't run. Returns (ok, reason-if-not)."""
        return True, ""

    def schema(self) -> ToolSchema:
        return {
            "name": self.name,
            "description": self.description.strip(),
            "parameters": self.parameters,
        }

    def metadata(self) -> dict[str, Any]:
        """reference-style audit metadata for model-visible and dashboard tool inventories.

        The metadata intentionally exposes names, hashes, and source locations only. It
        never reads configured secret values; env/auth fields are identifiers a human can
        satisfy during setup.
        """
        return tool_metadata(self)


def _schema_hash(schema: Any) -> str:
    try:
        body = json.dumps(schema, sort_keys=True, separators=(",", ":"), default=str)
    except TypeError:
        body = repr(schema)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _safe_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, dict):
        values = list(value.keys())
    elif isinstance(value, str):
        values = [value]
    elif isinstance(value, (list, tuple, set)):
        values = list(value)
    else:
        values = [value]
    return sorted({str(item).strip() for item in values if str(item).strip()})


def _tool_source(tool: Tool) -> str:
    raw = (
        getattr(tool, "source", "")
        or getattr(tool, "_aegis_source", "")
        or ("plugin" if getattr(tool, "_aegis_plugin", "") else "")
        or "builtin"
    )
    return str(raw).strip().lower() or "builtin"


def _tool_source_path(tool: Tool, source: str) -> str:
    for attr in ("source_path", "_aegis_source_path", "_aegis_plugin"):
        value = str(getattr(tool, attr, "") or "").strip()
        if value:
            return value
    if source == "mcp":
        server = str(getattr(tool, "server_name", "") or "").strip()
        remote = str(getattr(tool, "_remote", "") or getattr(tool, "name", "") or "").strip()
        if server:
            return f"mcp://{server}/{remote}"
    try:
        path = inspect.getsourcefile(tool.__class__) or inspect.getfile(tool.__class__)
    except (TypeError, OSError):
        path = ""
    return str(path or "")


def _tool_risk(tool: Tool) -> str:
    explicit = str(getattr(tool, "risk_level", "") or "").strip().lower()
    if explicit:
        return explicit
    groups = {str(group).lower() for group in getattr(tool, "groups", []) or []}
    if groups & {"runtime", "automation", "computer"}:
        return "high"
    if groups & {"fs", "filesystem", "network"}:
        return "medium"
    return "low"


def _tool_output_limits(tool: Tool) -> dict[str, Any]:
    raw = getattr(tool, "output_limits", None)
    if isinstance(raw, dict) and raw:
        out = dict(raw)
        if "max_result_size_chars" not in out:
            result_cap = getattr(tool, "max_result_size_chars", None)
            if result_cap is not None:
                out["max_result_size_chars"] = result_cap
        return out
    max_chars = getattr(tool, "max_output_chars", None) or getattr(tool, "MAX_OUTPUT_CHARS", None)
    out: dict[str, Any]
    if max_chars:
        out = {"max_chars": int(max_chars), "policy": "truncate"}
    else:
        out = {"max_chars": "config:tools.max_output_chars", "policy": "truncate"}
    result_cap = getattr(tool, "max_result_size_chars", None)
    if result_cap is not None:
        out["max_result_size_chars"] = result_cap
    return out


def tool_metadata(tool: Tool) -> dict[str, Any]:
    schema = tool.schema()
    available, reason = tool.available()
    cls = tool.__class__
    source = _tool_source(tool)
    source_path = _tool_source_path(tool, source)
    required_env = _safe_list(
        getattr(tool, "required_env", None)
        or getattr(tool, "_aegis_required_env", None)
        or getattr(tool, "env", None)
    )
    required_auth = _safe_list(
        getattr(tool, "required_auth", None)
        or getattr(tool, "_aegis_required_auth", None)
        or (["env"] if required_env else [])
    )
    manifest_id = str(
        getattr(tool, "manifest_id", "")
        or getattr(tool, "_aegis_manifest_id", "")
        or getattr(tool, "server_name", "")
        or ""
    )
    handler_module = f"{cls.__module__}.{cls.__qualname__}"
    return {
        "source": source,
        "source_path": source_path,
        "manifest_id": manifest_id,
        "toolset": str(getattr(tool, "toolset", "") or "core"),
        "schema_hash": _schema_hash(schema),
        "handler_module": handler_module,
        "availability_status": "available" if available else "unavailable",
        "availability_reason": "" if available else str(reason),
        "required_env": required_env,
        "required_auth": required_auth,
        "output_limits": _tool_output_limits(tool),
        "risk_level": _tool_risk(tool),
    }
