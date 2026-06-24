"""WebSocket gateway between the Node/Ink terminal client and the Python agent.

This is the Python half of AEGIS' Hermes-style terminal: a local-only WebSocket server
that owns the agent, runs each turn on a worker thread, and streams the result to the Ink
client as JSON frames. Every byte the turn would normally print to the terminal is captured
(ANSI intact) and forwarded as ``output`` frames, so the Ink surface shows exactly what the
classic REPL would — tool cards, thinking boxes, slash-command output and all — without
re-implementing any renderer.

Frame protocol (JSON, one object per message)::

    client -> server : {"type":"hello","token":...}
                       {"type":"input","text":...}
                       {"type":"interrupt"}
                       {"type":"answer","value":...}
    server -> client : {"type":"ready","header":{...}}
                       {"type":"output","text":<ansi chunk>}
                       {"type":"status","header":{...},"running":bool}
                       {"type":"ask","label":...,"secret":bool}
                       {"type":"turn_done"}
                       {"type":"exit"}

The server binds to 127.0.0.1 on an ephemeral port and requires a one-time token (passed to
the child Node process by the launcher) so nothing else on the box can drive the agent.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import sys
import threading
from typing import Any

from .config import Config
from .session import Session, SessionStore


class _Sink:
    """stdout-shaped object that ships written text to a thread-safe callback.

    Reports ``isatty() == True`` so Rich keeps emitting ANSI colour for the Ink client."""

    encoding = "utf-8"

    def __init__(self, emit):
        self._emit = emit

    def write(self, text: str) -> int:
        if text:
            self._emit(text)
        return len(text)

    def flush(self) -> None:
        return None

    def isatty(self) -> bool:
        return True

    def fileno(self) -> int:
        raise OSError("gateway sink has no fileno")


_FORWARD_EVENTS = frozenset({
    "terminal_turn_start", "terminal_turn_end", "iteration",
    "assistant_delta", "assistant_message", "reasoning_delta",
    "tool_start", "tool_result",
    "subagent_start", "subagent_done", "subagent_text", "subagent_reasoning",
    "continuation", "empty_nudge", "model_downshift", "budget_warning",
    "ultracode_continue", "thinking_strip_retry",
})

_SAFE_KEYS = frozenset({
    "type", "name", "text", "summary", "status", "error", "preview", "classification",
    "duration_ms", "n", "max", "chars", "iteration", "id", "subagent_id", "agent_type",
    "task", "prompt", "model", "remaining", "is_error", "session_id", "run_id",
})


def _safe_event(e: dict) -> dict:
    """Project an agent event onto a small, JSON-safe shape for the Ink client."""
    out: dict[str, Any] = {}
    for key in _SAFE_KEYS:
        if key not in e:
            continue
        val = e[key]
        if isinstance(val, (str, int, float, bool)) or val is None:
            out[key] = val
        else:
            out[key] = str(val)
    return out


class _StructuredEmitter:
    """An ``on_event`` consumer that forwards agent events to the Ink client as structured
    frames (no printing) so the front-end can render real components — tool cards, thinking,
    message bubbles — instead of parsing ANSI. Residual ``_out``/``_raw`` output (slash
    commands, banners, footers) is captured separately as ``output`` frames."""

    def __init__(self, emit):
        self._emit = emit

    def __call__(self, e: dict) -> None:
        t = e.get("type")
        if t in _FORWARD_EVENTS:
            self._emit({"type": "event", "event": _safe_event(e)})


def header_snapshot(agent: Any) -> dict:
    """Structured runtime header for the Ink top/status bars (no ANSI — the client styles it)."""
    from .cli import repl

    provider = getattr(agent, "provider", None)
    usage = getattr(getattr(agent, "budget", None), "usage", None)
    session = getattr(agent, "session", None)
    ctx = repl._context_window(agent)
    spend = 0.0
    try:
        spend = float(agent.session_spend_estimate())
    except Exception:  # noqa: BLE001
        spend = 0.0
    return {
        "brand": "AEGIS",
        "model": str(getattr(provider, "model", "") or "?"),
        "provider": str(agent.config.get("model.provider", "") or ""),
        "session_id": str(getattr(session, "id", "") or ""),
        "session_title": str(getattr(session, "title", "") or ""),
        "ctx_used": int(ctx.get("used", 0) or 0),
        "ctx_window": int(ctx.get("window", 0) or 0),
        "ctx_percent": int(ctx.get("percent", 0) or 0),
        "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
        "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
        "cost": round(spend, 4),
        "reasoning": f"{agent.config.get('display.reasoning', 'summary')}/"
                     f"{getattr(agent, 'reasoning', 'off')}",
        "perms": str(agent.config.get("tools.exec_mode", "auto") or "auto"),
        "busy": str(agent.config.get("gateway.busy_mode", "queue") or "queue"),
        "cwd": str(getattr(agent, "cwd", "") or ""),
        "version": __import__("aegis").__version__,
    }


class TuiGateway:
    """Serves one Ink client and drives one agent for the life of the connection."""

    def __init__(self, config: Config, *, model=None, provider_name=None,
                 session: Session | None = None, store: SessionStore | None = None,
                 auto: bool = False, token: str | None = None):
        self.config = config
        self.model = model
        self.provider_name = provider_name
        self.store = store or SessionStore()
        self.session = session or Session.create()
        self.auto = auto
        self.token = token or secrets.token_urlsafe(16)

        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue | None = None
        self._running = False
        self._agent = None
        self._runner = None

        # answer bridge (worker thread blocks until the client replies)
        self._answer_event: threading.Event | None = None
        self._answer_value = ""

    # ----------------------------------------------------------- agent wiring
    def _ensure_agent(self):
        if self._agent is not None:
            return
        from .cli import repl
        self._runner, self._agent = repl.build_terminal_agent(
            self.config, model=self.model, provider_name=self.provider_name,
            session=self.session, store=self.store, auto=self.auto,
            approver=self._approver(), asker=self._asker(), secret_capture=self._secret_capture(),
        )

    def _emit_threadsafe(self, frame: dict) -> None:
        if self._loop is None or self._queue is None:
            return
        try:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, frame)
        except RuntimeError:
            pass

    # --------------------------------------------------------- answer bridge
    def _await_answer(self, label: str, secret: bool = False) -> str:
        ev = threading.Event()
        self._answer_event = ev
        self._answer_value = ""
        self._emit_threadsafe({"type": "ask", "label": label, "secret": secret})
        ev.wait()
        return self._answer_value

    def _approver(self):
        auto = self.auto

        def approver(prompt_text: str):
            if auto:
                return True
            ans = self._await_answer(f"{prompt_text} [y/N/a]").strip().lower()
            if ans in ("a", "always"):
                return "always"
            return ans in ("y", "yes")
        return approver

    def _asker(self):
        def asker(question: str, choices: list[str]) -> str:
            lines = [f"  ❓ {question}"] + [f"     {i}. {c}" for i, c in enumerate(choices, 1)]
            self._emit_threadsafe({"type": "output", "text": "\n".join(lines) + "\n"})
            ans = self._await_answer("answer").strip()
            if choices and ans.isdigit() and 1 <= int(ans) <= len(choices):
                return choices[int(ans) - 1]
            return ans
        return asker

    def _secret_capture(self):
        def capture(key: str, prompt: str, metadata: dict | None = None) -> dict:
            from .secret_capture import store_secret_value
            self._emit_threadsafe({"type": "output", "text": f"  🔑 {prompt}\n"})
            value = self._await_answer(f"{key}", secret=True)
            return store_secret_value(key, value)
        return capture

    # ------------------------------------------------------------- the turn
    def _do_turn(self, text: str) -> None:
        from .cli import repl
        orig_stdout, orig_console = sys.stdout, repl._console
        sink = _Sink(lambda s: self._emit_threadsafe({"type": "output", "text": s}))
        sys.stdout = sink
        try:
            from rich.console import Console
            repl._console = Console(file=sink, force_terminal=True,
                                    color_system="truecolor", width=100, soft_wrap=False)
        except Exception:  # noqa: BLE001
            repl._console = orig_console
        try:
            result = repl.process_terminal_input(
                text, self._agent, self._runner, self.store,
                on_event=_StructuredEmitter(self._emit_threadsafe), surface="repl",
            )
        except Exception as exc:  # noqa: BLE001
            self._emit_threadsafe({"type": "output", "text": f"  error: {exc}\n"})
            result = "handled"
        finally:
            sys.stdout = orig_stdout
            repl._console = orig_console
        if result == "break":
            self._emit_threadsafe({"type": "exit"})

    # --------------------------------------------------------------- server
    async def _handler(self, ws) -> None:
        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue()

        # one-time token handshake
        try:
            first = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        except Exception:  # noqa: BLE001
            await ws.close()
            return
        if not isinstance(first, dict) or first.get("token") != self.token:
            await ws.send(json.dumps({"type": "error", "message": "bad token"}))
            await ws.close()
            return

        self._ensure_agent()

        # banner into the pane, then ready
        from .cli import repl
        commands = [{"name": c.name, "summary": c.summary} for c in repl.SLASH_COMMANDS]
        await ws.send(json.dumps({"type": "ready", "header": header_snapshot(self._agent),
                                  "commands": commands}))
        try:
            banner_sink = _Sink(lambda s: self._emit_threadsafe({"type": "output", "text": s}))
            orig_stdout, orig_console = sys.stdout, repl._console
            sys.stdout = banner_sink
            from rich.console import Console
            repl._console = Console(file=banner_sink, force_terminal=True,
                                    color_system="truecolor", width=100, soft_wrap=False)
            repl.banner(self._agent)
        except Exception:  # noqa: BLE001
            pass
        finally:
            sys.stdout = orig_stdout
            repl._console = orig_console

        sender = asyncio.create_task(self._sender(ws))
        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except Exception:  # noqa: BLE001
                    continue
                await self._dispatch(ws, msg)
        except Exception:  # noqa: BLE001 - client vanished/closed abruptly; shut down quietly
            pass
        finally:
            sender.cancel()
            self._stop_event_loop_owner()

    async def _sender(self, ws) -> None:
        assert self._queue is not None
        while True:
            frame = await self._queue.get()
            try:
                await ws.send(json.dumps(frame))
            except Exception:  # noqa: BLE001
                return

    async def _dispatch(self, ws, msg: dict) -> None:
        kind = msg.get("type")
        if kind == "input":
            if self._running:
                return
            text = str(msg.get("text") or "").strip()
            if not text:
                return
            self._running = True
            await self._emit_status(running=True)
            asyncio.create_task(self._run_turn(ws, text))
        elif kind == "interrupt":
            try:
                if self._agent is not None:
                    self._agent.cancel()
            except Exception:  # noqa: BLE001
                pass
        elif kind == "answer":
            if self._answer_event is not None:
                self._answer_value = str(msg.get("value") or "")
                ev, self._answer_event = self._answer_event, None
                ev.set()

    async def _run_turn(self, ws, text: str) -> None:
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, self._do_turn, text)
        finally:
            self._running = False
            try:
                self.store.save(self._agent.session)
            except Exception:  # noqa: BLE001
                pass
            self._emit_threadsafe({"type": "turn_done"})
            await self._emit_status(running=False)

    async def _emit_status(self, *, running: bool) -> None:
        if self._agent is None:
            return
        self._emit_threadsafe({"type": "status", "running": running,
                               "header": header_snapshot(self._agent)})

    def _stop_event_loop_owner(self) -> None:
        end = getattr(self._agent, "end_session", None)
        if callable(end):
            try:
                end()
            except Exception:  # noqa: BLE001
                pass


def start_gateway_thread(config: Config, *, model=None, provider_name=None,
                         session: Session | None = None, store: SessionStore | None = None,
                         auto: bool = False):
    """Start the gateway on a daemon thread and return ``(host, port, token, stop)``.

    The launcher uses this to run the WS server in-process while the child Node/Ink client
    owns the real terminal."""
    import websockets

    gateway = TuiGateway(config, model=model, provider_name=provider_name,
                         session=session, store=store, auto=auto)
    ready = threading.Event()
    info: dict[str, Any] = {}
    loop_holder: dict[str, asyncio.AbstractEventLoop] = {}

    server_holder: dict[str, Any] = {}

    def run() -> None:
        loop = asyncio.new_event_loop()
        loop_holder["loop"] = loop
        asyncio.set_event_loop(loop)

        async def boot():
            server = await websockets.serve(gateway._handler, "127.0.0.1", 0)
            server_holder["server"] = server
            sock = server.sockets[0].getsockname()
            info["host"], info["port"] = sock[0], sock[1]
            info["token"] = gateway.token
            ready.set()

        loop.run_until_complete(boot())
        try:
            loop.run_forever()  # serve until stop() asks the loop to stop
        finally:
            server = server_holder.get("server")
            if server is not None:
                server.close()
                try:
                    loop.run_until_complete(server.wait_closed())
                except Exception:  # noqa: BLE001
                    pass
            loop.close()

    thread = threading.Thread(target=run, name="aegis-tui-gateway", daemon=True)
    thread.start()
    ready.wait(timeout=10)

    def stop() -> None:
        loop = loop_holder.get("loop")
        if loop is not None:
            loop.call_soon_threadsafe(loop.stop)

    return info.get("host", "127.0.0.1"), info.get("port", 0), gateway.token, stop
