"""A from-scratch MCP client (JSON-RPC 2.0) over stdio and Streamable HTTP.

Implements the lifecycle: initialize -> notifications/initialized -> tools/list ->
tools/call. Each remote tool is wrapped as an AEGIS ``Tool`` (namespaced
``mcp__<server>__<tool>``) and registered like any built-in. Resource and prompt
capabilities are exposed as utility tools when a server advertises them.

Config (config.yaml ``mcp.servers`` or ``~/.aegis/mcp.json`` Claude-Desktop format):

    mcp:
      servers:
        filesystem: {command: npx, args: ["-y","@modelcontextprotocol/server-filesystem","/tmp"]}
        remote:     {url: "https://example.com/mcp", headers: {Authorization: "Bearer ..."}}
"""

from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import re
import selectors
import subprocess
from urllib.parse import urlparse

import httpx

from .. import config as cfg
from ..redact import redact_secret_values, redact_secrets
from ..tools.base import Tool, ToolContext, ToolResult
from ..util import ensure_dir, read_text, truncate

PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "aegis", "version": "0.1.0"}
_SAFE_ENV_KEYS = {
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "TMPDIR", "TEMP", "TMP",
    "LANG", "LC_ALL", "LC_CTYPE", "PYTHONIOENCODING", "SYSTEMROOT", "WINDIR",
}
_SAFE_ENV_KEYS_UPPER = {k.upper() for k in _SAFE_ENV_KEYS}
_NAME_PART_RE = re.compile(r"[^A-Za-z0-9_]")


class MCPError(RuntimeError):
    pass


class InvalidMCPUrlError(ValueError):
    pass


