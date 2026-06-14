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
from .surface import SurfaceRunner
from .types import Message

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
        self.store = store or SessionStore()
        self.runner = SurfaceRunner(config, store=self.store, include_mcp=True)
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
        try:
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
        finally:
            # stdin closed: release anything still waiting on a client response, then
            # close cached agents so memory/MCP/provider lifecycle hooks fire.
            for w in list(self._waiters.values()):
                w["event"].set()
            try:
                from .surface import _close_agent

                seen: set[int] = set()
                for entry in list(self.sessions.values()):
                    agent = entry.agent
                    if agent is None or id(agent) in seen:
                        continue
                    seen.add(id(agent))
                    _close_agent(agent)
            except Exception:  # noqa: BLE001
                pass
            self.runner.close()

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
                "sessionManagement": {
                    "list": True,
                    "detail": True,
                    "search": True,
                    "fork": True,
                },
                "promptCapabilities": {
                    "image": True,
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
            self._replay_loaded_message(stored.id, m)
        return {}

    def _session_list(self, req_id, params: dict[str, Any]) -> dict[str, Any]:
        limit = _int_param(params, "limit", 30, minimum=1, maximum=200)
        query = str(params.get("query") or params.get("search") or "").strip()
        rows = self.store.search(query, limit=limit) if query else self.store.list(limit=limit)
        return {"sessions": [_session_row(row, store=self.store) for row in rows]}

    def _session_get(self, req_id, params: dict[str, Any]) -> dict[str, Any]:
        sid = params.get("sessionId") or params.get("id")
        session = self.store.load(sid) if isinstance(sid, str) else None
        if session is None:
            raise _RpcError(INVALID_PARAMS, f"unknown sessionId: {sid!r}")
        return {"session": _session_detail(session, store=self.store)}

    def _session_fork(self, req_id, params: dict[str, Any]) -> dict[str, Any]:
        sid = params.get("sessionId") or params.get("id")
        parent = self.store.load(sid) if isinstance(sid, str) else None
        if parent is None:
            raise _RpcError(INVALID_PARAMS, f"unknown sessionId: {sid!r}")
        child = self.store.fork(parent)
        title = str(params.get("title") or "").strip()
        if title:
            child.title = title[:120]
            self.store.save(child)
        cwd = Path(params.get("cwd") or Path.cwd()).expanduser()
        self.sessions[child.id] = _SessionEntry(session=child, cwd=cwd)
        return {
            "sessionId": child.id,
            "parentSessionId": parent.id,
            "session": _session_detail(child, store=self.store),
        }

    def _session_prompt(self, req_id, params: dict[str, Any]):
        sid = params.get("sessionId")
        entry = self.sessions.get(sid) if isinstance(sid, str) else None
        if entry is None:
            raise _RpcError(INVALID_PARAMS, f"unknown sessionId: {sid!r}")
        if entry.busy:
            raise _RpcError(INVALID_REQUEST, "a prompt is already running for this session")

        text = _flatten_prompt(params.get("prompt"))
        images = _prompt_images(params.get("prompt"))
        if not text.strip() and not images:
            raise _RpcError(INVALID_PARAMS, "empty prompt")
        prompt = Message.user(text, images=images) if images else text

        entry.busy = True
        threading.Thread(target=self._run_prompt, args=(req_id, sid, entry, prompt),
                         daemon=True).start()
        return _NO_REPLY              # the worker sends the response when the turn ends

    def _run_prompt(self, req_id, sid: str, entry: _SessionEntry, prompt: str | Message) -> None:
        try:
            agent = entry.agent
            if agent is None or agent.session is not entry.session:
                agent = self.runner.make_agent(
                    session=entry.session,
                    cwd=entry.cwd,
                    approver=lambda desc: self._request_permission(sid, desc),
                    include_mcp=True,
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

            result = self.runner.run_prompt(
                prompt,
                session=entry.session,
                agent=agent,
                surface="acp",
                meta={"acp_session_id": sid},
                on_event=on_event,
            )
            result_session = getattr(result, "session", None)
            result_sid = str(getattr(result_session, "id", "") or "")
            if result_session is not None and result_sid:
                entry.session = result_session
                self.sessions[result_sid] = entry
                entry.agent = getattr(result, "agent", agent)
            if agent.cancel_event.is_set():
                self._result(req_id, _prompt_result("cancelled", result))
                return
            # Streaming providers already emitted deltas; if nothing streamed send it once.
            if not getattr(agent, "stream", True) and result.text:
                self._send_chunk(sid, result.text)
            self._result(req_id, _prompt_result("end_turn", result))
        except Exception as e:  # noqa: BLE001
            self._send_chunk(sid, f"[error] {type(e).__name__}: {e}")
            self._result(req_id, {"stopReason": "error"})
        finally:
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

    def _send_thought(self, sid: str, text: str) -> None:
        if text and text.strip():
            self._update(sid, {"sessionUpdate": "agent_thought_chunk",
                               "content": {"type": "text", "text": text}})

    def _replay_loaded_message(self, sid: str, message: Message) -> None:
        """Replay a persisted message into ACP updates when session/load restores history."""
        if message.role == "user":
            self._update(sid, {"sessionUpdate": "user_message_chunk",
                               "content": {"type": "text", "text": message.content}})
            return
        if message.role == "assistant":
            self._send_thought(sid, str(getattr(message, "reasoning", "") or ""))
            if message.content:
                self._update(sid, {"sessionUpdate": "agent_message_chunk",
                                   "content": {"type": "text", "text": message.content}})
            for tc in message.tool_calls or []:
                args = tc.arguments if isinstance(tc.arguments, dict) else {}
                raw_input = {k: v for k, v in args.items() if isinstance(v, (str, int, bool, float))}
                update: dict[str, Any] = {
                    "sessionUpdate": "tool_call",
                    "toolCallId": str(tc.id or tc.name),
                    "title": tc.name,
                    "status": "in_progress",
                    "kind": _TOOL_KIND.get(tc.name, "other"),
                }
                if raw_input:
                    update["rawInput"] = raw_input
                self._update(sid, update)
            return
        if message.role == "tool":
            name = message.name or "tool"
            update: dict[str, Any] = {
                "sessionUpdate": "tool_call_update",
                "toolCallId": str(message.tool_call_id or name),
                "title": name,
                "status": "completed",
                "kind": _TOOL_KIND.get(name, "other"),
            }
            if message.content:
                update["content"] = [{"type": "text", "text": str(message.content)[:4000]}]
            self._update(sid, update)

    def _request_permission(self, sid: str, description: str) -> bool | str:
        """Ask the editor to approve a tool action (runs on the prompt worker)."""
        result = self._rpc_call("session/request_permission", {
            "sessionId": sid,
            "toolCall": {"toolCallId": f"perm-{self._req_id}", "title": description or "tool action",
                         "kind": "other", "status": "pending"},
            "options": [{"optionId": "allow", "name": "Allow once", "kind": "allow_once"},
                        {"optionId": "allow_session", "name": "Allow this session",
                         "kind": "allow_session"},
                        {"optionId": "reject", "name": "Reject", "kind": "reject_once"}],
        })
        outcome = (result or {}).get("outcome") or {}
        if outcome.get("outcome") != "selected":
            return False
        option_id = outcome.get("optionId")
        if option_id == "allow_session":
            return "always"
        return option_id == "allow"

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
        elif status == "completed":
            preview = event.get("preview") or event.get("summary") or ""
            if preview:
                update["content"] = [{"type": "text", "text": str(preview)}]
            if event.get("duration_ms") is not None:
                update["metadata"] = {"duration_ms": event.get("duration_ms"),
                                      "classification": event.get("classification", "")}
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
        "session/list": _session_list,
        "session/get": _session_get,
        "session/detail": _session_get,
        "session/search": _session_list,
        "session/fork": _session_fork,
        "session/branch": _session_fork,
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


def _prompt_images(prompt: Any) -> list[str]:
    if not isinstance(prompt, list):
        return []
    images: list[str] = []
    for block in prompt:
        if not isinstance(block, dict):
            continue
        image = None
        btype = block.get("type")
        if btype in {"image", "image_url", "input_image"}:
            image = block.get("image_url") or block.get("image") or block.get("url")
        elif btype == "resource":
            res = block.get("resource") or {}
            mime = str(res.get("mimeType") or res.get("mime_type") or "")
            if mime.startswith("image/"):
                image = res.get("uri") or res.get("blob") or res.get("data")
        if isinstance(image, dict):
            image = image.get("url") or image.get("uri")
        if image:
            images.append(str(image))
    return images


def _int_param(
    params: dict[str, Any],
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    try:
        value = int(params.get(name, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _prompt_meta(session: Session) -> dict[str, Any]:
    parts = [p for p in (session.meta.get("prompt_parts") or []) if isinstance(p, dict)]
    last_refs = (session.meta.get("last_context_references")
                 if isinstance(session.meta.get("last_context_references"), dict) else {})
    ref_history = [r for r in (session.meta.get("context_references") or []) if isinstance(r, dict)]
    return {
        "hash": session.meta.get("system_prompt_hash", ""),
        "tokens": int(session.meta.get("system_prompt_tokens", 0) or 0),
        "chars": int(session.meta.get("system_prompt_chars", 0) or 0),
        "parts": len(parts),
        "contextReferences": _jsonable(last_refs),
        "contextReferenceHistory": _jsonable(ref_history[-10:]),
    }


def _prompt_result(stop_reason: str, result: Any | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"stopReason": stop_reason}
    if result is None:
        return payload
    session = getattr(result, "session", None)
    session_id = getattr(session, "id", "") if session is not None else ""
    if session_id:
        payload["sessionId"] = session_id
    for attr, key in (
        ("run_id", "runId"),
        ("trace_id", "traceId"),
        ("turn_id", "turnId"),
    ):
        value = str(getattr(result, attr, "") or "")
        if value:
            payload[key] = value
    return payload


def _session_row(row: dict[str, Any], *, store: SessionStore) -> dict[str, Any]:
    sid = str(row.get("id") or "")
    session = store.load(sid) if sid else None
    runtime = (session.meta.get("runtime") if session else {}) or {}
    meta = session.meta if session else {}
    messages = session.messages if session else []
    return {
        "sessionId": sid,
        "id": sid,
        "title": row.get("title", ""),
        "createdAt": row.get("created_at", ""),
        "updatedAt": row.get("updated_at", ""),
        "parentSessionId": row.get("parent_id"),
        "messageCount": len([m for m in messages if m.role in {"user", "assistant"}]),
        "runtime": runtime,
        "traceId": meta.get("trace_id", ""),
        "prompt": _prompt_meta(session) if session else {},
    }


def _message_block(message) -> dict[str, Any]:
    return {
        "role": message.role,
        "content": [{"type": "text", "text": message.content or ""}],
    }


def _jsonable(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except TypeError:
        if isinstance(value, dict):
            return {str(k): _jsonable(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [_jsonable(v) for v in value]
        return str(value)


def _session_detail(session: Session, *, store: SessionStore) -> dict[str, Any]:
    return {
        "sessionId": session.id,
        "id": session.id,
        "title": session.title,
        "createdAt": session.created_at,
        "updatedAt": session.updated_at,
        "parentSessionId": session.parent_id,
        "children": store.children(session.id),
        "messages": [_message_block(m) for m in session.messages if m.role != "system"],
        "runtime": session.meta.get("runtime") or {},
        "runtimeControls": session.meta.get("runtime_controls") or {},
        "traceId": session.meta.get("trace_id", ""),
        "prompt": _prompt_meta(session),
        "summary": session.meta.get("summary", ""),
    }


def run_acp(config: Config) -> None:
    """Run the ACP stdio server until stdin is closed (blocking)."""
    server = AcpServer(config=config, store=SessionStore())
    server.serve()


def cmd_acp(args, config: Config) -> int:
    """CLI entrypoint: ``aegis acp`` — serve ACP over stdio for an IDE."""
    run_acp(config)
    return 0
