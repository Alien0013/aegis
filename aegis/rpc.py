"""Generic AEGIS JSON-RPC surface over stdio.

This is the small, provider-neutral runtime surface used by IDE bridges,
platform adapters, batch supervisors, or other local tools that want an agent
loop without speaking ACP, MCP, or the OpenAI-compatible HTTP API.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, TextIO

from .config import Config
from .session import SessionStore
from .surface import SurfaceRunner
from .types import Message

PROTOCOL_VERSION = "2026-06-11"


class RpcServer:
    def __init__(
        self,
        config: Config,
        *,
        stdin: TextIO | None = None,
        stdout: TextIO | None = None,
        runner: SurfaceRunner | None = None,
    ) -> None:
        self.config = config
        self.stdin = stdin or sys.stdin
        self.stdout = stdout or sys.stdout
        self.store = SessionStore()
        self.runner = runner or SurfaceRunner(config, store=self.store, include_mcp=True)

    def serve(self) -> None:
        try:
            for line in self.stdin:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    self._send_error(None, -32700, "parse error")
                    continue
                self.handle(msg)
        finally:
            self.runner.close()

    def handle(self, msg: dict[str, Any]) -> None:
        mid = msg.get("id")
        method = msg.get("method")
        params = msg.get("params") or {}
        if not isinstance(params, dict):
            self._send_error(mid, -32602, "params must be an object")
            return
        try:
            if method == "initialize":
                self._send_result(mid, self._initialize())
            elif method in {"agent.run", "run"}:
                self._send_result(mid, self._agent_run(params))
            elif method in {"sessions.list", "session.list"}:
                self._send_result(mid, self._sessions_list(params))
            elif method in {"sessions.get", "session.get"}:
                self._send_result(mid, self._session_get(params))
            elif method in {"runs.list", "run.list"}:
                self._send_result(mid, self._runs_list(params))
            elif method in {"runs.get", "run.get"}:
                self._send_result(mid, self._run_get(params))
            elif method in {"traces.list", "trace.list"}:
                self._send_result(mid, self._traces_list(params))
            elif method in {"traces.get", "trace.get"}:
                self._send_result(mid, self._trace_get(params))
            elif method in {"evals.list", "eval.list"}:
                self._send_result(mid, self._evals_list(params))
            elif method in {"evals.get", "eval.get"}:
                self._send_result(mid, self._eval_get(params))
            elif method in {"evals.run", "eval.run"}:
                self._send_result(mid, self._eval_run(params))
            elif method in {"evals.trace", "eval.trace"}:
                self._send_result(mid, self._eval_trace(params))
            elif method == "ping":
                self._send_result(mid, {"ok": True})
            elif mid is not None:
                self._send_error(mid, -32601, "method not found")
        except LookupError as exc:
            self._send_error(mid, -32004, str(exc))
        except ValueError as exc:
            self._send_error(mid, -32602, str(exc))
        except Exception as exc:  # noqa: BLE001
            self._send_error(mid, -32000, f"{type(exc).__name__}: {exc}")

    def _initialize(self) -> dict[str, Any]:
        from . import __version__

        return {
            "protocolVersion": PROTOCOL_VERSION,
            "serverInfo": {"name": "aegis-json-rpc", "version": __version__},
            "capabilities": {
                "agent": {"run": True, "eventNotifications": True},
                "sessions": {"list": True, "get": True},
                "runs": {"list": True, "get": True},
                "traces": {"list": True, "get": True, "evaluate": True},
                "evals": {"list": True, "get": True, "run": True, "trace": True},
            },
        }

    def _agent_run(self, params: dict[str, Any]) -> dict[str, Any]:
        prompt = params.get("prompt", params.get("message", ""))
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("agent.run requires a non-empty prompt")
        session_id = _opt_str(params, "session_id") or _opt_str(params, "sessionId")
        title = _opt_str(params, "title")
        cwd = _opt_path(params.get("cwd"))
        model = _opt_str(params, "model")
        provider = _opt_str(params, "provider")
        stream = params.get("stream")
        stream_events = bool(params.get("stream_events", params.get("events", True)))
        meta = params.get("meta") if isinstance(params.get("meta"), dict) else {}
        history = _messages_from_params(params)
        session = self.runner.load_or_create_session(
            session_id,
            title=title,
            history=history,
            surface="rpc",
            meta={"rpc": True, **meta},
        )

        def on_event(event: dict[str, Any]) -> None:
            if stream_events:
                self._send_notification("agent.event", {
                    "session_id": session.id,
                    "event": _jsonable(event),
                })

        result = self.runner.run_prompt(
            prompt,
            session=session,
            title=title,
            model=model,
            provider_name=provider,
            cwd=cwd,
            surface="rpc",
            meta={"rpc": True, **meta},
            on_event=on_event,
            stream=bool(stream) if stream is not None else None,
        )
        return _run_payload(result)

    def _sessions_list(self, params: dict[str, Any]) -> dict[str, Any]:
        limit = _int(params.get("limit"), 50)
        return {"sessions": [_jsonable(s) for s in self.store.list(limit=limit)]}

    def _session_get(self, params: dict[str, Any]) -> dict[str, Any]:
        sid = _opt_str(params, "session_id") or _opt_str(params, "sessionId") or _opt_str(params, "id")
        if not sid:
            raise ValueError("session id required")
        session = self.store.load(sid)
        if session is None:
            raise LookupError(f"session not found: {sid}")
        return {"session": _session_payload(session)}

    def _runs_list(self, params: dict[str, Any]) -> dict[str, Any]:
        from .runs import RunStore

        return {"runs": _jsonable(RunStore().list(
            limit=_int(params.get("limit"), 50),
            surface=_opt_str(params, "surface") or None,
            session_id=_opt_str(params, "session_id") or _opt_str(params, "sessionId") or None,
            status=_opt_str(params, "status") or None,
        ))}

    def _run_get(self, params: dict[str, Any]) -> dict[str, Any]:
        run_id = _opt_str(params, "run_id") or _opt_str(params, "runId") or _opt_str(params, "id")
        if not run_id:
            raise ValueError("run id required")
        from .runs import RunStore

        run = RunStore().get(run_id)
        if run is None:
            raise LookupError(f"run not found: {run_id}")
        return {"run": _jsonable(run)}

    def _traces_list(self, params: dict[str, Any]) -> dict[str, Any]:
        from .tracing import TraceStore

        return {"traces": _jsonable(TraceStore.from_config(self.config).list_traces(
            session_id=_opt_str(params, "session_id") or _opt_str(params, "sessionId") or None,
            limit=_int(params.get("limit"), 50),
        ))}

    def _trace_get(self, params: dict[str, Any]) -> dict[str, Any]:
        trace_id = _opt_str(params, "trace_id") or _opt_str(params, "traceId") or _opt_str(params, "id")
        if not trace_id:
            raise ValueError("trace id required")
        from .tracing import TraceStore

        trace = TraceStore.from_config(self.config).get_trace(trace_id)
        if trace is None:
            raise LookupError(f"trace not found: {trace_id}")
        return {"trace": _jsonable(trace)}

    def _evals_list(self, params: dict[str, Any]) -> dict[str, Any]:
        from .evals import EvalStore

        return {"evals": _jsonable(EvalStore.from_config(self.config).list_runs(
            limit=_int(params.get("limit"), 20),
        ))}

    def _eval_get(self, params: dict[str, Any]) -> dict[str, Any]:
        eval_id = _opt_str(params, "eval_id") or _opt_str(params, "evalId") or _opt_str(params, "id")
        if not eval_id:
            raise ValueError("eval id required")
        from .evals import EvalStore

        run = EvalStore.from_config(self.config).get_run(eval_id)
        if run is None:
            raise LookupError(f"eval run not found: {eval_id}")
        return {"eval": _jsonable(run)}

    def _eval_run(self, params: dict[str, Any]) -> dict[str, Any]:
        path = _opt_str(params, "path")
        if not path:
            raise ValueError("eval run requires path")
        from .evals import run_suite

        return {"eval": _jsonable(run_suite(path, config=self.config))}

    def _eval_trace(self, params: dict[str, Any]) -> dict[str, Any]:
        trace_id = _opt_str(params, "trace_id") or _opt_str(params, "traceId") or _opt_str(params, "id")
        if not trace_id:
            raise ValueError("trace id required")
        from . import evals
        from .tracing import TraceStore

        return {"evaluation": _jsonable(evals.evaluate_trace(trace_id, store=TraceStore.from_config(self.config)))}

    def _send_result(self, mid: Any, result: dict[str, Any]) -> None:
        if mid is not None:
            self._send({"jsonrpc": "2.0", "id": mid, "result": result})

    def _send_error(self, mid: Any, code: int, message: str) -> None:
        self._send({"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}})

    def _send_notification(self, method: str, params: dict[str, Any]) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _send(self, obj: dict[str, Any]) -> None:
        self.stdout.write(json.dumps(obj, default=str) + "\n")
        self.stdout.flush()


def run_rpc_server(config: Config, *, stdin: TextIO | None = None, stdout: TextIO | None = None) -> None:
    RpcServer(config, stdin=stdin, stdout=stdout).serve()


def cmd_rpc(args, config: Config) -> int:
    run_rpc_server(config)
    return 0


def _messages_from_params(params: dict[str, Any]) -> list[Message] | None:
    raw = params.get("history")
    if raw is None:
        raw = params.get("messages")
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise ValueError("history/messages must be a list")
    messages: list[Message] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("history/messages entries must be objects")
        messages.append(Message.from_dict(item))
    return messages


def _run_payload(result) -> dict[str, Any]:
    return {
        "text": result.text,
        "session_id": result.session.id,
        "trace_id": result.trace_id,
        "turn_id": result.turn_id,
        "run_id": result.run_id,
        "events": [_jsonable(e) for e in result.events],
        "message": _jsonable(result.message.to_dict() if hasattr(result.message, "to_dict") else result.message),
    }


def _session_payload(session) -> dict[str, Any]:
    return {
        "id": session.id,
        "title": session.title,
        "created_at": session.created_at,
        "updated_at": session.updated_at,
        "parent_id": session.parent_id,
        "messages": [
            _jsonable(m.to_dict() if hasattr(m, "to_dict") else m)
            for m in session.messages
        ],
        "meta": _jsonable(session.meta),
    }


def _opt_str(params: dict[str, Any], key: str) -> str:
    value = params.get(key)
    return str(value).strip() if value is not None else ""


def _opt_path(value: Any) -> Path | None:
    if value in (None, ""):
        return None
    return Path(str(value)).expanduser()


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, tuple):
        return [_jsonable(v) for v in value]
    if hasattr(value, "to_dict"):
        return _jsonable(value.to_dict())
    if hasattr(value, "__dict__") and not isinstance(value, type):
        return _jsonable(vars(value))
    return value