class MCPClient:
    def __init__(self, name: str, *, command: str | None = None, args: list[str] | None = None,
                 env: dict | None = None, url: str | None = None, headers: dict | None = None,
                 cwd: str | None = None, tool_filter: dict | None = None):
        self.name = name
        self.command = command
        self.args = args or []
        self.env = env or {}
        self.url = url
        self.headers = headers or {}
        self.cwd = cwd
        self.tool_filter = tool_filter or {}
        self._proc: subprocess.Popen | None = None
        self._id = 0
        self._session_id: str | None = None
        self._initialized = False

    @property
    def is_http(self) -> bool:
        return bool(self.url)

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    # -- transport ----------------------------------------------------------
    def _spawn(self) -> None:
        env = _safe_subprocess_env(self.env)
        self._proc = subprocess.Popen(
            [self.command, *self.args],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, bufsize=1, env=env, cwd=self.cwd,
        )

    def _stdio_request(self, payload: dict, timeout: float = 30.0) -> dict | None:
        assert self._proc and self._proc.stdin and self._proc.stdout
        self._proc.stdin.write(json.dumps(payload) + "\n")
        self._proc.stdin.flush()
        if "id" not in payload:   # notification, no response expected
            return None
        wanted = payload["id"]
        deadline_loops = 0
        with selectors.DefaultSelector() as selector:
            selector.register(self._proc.stdout, selectors.EVENT_READ)
            while True:
                events = selector.select(timeout)
                if not events:
                    raise MCPError(
                        f"{self.name}: timed out waiting for response to {payload.get('method')}")
                line = self._proc.stdout.readline()
                if not line:
                    raise MCPError(f"{self.name}: server closed the connection")
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if msg.get("id") == wanted:
                    return msg
                deadline_loops += 1
                if deadline_loops > 1000:
                    raise MCPError(f"{self.name}: too many unrelated messages")

    def _http_request(self, payload: dict, timeout: float = 60.0) -> dict | None:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
            **self.headers,
        }
        if self._session_id:
            headers["MCP-Session-Id"] = self._session_id
        with httpx.Client(timeout=timeout) as c:
            r = c.post(self.url, headers=headers, json=payload)
            if r.status_code >= 400:
                raise MCPError(redact_secrets(f"{self.name}: HTTP {r.status_code}: {r.text[:200]}"))
            if "MCP-Session-Id" in r.headers:
                self._session_id = r.headers["MCP-Session-Id"]
            if "id" not in payload:
                return None
            ctype = r.headers.get("content-type", "")
            if "text/event-stream" in ctype:
                for line in r.text.splitlines():
                    if line.startswith("data:"):
                        msg = json.loads(line[5:].strip())
                        if msg.get("id") == payload["id"]:
                            return msg
                raise MCPError(f"{self.name}: no matching SSE response")
            return r.json()

    def _request(self, method: str, params: dict | None = None, *, notify: bool = False) -> dict | None:
        payload = {"jsonrpc": "2.0", "method": method}
        if not notify:
            payload["id"] = self._next_id()
        if params is not None:
            payload["params"] = params
        resp = self._http_request(payload) if self.is_http else self._stdio_request(payload)
        if resp and "error" in resp:
            message = resp["error"].get("message", resp["error"])
            raise MCPError(redact_secrets(f"{self.name}: {_nonempty_exc_text(message)}"))
        return resp

    # -- lifecycle ----------------------------------------------------------
    def connect(self) -> "MCPClient":
        if self._initialized:
            return self
        if not self.is_http:
            if not self.command:
                raise MCPError(f"{self.name}: no command or url configured")
            self._spawn()
        self._request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": CLIENT_INFO,
        })
        self._request("notifications/initialized", notify=True)
        self._initialized = True
        return self

    def list_tools(self, *, apply_filter: bool = True) -> list[dict]:
        resp = self._request("tools/list", {})
        tools = (resp or {}).get("result", {}).get("tools", [])
        return _filter_tools(tools, self.tool_filter) if apply_filter else tools

    def list_resources(self) -> list[dict]:
        resp = self._request("resources/list", {})
        return (resp or {}).get("result", {}).get("resources", [])

    def read_resource(self, uri: str) -> str:
        resp = self._request("resources/read", {"uri": uri})
        result = (resp or {}).get("result", {})
        parts: list[str] = []
        for item in result.get("contents", []):
            label = item.get("uri") or uri
            mime = item.get("mimeType") or item.get("mime_type") or ""
            if "text" in item:
                header = f'<resource uri="{label}"' + (f' mime="{mime}"' if mime else "") + ">"
                parts.append(f"{header}\n{item.get('text') or ''}\n</resource>")
            elif item.get("blob"):
                size = len(str(item.get("blob") or ""))
                detail = f"base64 blob, {size} chars"
                if mime:
                    detail += f", {mime}"
                parts.append(f"[resource {label}: {detail}]")
        return "\n\n".join(parts) or "(empty resource)"

    def list_prompts(self) -> list[dict]:
        resp = self._request("prompts/list", {})
        return (resp or {}).get("result", {}).get("prompts", [])

    def get_prompt(self, name: str, arguments: dict | None = None) -> str:
        resp = self._request("prompts/get", {"name": name, "arguments": arguments or {}})
        result = (resp or {}).get("result", {})
        parts: list[str] = []
        if result.get("description"):
            parts.append(f"# {result['description']}")
        for msg in result.get("messages", []):
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if isinstance(content, str):
                text = content
            elif isinstance(content, dict):
                text = _render_prompt_content(content)
            elif isinstance(content, list):
                text = "\n".join(_render_prompt_content(block) for block in content)
            else:
                text = str(content)
            parts.append(f"<{role}>\n{text}\n</{role}>")
        return "\n\n".join(parts) or "(empty prompt)"

    def call_tool(self, name: str, arguments: dict) -> tuple[str, bool]:
        resp = self._request("tools/call", {"name": name, "arguments": arguments}, )
        result = (resp or {}).get("result", {})
        parts: list[str] = []
        for block in result.get("content", []):
            if block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif block.get("type") == "image":
                tag = _cache_mcp_image_block(block)
                if tag:
                    parts.append(tag)
                else:
                    mime = block.get("mimeType") or block.get("mime_type") or "image/*"
                    data_len = len(str(block.get("data") or ""))
                    parts.append(f"[image content: {mime}, {data_len} base64 chars]")
            elif block.get("type") == "resource":
                res = block.get("resource", {})
                parts.append(res.get("text") or f"[resource {res.get('uri')}]")
            else:
                parts.append(f"[{block.get('type')} content]")
        text = "\n".join(part for part in parts if part) or ""
        structured = result.get("structuredContent")
        if structured is None:
            structured = result.get("structured_content")
        if structured is not None:
            structured = redact_secret_values(structured)
            rendered = json.dumps(structured, ensure_ascii=False, indent=2)
            if text:
                text = f"{text}\n\n<structuredContent>\n{rendered}\n</structuredContent>"
            else:
                text = rendered
        return text or "(no content)", bool(result.get("isError"))

    def close(self) -> None:
        if self._proc:
            try:
                self._proc.stdin and self._proc.stdin.close()
                self._proc.terminate()
                self._proc.wait(timeout=5)
            except Exception:  # noqa: BLE001
                try:
                    self._proc.kill()
                except Exception:  # noqa: BLE001
                    pass
            self._proc = None
        self._initialized = False


