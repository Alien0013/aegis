"""Full-screen terminal cockpit for AEGIS."""

from __future__ import annotations

import sys
import threading
from typing import Any

from ..config import Config
from ..session import Session, SessionStore
from ..surface import SurfaceRunner
from . import repl


def run_fullscreen(
    config: Config,
    *,
    model: str | None = None,
    provider_name: str | None = None,
    session: Session | None = None,
    store: SessionStore | None = None,
    auto: bool = False,
) -> None:
    """Run the prompt_toolkit full-screen TUI, falling back to the REPL when needed."""

    try:
        from prompt_toolkit.application import Application
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.layout import HSplit, Layout, Window
        from prompt_toolkit.layout.controls import FormattedTextControl
        from prompt_toolkit.widgets import Frame, TextArea
    except Exception:  # noqa: BLE001
        repl.interactive(config, model=model, provider_name=provider_name,
                         session=session, store=store, auto=auto)
        return

    if not sys.stdin.isatty() or not sys.stdout.isatty():
        repl.interactive(config, model=model, provider_name=provider_name,
                         session=session, store=store, auto=auto)
        return

    store = store or SessionStore()
    session = session or Session.create()
    runner = SurfaceRunner(config, store=store, include_mcp=True)
    agent = runner.make_agent(
        session=session,
        model=model,
        provider_name=provider_name,
        approver=repl.make_approver(auto),
        asker=repl.make_asker(),
        include_mcp=True,
    )
    store.save(agent.session)

    transcript = TextArea(text=_render_session(agent.session), scrollbar=True,
                          focusable=True, wrap_lines=True)
    events = TextArea(text="", height=8, scrollbar=True, focusable=True, wrap_lines=True)
    composer = TextArea(height=3, prompt=">>> ", multiline=True, wrap_lines=True,
                        completer=repl.make_slash_completer(), complete_while_typing=True)
    busy = threading.Event()
    queued_inputs: list[Any] = []
    streaming = {"active": False}
    status_state = repl.TerminalStatusState()
    app_ref: dict[str, Any] = {}

    def invalidate() -> None:
        app = app_ref.get("app")
        if app is not None:
            app.invalidate()

    def append(area: Any, text: str) -> None:
        if not text:
            return
        area.text = _tail(area.text + text, 160_000)
        try:
            area.buffer.cursor_position = len(area.text)
        except Exception:  # noqa: BLE001
            pass
        invalidate()

    def set_transcript_from_session() -> None:
        transcript.text = _render_session(agent.session)
        try:
            transcript.buffer.cursor_position = len(transcript.text)
        except Exception:  # noqa: BLE001
            pass
        invalidate()

    def refresh_transcript_if_changed(before: tuple[str, int, int]) -> None:
        if _session_signature(agent.session) != before:
            set_transcript_from_session()

    def set_status() -> list[tuple[str, str]]:
        provider = getattr(agent, "provider", None)
        run_id, trace, _turn = repl._run_refs(agent)
        bits = [
            "AEGIS TUI",
            "busy" if busy.is_set() else "ready",
            getattr(provider, "model", config.get("model.default", "")),
            agent.session.id,
        ]
        progress = status_state.segment()
        if progress:
            bits.append(progress)
        if run_id:
            bits.append(f"run {run_id[:12]}")
        if trace:
            bits.append(f"trace {trace[:12]}")
        return [("class:status", "  " + " | ".join(str(b) for b in bits if b) + "  ")]

    def on_event(event: dict[str, Any]) -> None:
        status_state.update(event)
        etype = event.get("type")
        if etype == "assistant_delta":
            streaming["active"] = True
            append(transcript, str(event.get("text") or ""))
        elif etype == "assistant_message":
            text = str(event.get("text") or "")
            if text and not streaming["active"]:
                append(transcript, f"\nassistant> {text}\n")
            calls = event.get("tool_calls") or []
            if calls:
                append(events, f"\nassistant requested {len(calls)} tool call(s)\n")
        elif etype == "final":
            if streaming["active"]:
                append(transcript, "\n")
            streaming["active"] = False
        else:
            line = _event_line(event)
            if line:
                append(events, line + "\n")

    def start_next_queued() -> None:
        if busy.is_set() or not queued_inputs:
            return
        item = queued_inputs.pop(0)
        if isinstance(item, dict):
            text = str(item.get("text") or "")
            append(events, f"\nqueued run> {text}\n")
            start_turn(
                text,
                display_text=str(item.get("display_text") or text),
                add_profile_directive=bool(item.get("add_profile_directive", True)),
                meta=item.get("meta") if isinstance(item.get("meta"), dict) else None,
                include_wakeups=bool(item.get("include_wakeups", True)),
            )
            return
        text = str(item)
        append(events, f"\nqueued run> {text}\n")
        handle_ready_input(text)

    def start_turn(text: str, *, display_text: str | None = None,
                   add_profile_directive: bool = True,
                   meta: dict | None = None,
                   include_wakeups: bool = True) -> None:
        append(transcript, f"\nuser> {display_text or text}\nassistant> ")
        busy.set()

        def work() -> None:
            try:
                repl.run_terminal_turn(
                    text,
                    agent,
                    runner,
                    store,
                    surface="tui",
                    on_event=on_event,
                    notify=lambda line: append(events, f"\n{line}\n"),
                    add_profile_directive=add_profile_directive,
                    meta=meta,
                    include_wakeups=include_wakeups,
                )
            except Exception as exc:  # noqa: BLE001
                append(events, f"\nerror: {type(exc).__name__}: {exc}\n")
            finally:
                busy.clear()
                streaming["active"] = False
                enqueue_process_notifications(start=False)
                invalidate()
                start_next_queued()

        threading.Thread(target=work, daemon=True).start()

    def enqueue_process_notifications(*, start: bool = True) -> None:
        notes = repl.drain_process_notification_events()
        for event, text in notes:
            queued_inputs.append({
                "text": text,
                "display_text": text,
                "add_profile_directive": False,
                "include_wakeups": False,
                "meta": repl._process_notification_meta(event),
            })
            append(events, f"\nbackground process notification: {event.get('session_id', '')}\n")
        if start and not busy.is_set():
            start_next_queued()

    def process_notification_loop() -> None:
        while not stop_notifications.wait(0.5):
            try:
                enqueue_process_notifications()
            except Exception:  # noqa: BLE001
                pass

    def run_slash_async(command: str) -> None:
        busy.set()

        def work() -> None:
            before = _session_signature(agent.session)
            try:
                result, output = _capture_slash(
                    command,
                    agent,
                    runner=runner,
                    store=store,
                    surface="tui",
                    on_event=on_event,
                )
                if output:
                    append(events, output.rstrip() + "\n")
                refresh_transcript_if_changed(before)
                if result == "break":
                    app_ref["app"].exit()
            except Exception as exc:  # noqa: BLE001
                append(events, f"\nerror: {type(exc).__name__}: {exc}\n")
            finally:
                busy.clear()
                streaming["active"] = False
                enqueue_process_notifications(start=False)
                invalidate()
                start_next_queued()

        threading.Thread(target=work, daemon=True).start()

    def handle_ready_input(text: str) -> None:
        if text.startswith(("/goal", "/subgoal")):
            goal_prompt = repl.handle_goal_command(
                text,
                agent,
                store,
                out=lambda line, _style=None: append(events, line + "\n"),
            )
            if goal_prompt:
                start_turn(goal_prompt)
            return
        if text.startswith("/"):
            name = text.split()[0].lower()
            if name in {"/retry", "/compress"}:
                run_slash_async(text)
                return
            before = _session_signature(agent.session)
            result, output = _capture_slash(
                text,
                agent,
                runner=runner,
                store=store,
                surface="tui",
                on_event=on_event,
            )
            if output:
                append(events, output.rstrip() + "\n")
            refresh_transcript_if_changed(before)
            if result == "break":
                app_ref["app"].exit()
            return
        start_turn(text)

    def submit() -> None:
        text = composer.text.strip()
        if not text:
            return
        composer.text = ""
        if busy.is_set():
            if text.startswith("/busy"):
                result, output = _capture_slash(
                    text,
                    agent,
                    runner=runner,
                    store=store,
                    surface="tui",
                    on_event=on_event,
                )
                if output:
                    append(events, output.rstrip() + "\n")
                if result == "break":
                    app_ref["app"].exit()
                return
            action = _handle_busy_input(text, agent, config, queued_inputs)
            if action == "queued":
                append(events, f"\nqueued> {text}\n")
            elif action == "steered":
                append(events, f"\nsteered> {text}\n")
            elif action == "interrupt":
                append(events, f"\ninterrupting> {text}\n")
            elif action == "cancelled":
                append(events, "\ninterrupt requested\n")
            else:
                append(events, "\ninput ignored while busy\n")
            return
        handle_ready_input(text)

    kb = KeyBindings()

    @kb.add("enter")
    def _submit(event):  # noqa: ANN001
        submit()

    @kb.add("c-j")
    def _newline(event):  # noqa: ANN001
        composer.buffer.insert_text("\n")

    @kb.add("c-c")
    def _quit(event):  # noqa: ANN001
        if busy.is_set():
            agent.cancel()
            append(events, "\ninterrupt requested\n")
            return
        event.app.exit()

    @kb.add("c-l")
    def _clear_events(event):  # noqa: ANN001
        events.text = ""

    root = HSplit([
        Window(FormattedTextControl(
            lambda: [("class:title", " ▟▛ AEGIS  "),
                     ("class:title.dim", "full-screen agent  ·  type /help for commands ")]),
            height=1),
        Frame(transcript, title="Session"),
        Frame(events, title="Activity"),
        Frame(composer, title="Composer  ·  Enter send · Ctrl-J newline · Ctrl-L clear · Ctrl-C quit/stop"),
        Window(FormattedTextControl(set_status), height=1),
    ])
    app = Application(
        layout=Layout(root, focused_element=composer),
        key_bindings=kb,
        full_screen=True,
        mouse_support=True,
        style=None,
    )
    app_ref["app"] = app
    stop_notifications = threading.Event()
    threading.Thread(target=process_notification_loop, daemon=True).start()
    try:
        app.run()
    finally:
        stop_notifications.set()


