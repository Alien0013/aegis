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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..tools.base import ToolResult
from ..types import LLMResponse, Message, ToolCall, ToolSchema, Usage
from .auth import AuthProvider
from .base import ApiMode, ApprovalHandler, OnDelta, ProviderTransport, ToolRunner
from .schema import sanitize as _sanitize_schema


class CodexAppServerError(RuntimeError):
    """Raised when the local Codex app-server cannot complete a turn."""

    def __init__(self, message: str, *, code: int | None = None, data: Any = None):
        self.code = code
        self.data = data
        super().__init__(message)


_TURN_ABORTED_MARKERS = ("<turn_aborted>", "<turn_aborted/>")


def _has_turn_aborted_marker(text: str | None) -> bool:
    if not text:
        return False
    return any(marker in text for marker in _TURN_ABORTED_MARKERS)


def _deterministic_call_id(item_type: str, item_id: str) -> str:
    if item_id:
        return f"codex_{item_type}_{item_id}"
    digest = hashlib.sha256(item_type.encode("utf-8")).hexdigest()[:16]
    return f"codex_{item_type}_{digest}"


@dataclass
class _CodexProjectionResult:
    messages: list[Message] = field(default_factory=list)
    is_tool_iteration: bool = False
    final_text: str | None = None


class _CodexEventProjector:
    """Project Codex app-server item notifications into AEGIS message shape."""

    def __init__(self) -> None:
        self._pending_reasoning: list[str] = []

    def project(self, notification: dict[str, Any]) -> _CodexProjectionResult:
        if notification.get("method") != "item/completed":
            return _CodexProjectionResult()
        params = notification.get("params") or {}
        item = params.get("item") or {}
        if not isinstance(item, dict):
            return _CodexProjectionResult()
        item_type = str(item.get("type") or "")
        item_id = str(item.get("id") or "")
        if item_type == "agentMessage":
            return self._project_agent_message(item)
        if item_type == "reasoning":
            self._pending_reasoning.extend(str(x) for x in (item.get("summary") or []))
            self._pending_reasoning.extend(str(x) for x in (item.get("content") or []))
            return _CodexProjectionResult()
        if item_type == "commandExecution":
            return self._project_command(item, item_id)
        if item_type == "fileChange":
            return self._project_file_change(item, item_id)
        if item_type == "mcpToolCall":
            return self._project_mcp_tool_call(item, item_id)
        if item_type == "dynamicToolCall":
            return self._project_dynamic_tool_call(item, item_id)
        if item_type == "userMessage":
            return self._project_user_message(item)
        return self._project_opaque(item, item_type)

    def _attach_reasoning(self, message: Message) -> Message:
        if self._pending_reasoning:
            message.reasoning = "\n".join(self._pending_reasoning)
            self._pending_reasoning = []
        return message

    def _project_agent_message(self, item: dict[str, Any]) -> _CodexProjectionResult:
        text = str(item.get("text") or "")
        return _CodexProjectionResult(messages=[self._attach_reasoning(Message.assistant(text))], final_text=text)

    def _project_user_message(self, item: dict[str, Any]) -> _CodexProjectionResult:
        parts: list[str] = []
        for fragment in item.get("content") or []:
            if isinstance(fragment, dict):
                if fragment.get("type") == "text":
                    parts.append(str(fragment.get("text") or ""))
                elif "text" in fragment:
                    parts.append(str(fragment["text"]))
        return _CodexProjectionResult(messages=[Message.user("\n".join(parts))])

    def _project_command(self, item: dict[str, Any], item_id: str) -> _CodexProjectionResult:
        call_id = _deterministic_call_id("exec", item_id)
        assistant = self._attach_reasoning(Message.assistant(tool_calls=[
            ToolCall(
                id=call_id,
                name="exec_command",
                arguments={"command": item.get("command") or "", "cwd": item.get("cwd") or ""},
            )
        ]))
        output = str(item.get("aggregatedOutput") or "")
        exit_code = item.get("exitCode")
        if exit_code is not None and exit_code != 0:
            output = f"[exit {exit_code}]\n{output}"
        return _CodexProjectionResult(
            messages=[assistant, Message.tool(call_id, "exec_command", output)],
            is_tool_iteration=True,
        )

    def _project_file_change(self, item: dict[str, Any], item_id: str) -> _CodexProjectionResult:
        call_id = _deterministic_call_id("apply_patch", item_id)
        changes_summary: list[dict[str, str]] = []
        for change in item.get("changes") or []:
            if not isinstance(change, dict):
                continue
            kind_obj = change.get("kind") or {}
            kind = kind_obj.get("type") if isinstance(kind_obj, dict) else None
            changes_summary.append({"kind": str(kind or "update"), "path": str(change.get("path") or "")})
        assistant = self._attach_reasoning(Message.assistant(tool_calls=[
            ToolCall(id=call_id, name="apply_patch", arguments={"changes": changes_summary})
        ]))
        status = item.get("status") or "unknown"
        return _CodexProjectionResult(
            messages=[
                assistant,
                Message.tool(call_id, "apply_patch", f"apply_patch status={status}, {len(changes_summary)} change(s)"),
            ],
            is_tool_iteration=True,
        )

    def _project_mcp_tool_call(self, item: dict[str, Any], item_id: str) -> _CodexProjectionResult:
        server = str(item.get("server") or "mcp")
        tool = str(item.get("tool") or "unknown")
        call_id = _deterministic_call_id(f"mcp_{server}_{tool}", item_id)
        args = item.get("arguments") if isinstance(item.get("arguments"), dict) else {"arguments": item.get("arguments")}
        assistant = self._attach_reasoning(Message.assistant(tool_calls=[
            ToolCall(id=call_id, name=f"mcp.{server}.{tool}", arguments=args or {})
        ]))
        error = item.get("error")
        if error:
            content = f"[error] {json.dumps(error, ensure_ascii=False)[:1000]}"
        elif item.get("result") is not None:
            content = json.dumps(item.get("result"), ensure_ascii=False)[:4000]
        else:
            content = ""
        return _CodexProjectionResult(
            messages=[assistant, Message.tool(call_id, f"mcp.{server}.{tool}", content)],
            is_tool_iteration=True,
        )

    def _project_dynamic_tool_call(self, item: dict[str, Any], item_id: str) -> _CodexProjectionResult:
        tool = str(item.get("tool") or "unknown")
        call_id = _deterministic_call_id(f"dyn_{tool}", item_id)
        args = item.get("arguments") if isinstance(item.get("arguments"), dict) else {"arguments": item.get("arguments")}
        assistant = self._attach_reasoning(Message.assistant(tool_calls=[
            ToolCall(id=call_id, name=tool, arguments=args or {})
        ]))
        content_items = item.get("contentItems") or []
        if isinstance(content_items, list) and content_items:
            content = json.dumps(content_items, ensure_ascii=False)[:4000]
        else:
            content = f"success={item.get('success')}"
        return _CodexProjectionResult(
            messages=[assistant, Message.tool(call_id, tool, content)],
            is_tool_iteration=True,
        )

    def _project_opaque(self, item: dict[str, Any], item_type: str) -> _CodexProjectionResult:
        try:
            payload = json.dumps(item, ensure_ascii=False)[:1500]
        except (TypeError, ValueError):
            payload = repr(item)[:1500]
        return _CodexProjectionResult(messages=[Message.assistant(f"[codex {item_type}] {payload}")])


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
        self._pending_file_changes: dict[str, str] = {}
        self._dynamic_tool_workers: list[threading.Thread] = []
        self._dynamic_tool_workers_lock = threading.Lock()
        self.post_tool_quiet_timeout = 90.0

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
        on_reasoning: OnDelta | None = None,
        metadata: dict | None = None,
    ) -> LLMResponse:
        if not auth.available():
            raise CodexAppServerError(
                "Codex CLI is not logged in. Run `codex login` "
                "(or `codex login --device-auth` on a headless host), then retry."
            )
        workdir = cwd or Path.cwd()
        instructions = self._instructions(messages)
        dynamic_tools = self._to_dynamic_tools(tools)
        self._maybe_migrate_codex_runtime_config(metadata)
        client = self._ensure_thread(
            model=model,
            cwd=workdir,
            instructions=instructions,
            dynamic_tools=dynamic_tools,
        )
        prompt = self._latest_user_text(messages)
        if not prompt.strip():
            prompt = "Continue."
        turn_params: dict[str, Any] = {
            "threadId": self._thread_id,
            "input": [{"type": "text", "text": prompt}],
            "model": model,
        }
        effort = _codex_reasoning_effort(reasoning)
        if effort:
            turn_params["effort"] = effort
            turn_params["summary"] = "auto"

        text_parts: list[str] = []
        completed_text: str | None = None
        errors: list[str] = []
        projected_messages: list[Message] = []
        projector = _CodexEventProjector()
        tool_iterations = 0
        token_usage_last: dict[str, Any] | None = None
        token_usage_total: dict[str, Any] | None = None
        model_context_window: int | None = None
        interrupted = False
        last_tool_completion_at: float | None = None
        # Codex app-server reports usage via `thread/tokenUsage/updated`; the per-step
        # `last` buckets sum to the turn total. inputTokens already includes the cached
        # subset (totalTokens == inputTokens + outputTokens), matching AEGIS's convention.
        usage_input = usage_cached = usage_output = 0

        def handle_server_request(msg: dict[str, Any]) -> None:
            self._handle_server_request(msg, tool_runner=tool_runner, approver=approver)

        turn = client.request(
            "turn/start",
            turn_params,
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
                if (
                    last_tool_completion_at is not None
                    and time.monotonic() - last_tool_completion_at > self.post_tool_quiet_timeout
                ):
                    self._interrupt_turn(client, turn_id)
                    self.close()
                    raise CodexAppServerError(
                        f"codex went silent for {self.post_tool_quiet_timeout:.0f}s after a tool result; "
                        "retiring app-server session."
                    )
                continue
            if "id" in msg and "method" in msg:
                handle_server_request(msg)
                continue
            method = msg.get("method")
            params = msg.get("params") or {}
            if method == "item/started":
                self._track_pending_file_change(msg)
            if method == "item/agentMessage/delta":
                delta = params.get("delta") or ""
                if delta:
                    text_parts.append(delta)
                    if on_delta:
                        on_delta(delta)
            elif method in ("item/reasoning/summaryTextDelta", "item/reasoning/textDelta"):
                # Codex streams its reasoning summary (and, when enabled, raw
                # reasoning) as separate notifications. Surface them live so the
                # display layer can render the thinking box.
                delta = params.get("delta") or ""
                if delta and on_reasoning:
                    on_reasoning(delta)
            elif method == "thread/tokenUsage/updated":
                token_usage = params.get("tokenUsage") or {}
                last = token_usage.get("last") or {}
                total = token_usage.get("total")
                if isinstance(last, dict):
                    token_usage_last = dict(last)
                if isinstance(total, dict):
                    token_usage_total = dict(total)
                window = token_usage.get("modelContextWindow")
                if isinstance(window, int) and window > 0:
                    model_context_window = window
                usage_input += int(last.get("inputTokens") or 0)
                usage_cached += int(last.get("cachedInputTokens") or 0)
                usage_output += (int(last.get("outputTokens") or 0)
                                 + int(last.get("reasoningOutputTokens") or 0))
            elif method == "item/completed":
                projection = projector.project(msg)
                if projection.messages:
                    projected_messages.extend(projection.messages)
                if projection.is_tool_iteration:
                    tool_iterations += 1
                    last_tool_completion_at = time.monotonic()
                elif projection.messages or projection.final_text is not None:
                    last_tool_completion_at = None
                if projection.final_text is not None:
                    completed_text = projection.final_text
                    if _has_turn_aborted_marker(projection.final_text):
                        interrupted = True
                        errors.append("codex reported turn_aborted")
                        break
                self._track_pending_file_change(msg)
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
            if (
                last_tool_completion_at is not None
                and time.monotonic() - last_tool_completion_at > self.post_tool_quiet_timeout
            ):
                self._interrupt_turn(client, turn_id)
                self.close()
                raise CodexAppServerError(
                    f"codex went silent for {self.post_tool_quiet_timeout:.0f}s after a tool result; "
                    "retiring app-server session."
                )
        else:
            self._interrupt_turn(client, turn_id)
            self.close()
            raise CodexAppServerError("codex app-server turn timed out")

        if errors and not (completed_text or text_parts):
            raise CodexAppServerError("; ".join(errors))
        final_text = completed_text if completed_text is not None else "".join(text_parts)
        usage = Usage(usage_input, usage_output, usage_cached, 0)
        return LLMResponse(
            text=final_text,
            finish_reason="interrupted" if interrupted else "completed",
            usage=usage,
            raw={
                "turn_id": turn_id,
                "projected_messages": [m.to_dict() for m in projected_messages],
                "tool_iterations": tool_iterations,
                "token_usage_last": token_usage_last,
                "token_usage_total": token_usage_total,
                "model_context_window": model_context_window,
                "interrupted": interrupted,
                "errors": list(errors),
            },
        )

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
        self._client = None
        self._thread_id = None
        self._fingerprint = None
        self._pending_file_changes = {}
        self._reap_dynamic_tool_workers()

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
                self._queue_dynamic_tool_request(request_id, params, tool_runner)
            elif method in {
                "item/commandExecution/requestApproval",
                "item/fileChange/requestApproval",
            }:
                self._client.respond(request_id, {"decision": self._approve_native_action(method, params, approver)})
            elif method == "item/tool/requestUserInput":
                self._client.respond(request_id, {"answers": {}})
            elif method == "item/permissions/requestApproval":
                self._client.respond(request_id, {"decision": "decline"})
            elif method == "mcpServer/elicitation/request":
                action = "accept" if params.get("serverName") in {"aegis-tools", "hermes-tools"} else "decline"
                self._client.respond(request_id, {"action": action, "content": None, "_meta": None})
            else:
                self._client.respond_error(request_id, f"Unsupported Codex server request: {method}", code=-32601)
        except Exception as exc:  # noqa: BLE001
            self._client.respond_error(request_id, f"{type(exc).__name__}: {exc}")

    def _queue_dynamic_tool_request(
        self,
        request_id: Any,
        params: dict[str, Any],
        tool_runner: ToolRunner | None,
    ) -> None:
        client = self._client
        if client is None:
            return

        def run_tool() -> None:
            try:
                client.respond(request_id, self._run_dynamic_tool(params, tool_runner))
            except Exception as exc:  # noqa: BLE001
                try:
                    client.respond_error(request_id, f"{type(exc).__name__}: {exc}")
                except Exception:
                    pass
            finally:
                current = threading.current_thread()
                with self._dynamic_tool_workers_lock:
                    self._dynamic_tool_workers = [
                        worker for worker in self._dynamic_tool_workers
                        if worker is not current and worker.is_alive()
                    ]

        thread = threading.Thread(
            target=run_tool,
            name=f"aegis-codex-dynamic-tool-{request_id}",
            daemon=True,
        )
        with self._dynamic_tool_workers_lock:
            self._dynamic_tool_workers.append(thread)
        thread.start()

    def drain_dynamic_tool_workers(self, timeout: float = 1.0) -> None:
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            self._reap_dynamic_tool_workers()
            with self._dynamic_tool_workers_lock:
                workers = list(self._dynamic_tool_workers)
            if not workers:
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            for worker in workers:
                worker.join(timeout=min(remaining, 0.05))

    def _reap_dynamic_tool_workers(self) -> None:
        with self._dynamic_tool_workers_lock:
            self._dynamic_tool_workers = [
                worker for worker in self._dynamic_tool_workers if worker.is_alive()
            ]

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
    ) -> str:
        if approver is None:
            return "decline"
        if method == "item/commandExecution/requestApproval":
            command = params.get("command") or "<unknown command>"
            cwd = params.get("cwd") or ""
            reason = params.get("reason") or "Codex requests command execution"
            prompt = f"{reason}\n\ncwd: {cwd}\ncommand: {command}"
        else:
            item_id = params.get("itemId") or "<unknown file change>"
            reason = params.get("reason") or "Codex requests file changes"
            summary = self._pending_file_changes.get(str(item_id or ""))
            prompt = f"{reason}\n\nitem: {item_id}"
            if summary:
                prompt = f"{prompt}\nchanges: {summary}"
        try:
            return _approval_choice_to_codex_decision(approver(prompt))
        except Exception:
            return "decline"

    def _track_pending_file_change(self, msg: dict[str, Any]) -> None:
        params = msg.get("params") or {}
        item = params.get("item") or {}
        if not isinstance(item, dict) or item.get("type") != "fileChange":
            return
        item_id = str(item.get("id") or "")
        if not item_id:
            return
        if msg.get("method") == "item/started":
            changes = item.get("changes") or []
            if not changes:
                self._pending_file_changes[item_id] = "1 change pending"
                return
            kinds: dict[str, int] = {}
            paths: list[str] = []
            for change in changes:
                if not isinstance(change, dict):
                    continue
                kind_obj = change.get("kind") or {}
                kind = kind_obj.get("type") if isinstance(kind_obj, dict) else None
                clean_kind = str(kind or "update")
                kinds[clean_kind] = kinds.get(clean_kind, 0) + 1
                path = str(change.get("path") or "")
                if path:
                    paths.append(path)
            counts = ", ".join(f"{count} {kind}" for kind, count in sorted(kinds.items()))
            preview = ", ".join(paths[:3])
            if len(paths) > 3:
                preview += f", +{len(paths) - 3} more"
            self._pending_file_changes[item_id] = f"{counts}: {preview}" if preview else counts
        elif msg.get("method") == "item/completed":
            self._pending_file_changes.pop(item_id, None)

    def _interrupt_turn(self, client: _CodexAppServerClient, turn_id: str | None) -> None:
        if not self._thread_id or not turn_id:
            return
        try:
            client.request(
                "turn/interrupt",
                {"threadId": self._thread_id, "turnId": turn_id},
                timeout=5,
            )
        except Exception:
            pass

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
                    "inputSchema": _sanitize_schema(
                        tool.get("parameters") or {"type": "object", "properties": {}}),
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

    def _maybe_migrate_codex_runtime_config(self, metadata: dict | None) -> None:
        try:
            from .codex_runtime_migration import maybe_migrate_from_metadata

            report = maybe_migrate_from_metadata(metadata)
        except Exception:
            return
        if report is not None and report.errors:
            # Do not fail the model turn just because the optional Codex config
            # bridge could not write. Dynamic AEGIS tools still work through the
            # direct app-server dynamicTools path.
            return


def _codex_reasoning_effort(reasoning: str | None) -> str:
    value = str(reasoning or "").strip().lower()
    if value in {"", "off", "none"}:
        return ""
    if value in {"minimal", "low", "medium", "high", "xhigh"}:
        return value
    return "medium"


def _approval_choice_to_codex_decision(choice: Any) -> str:
    if isinstance(choice, str):
        clean = choice.strip().lower()
        if clean == "once":
            return "accept"
        if clean in {"session", "always", "acceptforsession"}:
            return "acceptForSession"
        if clean in {"accept", "allow", "yes", "true"}:
            return "accept"
        return "decline"
    return "accept" if bool(choice) else "decline"
