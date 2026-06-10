"""Agent Client Protocol (ACP) stdio server for IDE integration (Zed et al.).

Speaks newline-delimited JSON-RPC 2.0 on stdin/stdout. The editor launches
``aegis acp`` as a subprocess; we drive one :class:`Agent` per session and
stream updates back as ``session/update`` notifications.

Architecture: the serve loop is the **only** stdin reader. Prompts run on a
worker thread (one per session at a time) so the loop keeps reading while a
turn is in flight — that's what makes ``session/cancel`` actually interrupt a
run, and lets client responses (permission outcomes, fs results) route back to
whichever call is waiting.

Editor-facing niceties:
  - file edits are sent as ACP ``diff`` content blocks so the editor renders a
    proper before/after review,
  - ``session/load`` replays a stored session's history,
  - tool calls carry an ACP ``kind`` (read/edit/execute/fetch) and locations.
"""

from __future__ import annotations

import json
import sys
import threading
from dataclasses import dataclass
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

_NO_REPLY = object()    # handler sentinel: the response is sent later (async prompt)

_TOOL_KIND = {"read_file": "read", "list_dir": "read", "grep": "search", "glob": "search",
              "write_file": "edit", "edit_file": "edit", "bash": "execute",
              "execute_code": "execute", "web_search": "fetch", "web_fetch": "fetch"}


@dataclass
class _SessionEntry:
    """A live ACP session: the AEGIS session plus its working directory."""

    session: Session
    cwd: Path
    agent: Agent | None = None        # set while a prompt is running (for cancel)
    busy: bool = False