def _render_session(session: Session) -> str:
    lines: list[str] = []
    for message in session.messages[-40:]:
        if message.role in ("user", "assistant") and message.content:
            lines.append(f"{message.role}> {message.content}")
    return "\n\n".join(lines)


def _session_signature(session: Session) -> tuple[str, int, int]:
    messages = getattr(session, "messages", []) or []
    chars = sum(len(getattr(m, "content", "") or "") for m in messages)
    return (getattr(session, "id", ""), len(messages), chars)


def _event_line(event: dict[str, Any]) -> str:
    etype = event.get("type", "")
    if etype == "iteration":
        return f"iteration {event.get('n')}/{event.get('max')}"
    if etype == "reasoning_delta":
        return "thinking..."
    if etype == "tool_start":
        args = event.get("args") or {}
        detail = args.get("command") or args.get("path") or args.get("url") or args.get("query") or ""
        return f"tool start: {event.get('name')} {str(detail)[:100]}".rstrip()
    if etype == "tool_result":
        mark = "error" if event.get("is_error") else "ok"
        return f"tool {mark}: {event.get('name')} - {event.get('summary', '')}"
    if etype in {"compacting", "budget_exhausted", "cancelled", "continuation"}:
        return str(etype)
    if etype == "error":
        return f"error: {event.get('message', '')}"
    return ""