class MCPTool(Tool):
    groups = ["network"]   # remote tools are gated like any side-effecting tool
    toolset = "mcp"

    def __init__(self, client: MCPClient, tool_def: dict):
        self._client = client
        self._remote = tool_def["name"]
        self.name = f"mcp__{_safe_name_part(client.name)}__{_safe_name_part(tool_def['name'])}"
        self.source = "mcp"
        self.server_name = client.name
        self.source_path = f"mcp://{client.name}/{self._remote}"
        self.manifest_id = client.name
        self.required_env = sorted(str(key) for key in client.env)
        self.required_auth = ["headers"] if client.headers else ([] if not self.required_env else ["env"])
        self.output_limits = {"max_chars": 30000, "policy": "truncate"}
        self.description = tool_def.get("description", "") or f"MCP tool {self._remote}"
        self.parameters = _normalize_mcp_input_schema(tool_def.get("inputSchema"))

    def run(self, args, ctx: ToolContext) -> ToolResult:
        try:
            content, is_err = self._client.call_tool(self._remote, args)
        except Exception as e:  # noqa: BLE001
            return ToolResult.error(f"mcp call failed: {redact_secrets(_nonempty_exc_text(e))}")
        return ToolResult(content=truncate(content, 30_000), is_error=is_err,
                          display=f"mcp:{self._client.name}/{self._remote}")


class MCPReadResourceTool(Tool):
    groups = ["network"]
    toolset = "mcp"

    def __init__(self, client: MCPClient, resources: list[dict]):
        self._client = client
        self.name = f"mcp__{_safe_name_part(client.name)}__read_resource"
        self.source = "mcp"
        self.server_name = client.name
        self.source_path = f"mcp://{client.name}/resources"
        self.manifest_id = client.name
        self.required_env = sorted(str(key) for key in client.env)
        self.required_auth = ["headers"] if client.headers else ([] if not self.required_env else ["env"])
        self.output_limits = {"max_chars": 30000, "policy": "truncate"}
        preview = _capability_preview(resources, "uri")
        self.description = (
            f"Read an MCP resource from server '{client.name}' by URI."
            + (f" Available resources include: {preview}." if preview else "")
        )
        self.parameters = {
            "type": "object",
            "properties": {
                "uri": {"type": "string", "description": "Resource URI from resources/list."},
            },
            "required": ["uri"],
        }

    def run(self, args, ctx: ToolContext) -> ToolResult:
        try:
            content = self._client.read_resource(str(args.get("uri", "")))
        except Exception as e:  # noqa: BLE001
            return ToolResult.error(f"mcp resource read failed: {e}")
        return ToolResult.ok(
            truncate(content, 30_000),
            display=f"mcp:{self._client.name}/resource",
            data={"artifact_ref": str(args.get("uri", "")), "server": self._client.name},
        )


class MCPGetPromptTool(Tool):
    groups = ["network"]
    toolset = "mcp"

    def __init__(self, client: MCPClient, prompts: list[dict]):
        self._client = client
        self.name = f"mcp__{_safe_name_part(client.name)}__get_prompt"
        self.source = "mcp"
        self.server_name = client.name
        self.source_path = f"mcp://{client.name}/prompts"
        self.manifest_id = client.name
        self.required_env = sorted(str(key) for key in client.env)
        self.required_auth = ["headers"] if client.headers else ([] if not self.required_env else ["env"])
        self.output_limits = {"max_chars": 30000, "policy": "truncate"}
        preview = _capability_preview(prompts, "name")
        self.description = (
            f"Render an MCP prompt template from server '{client.name}' by name."
            + (f" Available prompts include: {preview}." if preview else "")
        )
        self.parameters = {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Prompt name from prompts/list."},
                "arguments": {
                    "type": "object",
                    "description": "Prompt arguments keyed by argument name.",
                    "additionalProperties": True,
                },
            },
            "required": ["name"],
        }

    def run(self, args, ctx: ToolContext) -> ToolResult:
        try:
            content = self._client.get_prompt(
                str(args.get("name", "")),
                args.get("arguments") if isinstance(args.get("arguments"), dict) else {},
            )
        except Exception as e:  # noqa: BLE001
            return ToolResult.error(f"mcp prompt render failed: {e}")
        return ToolResult.ok(
            truncate(content, 30_000),
            display=f"mcp:{self._client.name}/prompt",
            data={"server": self._client.name, "prompt": str(args.get("name", ""))},
        )


