"""Agent Client Protocol (ACP) stdio server for IDE integration (Zed).

Implements a minimal but correct subset of Zed's Agent Client Protocol over
newline-delimited JSON-RPC 2.0 on stdin/stdout. The editor (client) launches
``aegis acp`` as a subprocess and speaks JSON-RPC; we drive an :class:`Agent`
per session and stream assistant text back as ``session/update`` notifications.

Wire protocol (one JSON object per line, ``\\n`` terminated)::

    --> {"jsonrpc":"2.0","id":1,"method":"initialize","params":{...}}
    <-- {"jsonrpc":"2.0","id":1,"result":{"protocolVersion":1,"agentCapabilities":{...}}}
    --> {"jsonrpc":"2.0","id":2,"method":"session/new","params":{"cwd":"/p","mcpServers":[]}}
    <-- {"jsonrpc":"2.0","id":2,"result":{"sessionId":"sess_..."}}
    --> {"jsonrpc":"2.0","id":3,"method":"session/prompt","params":{"sessionId":"sess_...","prompt":[...]}}
    <-- {"jsonrpc":"2.0","method":"session/update","params":{"sessionId":"...","update":{...}}}   (0..n)
    <-- {"jsonrpc":"2.0","id":3,"result":{"stopReason":"end_turn"}}

The implementation is intentionally synchronous: prompts are processed one at a
time on the read loop, streaming updates out as the agent produces deltas.
"""

from __future__ import annotations

import json
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

from .agent.agent import Agent
from .config import Config
from .session import Session, SessionStore

# ACP protocol version we implement (Zed currently negotiates integer versions).
PROTOCOL_VERSION = 1

# JSON-RPC 2.0 error codes.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


@dataclass
class _SessionEntry:
    """A live ACP session: the AEGIS session plus its working directory."""

    session: Session
    cwd: Path


@dataclass
class _AcpFs:
    """Filesystem delegate that reads/writes through the ACP client (editor) so unsaved buffers
    are honored, instead of touching local disk. Used only when the client advertises fs caps."""

    def __init__(self, server: "AcpServer", sid: str):
        self._server = server
        self._sid = sid

    def read_text(self, path: str) -> str:
        r = self._server._rpc_call("fs/read_text_file", {"sessionId": self._sid, "path": path})
        return r.get("content", "")

    def write_text(self, path: str, content: str) -> None:
        self._server._rpc_call("fs/write_text_file",
                               {"sessionId": self._sid, "path": path, "content": content})


