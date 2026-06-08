"""Codex CLI app-server transport.

This is the subscription-auth runtime path. Instead of calling OpenAI's API or
ChatGPT backend HTTP endpoints directly, AEGIS delegates the model turn to the
local ``codex app-server`` process. Codex owns ChatGPT/API-key authentication,
native shell/file tooling, approvals, and event streaming; AEGIS bridges its
own tools through Codex dynamic tools.
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import shutil
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

from ..tools.base import ToolResult
from ..types import LLMResponse, Message, ToolCall, ToolSchema
from .auth import AuthProvider
from .base import ApiMode, ApprovalHandler, OnDelta, ProviderTransport, ToolRunner


class CodexAppServerError(RuntimeError):
    """Raised when the local Codex app-server cannot complete a turn."""

    def __init__(self, message: str, *, code: int | None = None, data: Any = None):
        self.code = code
        self.data = data
        super().__init__(message)


class _CodexAppServerClient:
    def __init__(self, command: str = "codex", env: dict[str, str] | None = None) -> None:
        if shutil.which(command) is None:
            raise CodexAppServerError(
                "Codex CLI not found. Install with `npm i -g @openai/codex`, "
                "then run `codex login`."
            )
        spawn_env = os.environ.copy()
        if env:
            spawn_env.update(env)
        spawn_env.setdefault("RUST_LOG", "warn")
        self.proc = subprocess.Popen(
            [command, "app-server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=spawn_env,
        )
        self._next_id = 1
        self._incoming: queue.Queue[dict[str, Any]] = queue.Queue()
        self._backlog: deque[dict[str, Any]] = deque()
        self._stderr: deque[str] = deque(maxlen=60)
        self._closed = False
        self._stdout_thread = threading.Thread(target=self._read_stdout, daemon=True)
        self._stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
        self._stdout_thread.start()
        self._stderr_thread.start()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self.proc.stdin and not self.proc.stdin.closed:
                self.proc.stdin.close()
        except Exception:
            pass
        try:
            self.proc.terminate()
            self.proc.wait(timeout=2)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass

    def is_alive(self) -> bool:
        return self.proc.poll() is None

    def stderr_tail(self) -> str:
        return "\n".join(self._stderr)

    def initialize(self) -> None:
        self.request(
            "initialize",
            {
                "clientInfo": {
                    "name": "aegis",
                    "title": "AEGIS",
                    "version": "0.1.0",
                },
                "capabilities": {"experimentalApi": True},
            },
            timeout=15,
        )
        self.notify("initialized", {})

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        self._send({"method": method, "params": params or {}})

    def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float = 30.0,
        server_request_handler=None,
    ) -> dict[str, Any]:
        request_id = self._next_id
        self._next_id += 1
        self._send({"id": request_id, "method": method, "params": params or {}})
        deadline = time.monotonic() + timeout
        deferred: list[dict[str, Any]] = []
        while time.monotonic() < deadline:
            msg = self.take_message(timeout=max(0.05, min(0.25, deadline - time.monotonic())))
            if msg is None:
                if not self.is_alive():
                    tail = self.stderr_tail()
                    detail = f":\n{tail}" if tail else ""
                    raise CodexAppServerError(
                        f"codex app-server exited while waiting for {method!r}{detail}"
                    )
                continue
            if msg.get("id") == request_id:
                for deferred_msg in reversed(deferred):
                    self._backlog.appendleft(deferred_msg)
                if "error" in msg:
                    err = msg.get("error") or {}
                    raise CodexAppServerError(
                        str(err.get("message") or f"{method} failed"),
                        code=err.get("code"),
                        data=err.get("data"),
                    )
                result = msg.get("result")
                return result if isinstance(result, dict) else {}
            if "id" in msg and "method" in msg and server_request_handler is not None:
                server_request_handler(msg)
            else:
                deferred.append(msg)
        raise CodexAppServerError(f"codex app-server method {method!r} timed out")

    def take_message(self, timeout: float = 0.0) -> dict[str, Any] | None:
        if self._backlog:
            return self._backlog.popleft()
        try:
            return self._incoming.get(timeout=timeout)
        except queue.Empty:
            return None

    def respond(self, request_id: Any, result: dict[str, Any] | None = None) -> None:
        self._send({"id": request_id, "result": result or {}})

    def respond_error(self, request_id: Any, message: str, code: int = -32603) -> None:
        self._send({"id": request_id, "error": {"code": code, "message": message}})

    def _send(self, message: dict[str, Any]) -> None:
        if self.proc.stdin is None or self.proc.stdin.closed:
            raise CodexAppServerError("codex app-server stdin is closed")
        self.proc.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
        self.proc.stdin.flush()

    def _read_stdout(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            raw = line.strip()
            if not raw:
                continue
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                self._stderr.append(f"unparseable stdout: {raw[:500]}")
                continue
            if isinstance(msg, dict):
                self._incoming.put(msg)

    def _read_stderr(self) -> None:
        assert self.proc.stderr is not None
        for line in self.proc.stderr:
            stripped = line.rstrip()
            if stripped:
                self._stderr.append(stripped)


class CodexAppServerTransport(ProviderTransport):
    api_mode = ApiMode.CODEX_APP_SERVER

    def __init__(self, command: str = "codex") -> None:
        self.command = command
        self._client: _CodexAppServerClient | None = None
        self._thread_id: str | None = None
        self._fingerprint: str | None = None

    def complete(
        self,
        *,
        base_url: str,
        auth: AuthProvider,
        model: str,
        messages: list[Message],
        tools: list[ToolSchema] | None,
        stream: bool,
        on_delta: OnDelta | None = None,
        max_tokens: int = 8192,
        extra_headers: dict[str, str] | None = None,
        timeout: float = 600.0,
        reasoning: str = "off",
        tool_runner: ToolRunner | None = None,
        approver: ApprovalHandler | None = None,
        cwd: Path | None = None,
    ) -> LLMResponse:
        if not auth.available():
            raise CodexAppServerError(
                "Codex CLI is not logged in. Run `codex login` "
                "(or `codex login --device-auth` on a headless host), then retry."
            )
        workdir = cwd or Path.cwd()
        instructions = self._instructions(messages)
        dynamic_tools = self._to_dynamic_tools(tools)
        client = self._ensure_thread(
            model=model,
            cwd=workdir,
            instructions=instructions,
            dynamic_tools=dynamic_tools,
        )
        prompt = self._latest_user_text(messages)
        if not prompt.strip():
            prompt = "Continue."

        text_parts: list[str] = []
        completed_text: str | None = None
        errors: list[str] = []

        def handle_server_request(msg: dict[str, Any]) -> None:
            self._handle_server_request(msg, tool_runner=tool_runner, approver=approver)

        turn = client.request(
            "turn/start",
            {
                "threadId": self._thread_id,
                "input": [{"type": "text", "text": prompt}],
                "model": model,
            },
            timeout=20,
            server_request_handler=handle_server_request,
        )
        turn_id = (turn.get("turn") or {}).get("id")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not client.is_alive():
                tail = client.stderr_tail()
                raise CodexAppServerError(
                    "codex app-server exited during turn" + (f":\n{tail}" if tail else "")
                )
            msg = client.take_message(timeout=0.25)
            if msg is None:
                continue
            if "id" in msg and "method" in msg:
                handle_server_request(msg)
                continue
            method = msg.get("method")
            params = msg.get("params") or {}
            if method == "item/agentMessage/delta":
                delta = params.get("delta") or ""
                if delta:
                    text_parts.append(delta)
                    if on_delta:
                        on_delta(delta)
            elif method == "item/completed":
                item = params.get("item") or {}
                if item.get("type") == "agentMessage" and item.get("text"):
                    completed_text = item["text"]
            elif method == "turn/completed":
                turn_obj = params.get("turn") or {}
                completed_text = completed_text or self._final_text_from_turn(turn_obj)
                err = turn_obj.get("error")
                if err:
                    errors.append(json.dumps(err, ensure_ascii=False))
                break
            elif method == "error":
                err = params.get("error") or params
                errors.append(json.dumps(err, ensure_ascii=False))
                if not params.get("willRetry", False):
                    break
            elif msg.get("id") is not None:
                self._client.respond_error(msg["id"], "Unexpected response while waiting for turn")
        else:
            raise CodexAppServerError("codex app-server turn timed out")

        if errors and not (completed_text or text_parts):
            raise CodexAppServerError("; ".join(errors))
        final_text = completed_text if completed_text is not None else "".join(text_parts)
        return LLMResponse(text=final_text, finish_reason="completed", raw={"turn_id": turn_id})

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
        self._client = None
        self._thread_id = None
        self._fingerprint = None

    def _ensure_thread(
        self,
        *,
        model: str,
        cwd: Path,
        instructions: str,
        dynamic_tools: list[dict[str, Any]],
    ) -> _CodexAppServerClient:
        fingerprint = self._thread_fingerprint(model, cwd, instructions, dynamic_tools)
        if (
            self._client is not None
            and self._client.is_alive()
            and self._thread_id is not None
            and self._fingerprint == fingerprint
        ):
            return self._client

        self.close()
        self._client = _CodexAppServerClient(self.command)
        self._client.initialize()
        params: dict[str, Any] = {
            "cwd": str(cwd),
            "model": model,
            "developerInstructions": instructions,
            "experimentalRawEvents": True,
            "dynamicTools": dynamic_tools,
        }
        result = self._client.request("thread/start", params, timeout=30)
        thread = result.get("thread") or {}
        thread_id = thread.get("id") or thread.get("sessionId") or result.get("threadId")
        if not thread_id:
            raise CodexAppServerError("codex thread/start returned no thread id")
        self._thread_id = thread_id
        self._fingerprint = fingerprint
        return self._client

    def _handle_server_request(
        self,
        msg: dict[str, Any],
        *,
        tool_runner: ToolRunner | None,
        approver: ApprovalHandler | None,
    ) -> None:
        if self._client is None:
            return
        request_id = msg.get("id")
        method = msg.get("method")
        params = msg.get("params") or {}
        try:
            if method == "item/tool/call":
                self._client.respond(request_id, self._run_dynamic_tool(params, tool_runner))
            elif method in {
                "item/commandExecution/requestApproval",
                "item/fileChange/requestApproval",
            }:
                approved = self._approve_native_action(method, params, approver)
                self._client.respond(
                    request_id,
                    {"decision": "accept" if approved else "decline"},
                )
            elif method == "item/tool/requestUserInput":
                self._client.respond(request_id, {"answers": {}})
            elif method == "item/permissions/requestApproval":
                self._client.respond(request_id, {"permissions": {}, "scope": "turn"})
            else:
                self._client.respond_error(request_id, f"Unsupported Codex server request: {method}")
        except Exception as exc:  # noqa: BLE001
            self._client.respond_error(request_id, f"{type(exc).__name__}: {exc}")

    def _run_dynamic_tool(
        self,
        params: dict[str, Any],
        tool_runner: ToolRunner | None,
    ) -> dict[str, Any]:
        if tool_runner is None:
            return self._dynamic_tool_response(
                ToolResult.error("AEGIS tool runner is not available in this turn")
            )
        raw_args = params.get("arguments")
        args = raw_args if isinstance(raw_args, dict) else {"value": raw_args}
        call = ToolCall(
            id=str(params.get("callId") or f"codex_tool_{int(time.time() * 1000)}"),
            name=str(params.get("tool") or ""),
            arguments=args,
        )
        return self._dynamic_tool_response(tool_runner(call))

    def _dynamic_tool_response(self, result: ToolResult) -> dict[str, Any]:
        return {
            "contentItems": [{"type": "inputText", "text": result.content or ""}],
            "success": not result.is_error,
        }

    def _approve_native_action(
        self,
        method: str,
        params: dict[str, Any],
        approver: ApprovalHandler | None,
    ) -> bool:
        if approver is None:
            return False
        if method == "item/commandExecution/requestApproval":
            command = params.get("command") or "<unknown command>"
            cwd = params.get("cwd") or ""
            reason = params.get("reason") or "Codex requests command execution"
            prompt = f"{reason}\n\ncwd: {cwd}\ncommand: {command}"
        else:
            item_id = params.get("itemId") or "<unknown file change>"
            reason = params.get("reason") or "Codex requests file changes"
            prompt = f"{reason}\n\nitem: {item_id}"
        try:
            return bool(approver(prompt))
        except Exception:
            return False

    def _instructions(self, messages: list[Message]) -> str:
        return "\n\n".join(m.content for m in messages if m.role == "system" and m.content)

    def _latest_user_text(self, messages: list[Message]) -> str:
        for message in reversed(messages):
            if message.role == "user":
                text = message.content or ""
                if message.images:
                    image_lines = "\n".join(f"[image attached: {p}]" for p in message.images)
                    return f"{text}\n\n{image_lines}".strip()
                return text
        return ""

    def _final_text_from_turn(self, turn: dict[str, Any]) -> str:
        parts: list[str] = []
        for item in turn.get("items") or []:
            if isinstance(item, dict) and item.get("type") == "agentMessage" and item.get("text"):
                parts.append(item["text"])
        return "".join(parts)

    def _to_dynamic_tools(self, tools: list[ToolSchema] | None) -> list[dict[str, Any]]:
        dynamic: list[dict[str, Any]] = []
        for tool in tools or []:
            name = str(tool.get("name") or "").strip()
            if not name:
                continue
            dynamic.append(
                {
                    "name": name,
                    "namespace": "aegis",
                    "description": str(tool.get("description") or f"AEGIS {name} tool"),
                    "inputSchema": tool.get("parameters") or {"type": "object", "properties": {}},
                }
            )
        return dynamic

    def _thread_fingerprint(
        self,
        model: str,
        cwd: Path,
        instructions: str,
        dynamic_tools: list[dict[str, Any]],
    ) -> str:
        payload = {
            "model": model,
            "cwd": str(cwd),
            "instructions": instructions,
            "dynamic_tools": dynamic_tools,
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()