class MCPManager:
    def __init__(self):
        self.clients: list[MCPClient] = []

    def add(self, client: MCPClient) -> None:
        self.clients.append(client)

    def connect_all(self) -> list[Tool]:
        tools: list[Tool] = []
        for client in self.clients:
            try:
                client.connect()
                for td in client.list_tools():
                    tools.append(MCPTool(client, td))
                try:
                    resources = client.list_resources()
                except Exception:  # noqa: BLE001
                    resources = []
                if resources:
                    tools.append(MCPReadResourceTool(client, resources))
                try:
                    prompts = client.list_prompts()
                except Exception:  # noqa: BLE001
                    prompts = []
                if prompts:
                    tools.append(MCPGetPromptTool(client, prompts))
            except Exception as e:  # noqa: BLE001
                print(f"  ! MCP server '{client.name}' failed: {e}")
        return tools

    def close_all(self) -> None:
        for c in self.clients:
            c.close()


def _safe_subprocess_env(user_env: dict | None = None) -> dict[str, str]:
    """Return a minimal stdio-server env plus explicitly configured values."""
    env: dict[str, str] = {}
    for key, value in os.environ.items():
        if key in _SAFE_ENV_KEYS or key.upper() in _SAFE_ENV_KEYS_UPPER or key.startswith("XDG_"):
            env[key] = value
    for key, value in (user_env or {}).items():
        env[str(key)] = str(value)
    return env


def _nonempty_exc_text(value) -> str:
    text = str(value).strip()
    return text if text else repr(value)


def _safe_name_part(value: str) -> str:
    text = _NAME_PART_RE.sub("_", str(value or "")).strip("_")
    return text or "unnamed"


def _normalize_mcp_input_schema(schema) -> dict:
    """Repair common MCP JSON Schema shapes before exposing them to model APIs."""
    if not isinstance(schema, dict) or not schema:
        return {"type": "object", "properties": {}}

    def rewrite_refs(node):
        if isinstance(node, list):
            return [rewrite_refs(item) for item in node]
        if not isinstance(node, dict):
            return node
        out = {}
        for key, value in node.items():
            out_key = "$defs" if key == "definitions" else key
            out[out_key] = rewrite_refs(value)
        ref = out.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/definitions/"):
            out["$ref"] = "#/$defs/" + ref[len("#/definitions/"):]
        return out

    def collapse_nullable(node):
        if isinstance(node, list):
            return [collapse_nullable(item) for item in node]
        if not isinstance(node, dict):
            return node
        for union_key in ("anyOf", "oneOf"):
            choices = node.get(union_key)
            if isinstance(choices, list) and len(choices) == 2:
                non_null = [choice for choice in choices if not (
                    isinstance(choice, dict) and choice.get("type") == "null"
                )]
                if len(non_null) == 1:
                    collapsed = collapse_nullable(non_null[0])
                    if isinstance(collapsed, dict):
                        out = {
                            k: collapse_nullable(v)
                            for k, v in node.items()
                            if k not in {"anyOf", "oneOf"}
                        }
                        out.update(collapsed)
                        out["nullable"] = True
                        return out
        return {key: collapse_nullable(value) for key, value in node.items()}

    def repair_objects(node):
        if isinstance(node, list):
            return [repair_objects(item) for item in node]
        if not isinstance(node, dict):
            return node
        repaired = {key: repair_objects(value) for key, value in node.items()}
        if not repaired.get("type") and ("properties" in repaired or "required" in repaired):
            repaired["type"] = "object"
        if repaired.get("type") == "object":
            if not isinstance(repaired.get("properties"), dict):
                repaired["properties"] = {}
            required = repaired.get("required")
            if isinstance(required, list):
                props = repaired.get("properties") or {}
                valid = [name for name in required if isinstance(name, str) and name in props]
                if valid:
                    repaired["required"] = valid
                else:
                    repaired.pop("required", None)
        return repaired

    normalized = repair_objects(collapse_nullable(rewrite_refs(schema)))
    if not isinstance(normalized, dict):
        return {"type": "object", "properties": {}}
    if not normalized.get("type") and ("properties" in normalized or "required" in normalized):
        normalized["type"] = "object"
    if normalized.get("type") == "object" and not isinstance(normalized.get("properties"), dict):
        normalized["properties"] = {}
    return normalized


