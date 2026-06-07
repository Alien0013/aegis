"""A from-scratch MCP client (JSON-RPC 2.0) over stdio and Streamable HTTP.

Implements the lifecycle: initialize -> notifications/initialized -> tools/list ->
tools/call. Each remote tool is wrapped as an AEGIS ``Tool`` (namespaced
``mcp__<server>__<tool>``) and registered like any built-in.

Config (config.yaml ``mcp.servers`` or ``~/.aegis/mcp.json`` Claude-Desktop format):

    mcp:
      servers:
        filesystem: {command: npx, args: ["-y","@modelcontextprotocol/server-filesystem","/tmp"]}
        remote:     {url: "https://example.com/mcp", headers: {Authorization: "Bearer ..."}}
"""

from __future__ import annotations

import json
import os
import select
import subprocess
from pathlib import Path

import httpx

from .. import config as cfg
from ..tools.base import Tool, ToolContext, ToolResult
from ..util import read_text, truncate

PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "aegis", "version": "0.1.0"}


class MCPError(RuntimeError):
    pass


class MCPClient:
    def __init__(self, name: str, *, command: str | None = None, args: list[str] | None = None,
                 env: dict | None = None, url: str | None = None, headers: dict | None = None,
                 cwd: str | None = None):
        self.name = name
        self.command = command
        self.args = args or []
        self.env = env or {}
        self.url = url
        self.headers = headers or {}
        self.cwd = cwd
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
        env = {**os.environ, **self.env}
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
        while True:
            r, _, _ = select.select([self._proc.stdout], [], [], timeout)
            if not r:
                raise MCPError(f"{self.name}: timed out waiting for response to {payload.get('method')}")
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
                raise MCPError(f"{self.name}: HTTP {r.status_code}: {r.text[:200]}")
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
            raise MCPError(f"{self.name}: {resp['error'].get('message', resp['error'])}")
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

    def list_tools(self) -> list[dict]:
        resp = self._request("tools/list", {})
        return (resp or {}).get("result", {}).get("tools", [])

    def call_tool(self, name: str, arguments: dict) -> tuple[str, bool]:
        resp = self._request("tools/call", {"name": name, "arguments": arguments}, )
        result = (resp or {}).get("result", {})
        parts: list[str] = []
        for block in result.get("content", []):
            if block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif block.get("type") == "resource":
                res = block.get("resource", {})
                parts.append(res.get("text") or f"[resource {res.get('uri')}]")
            else:
                parts.append(f"[{block.get('type')} content]")
        return "\n".join(parts) or "(no content)", bool(result.get("isError"))

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
        self.name = f"mcp__{client.name}__{tool_def['name']}"
        self.description = tool_def.get("description", "") or f"MCP tool {self._remote}"
        self.parameters = tool_def.get("inputSchema") or {"type": "object", "properties": {}}

    def run(self, args, ctx: ToolContext) -> ToolResult:
        try:
            content, is_err = self._client.call_tool(self._remote, args)
        except Exception as e:  # noqa: BLE001
            return ToolResult.error(f"mcp call failed: {e}")
        return ToolResult(content=truncate(content, 30_000), is_error=is_err,
                          display=f"mcp:{self._client.name}/{self._remote}")


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
            except Exception as e:  # noqa: BLE001
                print(f"  ! MCP server '{client.name}' failed: {e}")
        return tools

    def close_all(self) -> None:
        for c in self.clients:
            c.close()


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
        mgr.add(MCPClient(
            name, command=spec.get("command"), args=spec.get("args"),
            env=spec.get("env"), url=spec.get("url"), headers=spec.get("headers"),
            cwd=spec.get("cwd"),
        ))
    return mgr


def mcp_tools_from_config(config) -> tuple[list[Tool], MCPManager]:
    mgr = build_manager(config)
    return mgr.connect_all(), mgr