class _AcpFs:
    """Filesystem delegate that reads/writes through the ACP client (editor) so unsaved
    buffers are honored, instead of touching local disk."""

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

    def __init__(self, config: Config, stdin: TextIO | None = None,
                 stdout: TextIO | None = None, store: SessionStore | None = None):
        self.config = config
        self.stdin = stdin or sys.stdin
        self.stdout = stdout or sys.stdout
        self.store = store
        self.sessions: dict[str, _SessionEntry] = {}
        self._write_lock = threading.Lock()
        self._req_id = 0
        self._client_fs = False     # client advertised fs/read_text_file + fs/write_text_file
        self._waiters: dict[Any, dict] = {}   # our request id -> {event, result}

    # -- low-level framing --------------------------------------------------
    def _write(self, obj: dict[str, Any]) -> None:
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

    def _update(self, sid: str, update: dict[str, Any]) -> None:
        self._notify("session/update", {"sessionId": sid, "update": update})

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
        # stdin closed: release anything still waiting on a client response
        for w in list(self._waiters.values()):
            w["event"].set()

    def _handle(self, msg: dict[str, Any]) -> None:
        req_id = msg.get("id")
        method = msg.get("method")
        if not isinstance(method, str):
            # response from the client -> route to the waiting call
            if req_id is not None and req_id in self._waiters:
                w = self._waiters.pop(req_id)
                w["result"] = msg.get("result") or {}
                w["event"].set()
            return
        try:
            handler = self._METHODS.get(method)
            if handler is None:
                if req_id is not None:
                    self._error(req_id, METHOD_NOT_FOUND, f"unknown method: {method}")
                return
            result = handler(self, req_id, params=msg.get("params") or {})
            if req_id is not None and result is not _NO_REPLY:
                self._result(req_id, result)
        except _RpcError as e:
            if req_id is not None:
                self._error(req_id, e.code, e.message)
        except Exception as e:  # noqa: BLE001
            if req_id is not None:
                self._error(req_id, INTERNAL_ERROR, f"{type(e).__name__}: {e}")

    # -- client calls (worker thread -> editor) ------------------------------
    def _rpc_call(self, method: str, params: dict[str, Any], timeout: float = 300.0) -> dict[str, Any]:
        """Send a request to the client and wait for the serve loop to route the response."""
        with self._write_lock:
            self._req_id += 1
            rid = f"req-{self._req_id}"
        waiter = {"event": threading.Event(), "result": {}, "sid": params.get("sessionId")}
        self._waiters[rid] = waiter
        self._write({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        waiter["event"].wait(timeout)
        self._waiters.pop(rid, None)
        return waiter["result"]

    # -- method handlers ----------------------------------------------------
    def _initialize(self, req_id, params: dict[str, Any]) -> dict[str, Any]:
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
                "loadSession": True,
                "promptCapabilities": {
                    "image": False,
                    "audio": False,
                    "embeddedContext": True,
                },
            },
            "authMethods": [],
        }

    def _session_new(self, req_id, params: dict[str, Any]) -> dict[str, Any]:
        cwd = Path(params.get("cwd") or Path.cwd()).expanduser()
        session = Session.create()
        self.sessions[session.id] = _SessionEntry(session=session, cwd=cwd)
        return {"sessionId": session.id}

    def _session_load(self, req_id, params: dict[str, Any]) -> dict[str, Any]:
        """Restore a stored session and replay its history as updates."""
        sid = params.get("sessionId")
        stored = self.store.load(sid) if (self.store and isinstance(sid, str)) else None
        if stored is None:
            raise _RpcError(INVALID_PARAMS, f"unknown sessionId: {sid!r}")
        cwd = Path(params.get("cwd") or Path.cwd()).expanduser()
        self.sessions[stored.id] = _SessionEntry(session=stored, cwd=cwd)
        for m in stored.messages:
            if m.role == "user":
                self._update(stored.id, {"sessionUpdate": "user_message_chunk",
                                         "content": {"type": "text", "text": m.content}})
            elif m.role == "assistant" and m.content:
                self._update(stored.id, {"sessionUpdate": "agent_message_chunk",
                                         "content": {"type": "text", "text": m.content}})
        return {}

    def _session_prompt(self, req_id, params: dict[str, Any]):
        sid = params.get("sessionId")
        entry = self.sessions.get(sid) if isinstance(sid, str) else None
        if entry is None:
            raise _RpcError(INVALID_PARAMS, f"unknown sessionId: {sid!r}")
        if entry.busy:
            raise _RpcError(INVALID_REQUEST, "a prompt is already running for this session")

        text = _flatten_prompt(params.get("prompt"))
        if not text.strip():
            raise _RpcError(INVALID_PARAMS, "empty prompt")

        entry.busy = True
        threading.Thread(target=self._run_prompt, args=(req_id, sid, entry, text),
                         daemon=True).start()
        return _NO_REPLY              # the worker sends the response when the turn ends

    def _run_prompt(self, req_id, sid: str, entry: _SessionEntry, text: str) -> None:
        try:
            agent = Agent.create(
                self.config,
                session=entry.session,
                cwd=entry.cwd,
                store=self.store,
                approver=lambda desc: self._request_permission(sid, desc),
            )
            entry.agent = agent
            if self._client_fs:              # route file reads/writes through the editor
                agent.tool_context.fs = _AcpFs(self, sid)

            def on_event(event: dict[str, Any]) -> None:
                etype = event.get("type")
                if etype == "assistant_delta":
                    self._send_chunk(sid, event.get("text", ""))
                elif etype == "tool_start":
                    self._send_tool_call(sid, entry, event, status="in_progress")
                elif etype == "tool_result":
                    self._send_tool_call(sid, entry, event, status="completed")

            result = agent.run(text, on_event)
            if agent.cancel_event.is_set():
                self._result(req_id, {"stopReason": "cancelled"})
                return
            # Streaming providers already emitted deltas; if nothing streamed send it once.
            if not getattr(agent, "stream", True) and result.content:
                self._send_chunk(sid, result.content)
            self._result(req_id, {"stopReason": "end_turn"})
        except Exception as e:  # noqa: BLE001
            self._send_chunk(sid, f"[error] {type(e).__name__}: {e}")
            self._result(req_id, {"stopReason": "error"})
        finally:
            entry.agent = None
            entry.busy = False

    def _session_cancel(self, req_id, params: dict[str, Any]) -> dict[str, Any]:
        sid = params.get("sessionId")
        entry = self.sessions.get(sid) if isinstance(sid, str) else None
        if entry is not None and entry.agent is not None:
            entry.agent.cancel()             # the worker replies with stopReason: cancelled
        # release any call waiting on this session's client (permission prompt etc.) as a deny
        for rid, w in list(self._waiters.items()):
            if w.get("sid") == sid:
                self._waiters.pop(rid, None)
                w["event"].set()
        return {}

    def _authenticate(self, req_id, params: dict[str, Any]) -> dict[str, Any]:
        return {}                            # no auth required for the local stdio agent

    # -- updates ------------------------------------------------------------
    def _send_chunk(self, sid: str, text: str) -> None:
        if text:
            self._update(sid, {"sessionUpdate": "agent_message_chunk",
                               "content": {"type": "text", "text": text}})

    def _request_permission(self, sid: str, description: str) -> bool:
        """Ask the editor to approve a tool action (runs on the prompt worker)."""
        result = self._rpc_call("session/request_permission", {
            "sessionId": sid,
            "toolCall": {"toolCallId": f"perm-{self._req_id}", "title": description or "tool action",
                         "kind": "other", "status": "pending"},
            "options": [{"optionId": "allow", "name": "Allow", "kind": "allow_once"},
                        {"optionId": "reject", "name": "Reject", "kind": "reject_once"}],
        })
        outcome = (result or {}).get("outcome") or {}
        return outcome.get("outcome") == "selected" and outcome.get("optionId") == "allow"

    def _send_tool_call(self, sid: str, entry: _SessionEntry, event: dict[str, Any],
                        *, status: str) -> None:
        name = event.get("name") or "tool"
        update: dict[str, Any] = {
            "sessionUpdate": "tool_call" if status == "in_progress" else "tool_call_update",
            "toolCallId": str(event.get("id") or name),
            "title": event.get("summary") or name,
            "status": "failed" if event.get("is_error") else status,
            "kind": _TOOL_KIND.get(name, "other"),
        }
        args = event.get("args") or {}
        if status == "in_progress" and isinstance(args, dict):
            update["rawInput"] = {k: v for k, v in args.items() if isinstance(v, (str, int, bool))}
            diff = self._edit_diff(entry, name, args)
            if diff is not None:
                update["content"] = [diff]
            if isinstance(args.get("path"), str):
                update["locations"] = [{"path": str((entry.cwd / args["path"]).resolve()
                                                    if not Path(args["path"]).is_absolute()
                                                    else args["path"])}]
        self._update(sid, update)

    @staticmethod
    def _edit_diff(entry: _SessionEntry, name: str, args: dict[str, Any]) -> dict[str, Any] | None:
        """ACP diff content block for file edits, so editors render a before/after review."""
        raw = args.get("path")
        if not isinstance(raw, str) or not raw:
            return None
        path = Path(raw)
        if not path.is_absolute():
            path = entry.cwd / path
        try:
            old_text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
        except OSError:
            old_text = ""
        if name == "write_file" and isinstance(args.get("content"), str):
            return {"type": "diff", "path": str(path), "oldText": old_text,
                    "newText": args["content"]}
        if name == "edit_file" and isinstance(args.get("old_string"), str):
            new, old = str(args.get("new_string", "")), str(args["old_string"])
            replaced = (old_text.replace(old, new)
                        if args.get("replace_all") else old_text.replace(old, new, 1))
            return {"type": "diff", "path": str(path), "oldText": old_text, "newText": replaced}
        return None

    _METHODS = {
        "initialize": _initialize,
        "authenticate": _authenticate,
        "session/new": _session_new,
        "session/load": _session_load,
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
    """Collapse an ACP prompt (list of content blocks, or a raw string) to text."""
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