class AcpServer:
    """JSON-RPC 2.0 over stdio implementing the Agent Client Protocol."""

    config: Config
    stdin: TextIO = field(default_factory=lambda: sys.stdin)
    stdout: TextIO = field(default_factory=lambda: sys.stdout)
    store: SessionStore | None = None

    sessions: dict[str, _SessionEntry] = field(default_factory=dict)
    _write_lock: threading.Lock = field(default_factory=threading.Lock)
    _req_id: int = 0
    _client_fs: bool = False    # client advertised fs/read_text_file + fs/write_text_file

    # -- low-level framing --------------------------------------------------
    def _write(self, obj: dict[str, Any]) -> None:
        """Serialize one JSON-RPC message as a single newline-delimited line."""
        line = json.dumps(obj, ensure_ascii=False)
        with self._write_lock:
            self.stdout.write(line + "\n")
            self.stdout.flush()

    def _result(self, req_id: Any, result: Any) -> None:
        self._write({"jsonrpc": "2.0", "id": req_id, "result": result})

    def _error(self, req_id: Any, code: int, message: str) -> None:
        self._write({"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}})

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    # -- main loop ----------------------------------------------------------
    def serve(self) -> None:
        """Read JSON-RPC messages line by line until stdin closes."""
        for raw in self.stdin:
            line = raw.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                self._error(None, PARSE_ERROR, "invalid JSON")
                continue
            self._handle(msg)

    def _handle(self, msg: dict[str, Any]) -> None:
        req_id = msg.get("id")
        method = msg.get("method")
        params = msg.get("params") or {}
        # Responses/notifications coming from the client (no method) are ignored.
        if not isinstance(method, str):
            return
        try:
            handler = self._METHODS.get(method)
            if handler is None:
                if req_id is not None:
                    self._error(req_id, METHOD_NOT_FOUND, f"unknown method: {method}")
                return
            result = handler(self, params)
            if req_id is not None:
                self._result(req_id, result)
        except _RpcError as e:
            if req_id is not None:
                self._error(req_id, e.code, e.message)
        except Exception as e:  # noqa: BLE001
            if req_id is not None:
                self._error(req_id, INTERNAL_ERROR, f"{type(e).__name__}: {e}")

    # -- method handlers ----------------------------------------------------
    def _initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        # Echo the lower of the client's requested version and ours; default to ours.
        client_version = params.get("protocolVersion", PROTOCOL_VERSION)
        try:
            version = min(int(client_version), PROTOCOL_VERSION)
        except (TypeError, ValueError):
            version = PROTOCOL_VERSION
        fs_caps = ((params.get("clientCapabilities") or {}).get("fs") or {})
        self._client_fs = bool(fs_caps.get("readTextFile") and fs_caps.get("writeTextFile"))
        return {
            "protocolVersion": version,
            "agentCapabilities": {
                "loadSession": False,
                "promptCapabilities": {
                    "image": False,
                    "audio": False,
                    "embeddedContext": True,
                },
            },
            "authMethods": [],
        }

    def _session_new(self, params: dict[str, Any]) -> dict[str, Any]:
        cwd = Path(params.get("cwd") or Path.cwd()).expanduser()
        session = Session.create()
        self.sessions[session.id] = _SessionEntry(session=session, cwd=cwd)
        return {"sessionId": session.id}

    def _session_prompt(self, params: dict[str, Any]) -> dict[str, Any]:
        sid = params.get("sessionId")
        entry = self.sessions.get(sid) if isinstance(sid, str) else None
        if entry is None:
            raise _RpcError(INVALID_PARAMS, f"unknown sessionId: {sid!r}")

        text = _flatten_prompt(params.get("prompt"))
        if not text.strip():
            raise _RpcError(INVALID_PARAMS, "empty prompt")

        agent = Agent.create(
            self.config,
            session=entry.session,
            cwd=entry.cwd,
            store=self.store,
            approver=lambda desc: self._request_permission(sid, desc),
        )
        if self._client_fs:                      # route file reads/writes through the editor
            agent.tool_context.fs = _AcpFs(self, sid)

        def on_event(event: dict[str, Any]) -> None:
            etype = event.get("type")
            if etype == "assistant_delta":
                self._send_chunk(sid, event.get("text", ""))
            elif etype == "tool_start":            # surface tool activity in the editor (Zed etc.)
                self._send_tool_call(sid, event, status="in_progress")
            elif etype == "tool_result":
                self._send_tool_call(sid, event, status="completed")

        try:
            result = agent.run(text, on_event)
        except Exception as e:  # noqa: BLE001
            self._notify(
                "session/update",
                {
                    "sessionId": sid,
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": f"[error] {type(e).__name__}: {e}"},
                    },
                },
            )
            return {"stopReason": "error"}

        # Streaming providers already emitted deltas; if nothing streamed (e.g. a
        # non-streaming provider) send the full message once so the editor sees it.
        if not getattr(agent, "stream", True) and result.content:
            self._send_chunk(sid, result.content)

        return {"stopReason": "end_turn"}

    def _session_cancel(self, params: dict[str, Any]) -> dict[str, Any]:
        # We process prompts synchronously, so by the time a cancel arrives the
        # turn is already done. Acknowledge so the client doesn't hang.
        return {}

    def _authenticate(self, params: dict[str, Any]) -> dict[str, Any]:
        # No auth required for the local stdio agent.
        return {}

    def _send_chunk(self, sid: str, text: str) -> None:
        if not text:
            return
        self._notify(
            "session/update",
            {
                "sessionId": sid,
                "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": text},
                },
            },
        )

    def _rpc_call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Send a request to the client and block for its response (reading stdin, which is
        idle while the serve loop is suspended inside agent.run()). Returns the result dict."""
        self._req_id += 1
        rid = f"req-{self._req_id}"
        self._write({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        for raw in self.stdin:
            line = raw.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("id") == rid:
                return msg.get("result") or {}
        return {}

    def _request_permission(self, sid: str, description: str) -> bool:
        """Ask the editor to approve a tool action (ACP session/request_permission). Blocks
        reading stdin for the matching response — safe because the serve loop is suspended
        up-stack inside agent.run() while this fires."""
        self._req_id += 1
        rid = f"perm-{self._req_id}"
        self._write({
            "jsonrpc": "2.0", "id": rid, "method": "session/request_permission",
            "params": {
                "sessionId": sid,
                "toolCall": {"toolCallId": rid, "title": description or "tool action",
                             "kind": "other", "status": "pending"},
                "options": [{"optionId": "allow", "name": "Allow", "kind": "allow_once"},
                            {"optionId": "reject", "name": "Reject", "kind": "reject_once"}],
            },
        })
        for raw in self.stdin:
            line = raw.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("id") == rid:
                outcome = (msg.get("result") or {}).get("outcome") or {}
                return outcome.get("outcome") == "selected" and outcome.get("optionId") == "allow"
            if msg.get("method") in ("session/cancel", "$/cancelRequest"):
                return False          # client cancelled while we waited -> treat as deny
        return False                  # stdin closed -> deny

    def _send_tool_call(self, sid: str, event: dict[str, Any], *, status: str) -> None:
        name = event.get("name") or "tool"
        update: dict[str, Any] = {
            "sessionUpdate": "tool_call",
            "toolCallId": str(event.get("id") or name),
            "title": event.get("summary") or name,
            "status": "failed" if event.get("is_error") else status,
            "kind": "other",
        }
        self._notify("session/update", {"sessionId": sid, "update": update})

    _METHODS = {
        "initialize": _initialize,
        "authenticate": _authenticate,
        "session/new": _session_new,
        "session/prompt": _session_prompt,
        "session/cancel": _session_cancel,
    }


class _RpcError(Exception):
    """Maps to a JSON-RPC error response with a specific code."""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _flatten_prompt(prompt: Any) -> str:
    """Collapse an ACP prompt (list of content blocks, or a raw string) to text.

    ACP sends ``prompt`` as a list of content blocks. We extract ``text`` blocks
    and the ``text``/``uri`` of embedded ``resource`` / ``resource_link`` blocks;
    other block types (image/audio) are skipped since we advertise no support.
    """
    if isinstance(prompt, str):
        return prompt
    if not isinstance(prompt, list):
        return ""
    parts: list[str] = []
    for block in prompt:
        if isinstance(block, str):
            parts.append(block)
            continue
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            parts.append(str(block.get("text", "")))
        elif btype == "resource_link":
            uri = block.get("uri", "")
            if uri:
                parts.append(f"@{uri}")
        elif btype == "resource":
            res = block.get("resource") or {}
            txt = res.get("text")
            if txt:
                uri = res.get("uri", "")
                header = f"<context uri=\"{uri}\">\n" if uri else "<context>\n"
                parts.append(f"{header}{txt}\n</context>")
    return "\n".join(p for p in parts if p)


def run_acp(config: Config) -> None:
    """Run the ACP stdio server until stdin is closed (blocking)."""
    server = AcpServer(config=config, store=SessionStore())
    server.serve()


def cmd_acp(args, config: Config) -> int:
    """CLI entrypoint: ``aegis acp`` — serve ACP over stdio for an IDE."""
    run_acp(config)
    return 0