def _mcp_image_extension_for_mime_type(mime_type: str) -> str:
    normalized = (mime_type or "").split(";", 1)[0].strip().lower()
    if normalized in {"image/jpeg", "image/jpg"}:
        return ".jpg"
    return mimetypes.guess_extension(normalized) or ".png"


def _looks_like_image(data: bytes) -> bool:
    return (
        data.startswith(b"\x89PNG\r\n\x1a\n")
        or data.startswith(b"\xff\xd8\xff")
        or data.startswith(b"GIF87a")
        or data.startswith(b"GIF89a")
        or (data.startswith(b"RIFF") and data[8:12] == b"WEBP")
    )


def _cache_mcp_image_block(block: dict) -> str:
    mime_type = str(block.get("mimeType") or block.get("mime_type") or "").split(";", 1)[0].lower()
    if not mime_type.startswith("image/") or not block.get("data"):
        return ""
    try:
        raw = base64.b64decode(str(block.get("data")), validate=True)
    except (TypeError, ValueError):
        return ""
    if not _looks_like_image(raw):
        return ""
    ext = _mcp_image_extension_for_mime_type(mime_type)
    digest = hashlib.sha256(raw).hexdigest()[:16]
    out_dir = ensure_dir(cfg.sub("tool_outputs", "mcp_images"))
    out = out_dir / f"mcp_{digest}{ext}"
    if not out.exists():
        out.write_bytes(raw)
    return f"MEDIA:{out}"


def _validate_remote_mcp_url(server_name: str, url) -> str:
    if not isinstance(url, str):
        raise InvalidMCPUrlError(
            f"MCP server '{server_name}' expected a string url, got {type(url).__name__}"
        )
    stripped = url.strip()
    if not stripped:
        raise InvalidMCPUrlError(f"MCP server '{server_name}' has an empty url")
    parsed = urlparse(stripped)
    if parsed.scheme not in {"http", "https"}:
        raise InvalidMCPUrlError(
            f"MCP server '{server_name}' url scheme must be http or https"
        )
    if not parsed.hostname:
        raise InvalidMCPUrlError(f"MCP server '{server_name}' url is missing host")
    return stripped


def _server_configs(config) -> dict:
    servers = dict(config.get("mcp.servers", {}) or {})
    # also merge ~/.aegis/mcp.json (Claude Desktop format: {"mcpServers": {...}})
    raw = read_text(cfg.sub("mcp.json"))
    if raw.strip():
        try:
            data = json.loads(raw)
            servers.update(_normalize_external_mcp_config(data))
        except json.JSONDecodeError:
            pass
    return servers


def catalog(config) -> list[dict]:
    """Configured MCP catalog entries.

    The catalog is intentionally local/config-backed: users and distributions can
    ship known server recipes without requiring a network marketplace.
    """
    out = []
    for entry in config.get("mcp.catalog", []) or []:
        if isinstance(entry, dict) and entry.get("name") and (entry.get("command") or entry.get("url")):
            out.append(dict(entry))
    return out


def install_from_catalog(config, name: str) -> dict:
    entries = {e["name"]: e for e in catalog(config)}
    entry = entries.get(name)
    if not entry:
        raise KeyError(name)
    servers = dict(config.get("mcp.servers", {}) or {})
    spec = {k: v for k, v in entry.items()
            if k in {"command", "args", "env", "url", "headers", "cwd", "tool_filter"}}
    servers[name] = spec
    config.data.setdefault("mcp", {})["servers"] = servers
    config.save()
    return spec


def probe_server(config, name: str) -> dict:
    """Connect to a configured MCP server and return a structured inventory."""
    spec = _server_configs(config).get(name)
    if not isinstance(spec, dict) or not (spec.get("command") or spec.get("url")):
        raise KeyError(name)
    client = _client_from_spec(name, spec)
    try:
        client.connect()
        all_tools = client.list_tools(apply_filter=False)
        tools = _filter_tools(all_tools, spec.get("tool_filter"))
        resources, resource_error = _optional_capability(client.list_resources)
        prompts, prompt_error = _optional_capability(client.list_prompts)
        return {
            "ok": True,
            "name": name,
            "transport": "http" if spec.get("url") else "stdio",
            "tools": tools,
            "all_tools": all_tools,
            "resources": resources,
            "prompts": prompts,
            "capability_errors": {
                k: v for k, v in {
                    "resources": resource_error,
                    "prompts": prompt_error,
                }.items() if v
            },
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "name": name, "error": str(e), "tools": [],
                "all_tools": [], "resources": [], "prompts": []}
    finally:
        client.close()