def _capture_slash(
    command: str,
    agent: Any,
    *,
    runner: SurfaceRunner | None = None,
    store: SessionStore | None = None,
    surface: str = "tui",
    on_event=None,
) -> tuple[str, str]:
    lines: list[str] = []
    old = repl._out

    def capture(text: str = "", style: str | None = None) -> None:
        lines.append(str(text))

    repl._out = capture
    try:
        result = repl.handle_slash(
            command,
            agent,
            runner=runner,
            store=store,
            surface=surface,
            on_event=on_event,
        )
    finally:
        repl._out = old
    return result, "\n".join(lines)


def _busy_mode(config: Config) -> str:
    mode = str(config.get("gateway.busy_mode", "queue") or "queue")
    return mode if mode in {"queue", "steer", "interrupt"} else "queue"


def _handle_busy_input(text: str, agent: Any, config: Config, pending: list[str]) -> str:
    """Apply TUI busy-mode semantics while a turn is already running."""
    raw = text.strip()
    if not raw:
        return "ignored"
    if raw.lower() in {"stop", "/stop"}:
        agent.cancel()
        return "cancelled"
    mode = _busy_mode(config)
    if mode == "steer":
        return "steered" if agent.steer(raw) else "ignored"
    if mode == "interrupt":
        agent.cancel()
        pending[:] = [raw]
        return "interrupt"
    pending.append(raw)
    return "queued"


def _tail(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[-limit:]