def tool_checklist(config, name: str) -> dict:
    """Return discovered MCP tools with their current selected/surfaced state."""
    probe = probe_server(config, name)
    if not probe.get("ok"):
        return {**probe, "items": []}
    selected = {tool.get("name", "") for tool in probe.get("tools", [])}
    items = []
    for tool in probe.get("all_tools", []):
        tool_name = str(tool.get("name", ""))
        if not tool_name:
            continue
        items.append({
            "name": tool_name,
            "description": str(tool.get("description", "")),
            "selected": tool_name in selected,
        })
    return {**probe, "items": items}


def save_tool_checklist(config, name: str, include: list[str]) -> dict:
    """Persist a selected MCP tool checklist as ``tool_filter.include``."""
    servers = dict(config.get("mcp.servers", {}) or {})
    spec = servers.get(name)
    if not isinstance(spec, dict):
        raise KeyError(name)
    spec = dict(spec)
    tool_filter = dict(spec.get("tool_filter") or {})
    tool_filter["include"] = _dedupe_strings(include)
    spec["tool_filter"] = tool_filter
    servers[name] = spec
    config.data.setdefault("mcp", {})["servers"] = servers
    config.save()
    return spec


def _filter_tools(tools: list[dict], tool_filter: dict | None) -> list[dict]:
    filt = tool_filter or {}
    has_include = "include" in filt and filt.get("include") is not None
    include = set(filt.get("include") or [])
    exclude = set(filt.get("exclude") or [])
    if not has_include and not exclude:
        return tools
    out = []
    for tool in tools:
        name = tool.get("name", "")
        if has_include and name not in include:
            continue
        if exclude and name in exclude:
            continue
        out.append(tool)
    return out


def _optional_capability(fn) -> tuple[list[dict], str]:
    try:
        return fn(), ""
    except Exception as e:  # noqa: BLE001
        return [], str(e)


def _dedupe_strings(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        if text and text not in seen:
            out.append(text)
            seen.add(text)
    return out


def _capability_preview(items: list[dict], key: str, limit: int = 8) -> str:
    values = [str(item.get(key, "")).strip() for item in items if item.get(key)]
    shown = values[:limit]
    suffix = " ..." if len(values) > limit else ""
    return ", ".join(shown) + suffix


def _render_prompt_content(content) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, dict):
        return str(content)
    ctype = content.get("type", "")
    if ctype == "text":
        return str(content.get("text", ""))
    if ctype == "resource":
        res = content.get("resource") or {}
        return res.get("text") or f"[resource {res.get('uri', '')}]"
    if ctype == "image":
        return "[image content]"
    if ctype == "audio":
        return "[audio content]"
    return f"[{ctype or 'unknown'} content]"


def _looks_like_server_spec(value: object) -> bool:
    return isinstance(value, dict) and any(k in value for k in ("command", "url"))


def _normalize_external_mcp_config(data: object) -> dict:
    """Accept Claude, AEGIS, and common wrapper shapes for mcp.json."""
    if not isinstance(data, dict):
        return {}
    if isinstance(data.get("mcpServers"), dict):
        return data["mcpServers"]
    if isinstance(data.get("mcp"), dict) and isinstance(data["mcp"].get("servers"), dict):
        return data["mcp"]["servers"]
    if isinstance(data.get("servers"), dict) and not _looks_like_server_spec(data["servers"]):
        return data["servers"]
    return data


def build_manager(config) -> MCPManager:
    mgr = MCPManager()
    for name, spec in _server_configs(config).items():
        if not isinstance(spec, dict) or not (spec.get("command") or spec.get("url")):
            # skip malformed entries instead of spamming "no command or url configured"
            continue
        mgr.add(_client_from_spec(name, spec))
    return mgr


def _client_from_spec(name: str, spec: dict) -> MCPClient:
    url = spec.get("url")
    if url is not None:
        url = _validate_remote_mcp_url(name, url)
    return MCPClient(
        name, command=spec.get("command"), args=spec.get("args"),
        env=spec.get("env"), url=url, headers=spec.get("headers"),
        cwd=spec.get("cwd"), tool_filter=spec.get("tool_filter"),
    )


def mcp_tools_from_config(config) -> tuple[list[Tool], MCPManager]:
    if not config.get("mcp.enabled", True):
        return [], MCPManager()       # respect the disable flag
    mgr = build_manager(config)
    return mgr.connect_all(), mgr
