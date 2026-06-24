"""Full-screen terminal surface for AEGIS.

Typing ``aegis`` (with an interactive TTY) opens this: a real full-screen app with an
alternate screen, a live header, a scrollable conversation region, a status bar, and a
persistent composer with slash-command completion and mouse support.

It is a thin *surface* — all agent behaviour, event rendering, tool cards, thinking
boxes and slash commands come from :mod:`aegis.cli.repl`. The turn runs on a worker
thread while every byte it would normally print to the terminal is captured (with ANSI
colour intact) and streamed into the conversation pane. That way the full-screen surface
and the classic line REPL render identically and never drift.

prompt_toolkit (already an AEGIS dependency) is the only requirement; when it is missing
or the terminal is unsuitable, :func:`run_fullscreen` raises
:class:`~aegis.cli.repl._FullscreenUnavailable` and the caller falls back to the REPL.
"""

from __future__ import annotations

import sys
import threading
import time
from typing import TYPE_CHECKING, Any

from ..config import Config, logs_dir
from ..session import Session, SessionStore
from . import repl
from .repl import _FullscreenUnavailable

if TYPE_CHECKING:  # pragma: no cover - typing only
    from prompt_toolkit.application import Application

# Conversation scrollback is capped so a very long session can't grow the in-memory
# transcript without bound; older output scrolls off the top.
_MAX_TRANSCRIPT = 400_000

_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


class _Capture:
    """A stdout-shaped sink that feeds written text into the conversation pane.

    It reports ``isatty() == True`` so Rich (used by ``repl._out``) keeps emitting ANSI
    colour, which prompt_toolkit then renders faithfully.
    """

    encoding = "utf-8"

    def __init__(self, sink):
        self._sink = sink

    def write(self, text: str) -> int:
        if text:
            self._sink(text)
        return len(text)

    def flush(self) -> None:  # noqa: D401 - file-like no-op
        return None

    def isatty(self) -> bool:
        return True

    def fileno(self) -> int:  # Rich probes this for width; fall back to its default.
        raise OSError("capture has no fileno")


class FullScreenApp:
    """Owns the prompt_toolkit Application and the worker that runs agent turns."""

    def __init__(self, config: Config, runner, agent, store: SessionStore):
        self.config = config
        self.runner = runner
        self.agent = agent
        self.store = store

        self._lock = threading.Lock()
        self._transcript = ""
        self._running = False
        self._follow = True
        self._spin_started = 0.0
        self._worker: threading.Thread | None = None

        # Approval / clarify bridge: the worker thread blocks on these while the user
        # answers in the composer.
        self._answer_event: threading.Event | None = None
        self._answer_value = ""
        self._answer_label = ""
        self._answer_secret = False

        self._build_ui()

    # ----------------------------------------------------------------- transcript
    def _append(self, text: str) -> None:
        with self._lock:
            self._transcript += text
            if len(self._transcript) > _MAX_TRANSCRIPT:
                self._transcript = self._transcript[-_MAX_TRANSCRIPT:]
        self._follow = True
        self._invalidate()

    def _invalidate(self) -> None:
        app = getattr(self, "app", None)
        if app is not None:
            try:
                app.invalidate()
            except Exception:  # noqa: BLE001 - invalidate is best-effort
                pass

    # ------------------------------------------------------------------- ui build
    def _build_ui(self) -> None:
        from prompt_toolkit.application import Application
        from prompt_toolkit.buffer import Buffer
        from prompt_toolkit.formatted_text import ANSI, HTML
        from prompt_toolkit.history import FileHistory
        from prompt_toolkit.key_binding import merge_key_bindings
        from prompt_toolkit.key_binding.defaults import load_key_bindings
        from prompt_toolkit.layout import Layout
        from prompt_toolkit.layout.containers import (
            Float,
            FloatContainer,
            HSplit,
            VSplit,
            Window,
            WindowAlign,
        )
        from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
        from prompt_toolkit.layout.dimension import Dimension
        from prompt_toolkit.layout.menus import CompletionsMenu
        from prompt_toolkit.layout.processors import BeforeInput
        from prompt_toolkit.styles import Style

        uni = repl._repl_unicode_enabled()

        # --- conversation pane -------------------------------------------------
        def get_body():
            with self._lock:
                text = self._transcript
            return ANSI(text)

        self.body_control = FormattedTextControl(get_body, focusable=False)
        self.body_window = Window(
            content=self.body_control,
            wrap_lines=True,
            always_hide_cursor=True,
        )

        # --- top bar -----------------------------------------------------------
        def get_title():
            agent = self.agent
            model = str(getattr(getattr(agent, "provider", None), "model", "") or "?")
            session = getattr(agent, "session", None)
            sid = str(getattr(session, "title", "") or getattr(session, "id", "") or "")
            brand = "◆ AEGIS" if uni else "AEGIS"
            return HTML(
                f" <b>{brand}</b>  <style fg='{repl.TERM_MUTED}'>{model}</style>"
                f"  <style fg='{repl.TERM_MUTED}'>· {sid[:28]}</style>"
            )

        hint = "/help · ↵ send · ^C stop · ^D quit · ⇞/⇟ scroll" if uni \
            else "/help  Enter send  ^C stop  ^D quit  PgUp/PgDn scroll"
        top_bar = VSplit([
            Window(FormattedTextControl(get_title), style="class:topbar", height=1),
            Window(FormattedTextControl(lambda: hint + " "), style="class:topbar",
                   align=WindowAlign.RIGHT, height=1),
        ])

        # --- status bar --------------------------------------------------------
        def get_status():
            if self._running:
                frame = _SPINNER[int((time.time() - self._spin_started) * 8) % len(_SPINNER)] if uni else "*"
                elapsed = time.time() - self._spin_started
                label = "answer" if self._answer_event is not None else "working"
                return HTML(
                    f" <style fg='{repl.TERM_AMBER}'><b>{frame}</b> {label}…</style>"
                    f" <style fg='{repl.TERM_MUTED}'>{elapsed:4.1f}s · ^C to stop</style>"
                )
            try:
                return repl._bottom_toolbar(self.agent)
            except Exception:  # noqa: BLE001
                return ""

        status_bar = Window(FormattedTextControl(get_status), style="class:status", height=1)

        # --- composer ----------------------------------------------------------
        history = FileHistory(str(logs_dir() / "repl_history"))
        self.buffer = Buffer(
            multiline=False,
            completer=repl.make_slash_completer(),
            complete_while_typing=True,
            history=history,
            accept_handler=self._accept,
        )

        def prompt_text():
            if self._answer_event is not None:
                return [("class:prompt", f" {self._answer_label} ")]
            label = "aegis"
            profile = str(self.config.get("agent.personality") or "").strip()
            if profile:
                label += f":{profile}"
            arrow = "❯" if uni else ">"
            return [("class:prompt", f" {label} {arrow} ")]

        input_window = Window(
            BufferControl(
                buffer=self.buffer,
                input_processors=[
                    BeforeInput(prompt_text),
                    _ConditionalPassword(self),
                ],
            ),
            height=Dimension(min=1, max=8),
            wrap_lines=True,
            style="class:composer",
        )

        root = FloatContainer(
            content=HSplit([
                top_bar,
                Window(height=1, char="─" if uni else "-", style="class:rule"),
                self.body_window,
                status_bar,
                input_window,
            ]),
            floats=[
                Float(
                    xcursor=True,
                    ycursor=True,
                    content=CompletionsMenu(max_height=10, scroll_offset=1),
                ),
            ],
        )

        style = Style.from_dict({
            "topbar": f"bg:{repl.TERM_PANEL} {repl.TERM_AMBER}",
            "status": f"bg:{repl.TERM_PANEL}",
            "rule": repl.TERM_AMBER_DARK,
            "prompt": f"{repl.TERM_AMBER} bold",
            "composer": "",
            "completion-menu.completion": f"bg:{repl.TERM_PANEL} {repl.TERM_TEXT}",
            "completion-menu.completion.current": f"bg:{repl.TERM_AMBER} #1b1d22",
        })

        self.app: Application = Application(
            layout=Layout(root, focused_element=input_window),
            key_bindings=merge_key_bindings([load_key_bindings(), self._key_bindings()]),
            style=style,
            full_screen=True,
            mouse_support=True,
        )

    # ------------------------------------------------------------- key bindings
    def _key_bindings(self):
        from prompt_toolkit.key_binding import KeyBindings

        kb = KeyBindings()

        @kb.add("c-c", eager=True)
        def _(event):
            if self._running:
                try:
                    self.agent.cancel()
                except Exception:  # noqa: BLE001
                    pass
                self._append(_red("\n  ⏹ interrupting — stopping this turn…\n"))
            elif self.buffer.text:
                self.buffer.reset()
            else:
                event.app.exit()

        @kb.add("c-d", eager=True)
        def _(event):
            if not self.buffer.text and not self._running:
                event.app.exit()

        @kb.add("pageup")
        def _(event):
            self._scroll(-self._page())

        @kb.add("pagedown")
        def _(event):
            self._scroll(self._page())

        @kb.add("s-up")
        def _(event):
            self._scroll(-3)

        @kb.add("s-down")
        def _(event):
            self._scroll(3)

        @kb.add("c-l")
        def _(event):
            self._follow = True
            self._invalidate()

        return kb

    def _page(self) -> int:
        info = getattr(self.body_window, "render_info", None)
        if info is not None:
            try:
                return max(1, info.window_height - 1)
            except Exception:  # noqa: BLE001
                pass
        return 10

    def _scroll(self, delta: int) -> None:
        info = getattr(self.body_window, "render_info", None)
        if info is None:
            return
        try:
            max_scroll = max(0, info.content_height - info.window_height)
        except Exception:  # noqa: BLE001
            max_scroll = 0
        new = self.body_window.vertical_scroll + delta
        new = max(0, min(new, max_scroll))
        self.body_window.vertical_scroll = new
        self._follow = new >= max_scroll
        self._invalidate()

    def _pin_if_following(self) -> None:
        if not self._follow:
            return
        info = getattr(self.body_window, "render_info", None)
        if info is None:
            return
        try:
            max_scroll = max(0, info.content_height - info.window_height)
        except Exception:  # noqa: BLE001
            max_scroll = 0
        self.body_window.vertical_scroll = max_scroll

    # ------------------------------------------------------------------ composer
    def _accept(self, buf) -> bool:
        text = buf.text
        # Answering an approval / clarify question routed from the worker.
        if self._answer_event is not None:
            self._answer_value = text
            ev = self._answer_event
            self._answer_event = None
            self._answer_secret = False
            ev.set()
            return False
        if self._running:
            return False  # busy; ^C to stop, then resubmit
        if not text.strip():
            return False
        self._start_turn(text)
        return False

    def _start_turn(self, text: str) -> None:
        arrow = "❯" if repl._repl_unicode_enabled() else ">"
        self._append(f"\n\x1b[1;38;2;214;161;94m{arrow} {text}\x1b[0m\n")
        self._running = True
        self._spin_started = time.time()
        self._worker = threading.Thread(target=self._run_turn, args=(text,),
                                         name="aegis-fullscreen-turn", daemon=True)
        self._worker.start()
        self._start_spinner()

    def _start_spinner(self) -> None:
        def spin():
            while self._running:
                self._invalidate()
                time.sleep(0.12)
        threading.Thread(target=spin, name="aegis-fullscreen-spinner", daemon=True).start()

    def _run_turn(self, text: str) -> None:
        orig_stdout = sys.stdout
        orig_console = repl._console
        capture = _Capture(self._append)
        sys.stdout = capture  # _raw()/print() flow into the conversation pane
        repl._console = _make_console(capture, self._term_width())
        result = "handled"
        try:
            result = repl.process_terminal_input(
                text, self.agent, self.runner, self.store,
                on_event=repl.Renderer(self.config), surface="repl",
            )
        except Exception as exc:  # noqa: BLE001 - surface any turn error in the pane
            self._append(_red(f"\n  error: {exc}\n"))
        finally:
            sys.stdout = orig_stdout
            repl._console = orig_console
            self._running = False
            self._invalidate()
        if result == "break":
            try:
                self.app.exit()
            except Exception:  # noqa: BLE001
                pass

    def _term_width(self) -> int:
        try:
            return max(40, self.app.output.get_size().columns)
        except Exception:  # noqa: BLE001
            return 100

    # --------------------------------------------------------- approval bridge
    def await_answer(self, label: str, *, secret: bool = False) -> str:
        """Block the worker thread until the user answers in the composer."""
        ev = threading.Event()
        self._answer_label = label
        self._answer_secret = secret
        self._answer_value = ""
        self._answer_event = ev
        self._invalidate()
        ev.wait()
        return self._answer_value

    # ----------------------------------------------------------------- run loop
    def run(self) -> None:
        repl.banner(self.agent)  # captured below into the pane
        # Render the startup banner through the same capture path so it appears in-pane.
        orig_stdout = sys.stdout
        orig_console = repl._console
        capture = _Capture(self._append)
        sys.stdout = capture
        repl._console = _make_console(capture, 100)
        try:
            repl.banner(self.agent)
        except Exception:  # noqa: BLE001
            pass
        finally:
            sys.stdout = orig_stdout
            repl._console = orig_console

        # Keep the pane pinned to the bottom while following new output.
        from prompt_toolkit.filters import Condition  # noqa: F401 (kept for clarity)

        original_write = self.body_window._write_to_screen

        def write_to_screen(*args, **kwargs):
            self._pin_if_following()
            return original_write(*args, **kwargs)

        self.body_window._write_to_screen = write_to_screen  # type: ignore[assignment]

        try:
            self.app.run()
        finally:
            end = getattr(self.agent, "end_session", None)
            if callable(end):
                try:
                    end()
                except Exception:  # noqa: BLE001
                    pass


def _ConditionalPassword(app: FullScreenApp):
    """A processor that masks the composer only while answering a secret prompt."""
    from prompt_toolkit.layout.processors import Processor, Transformation

    class _P(Processor):
        def apply_transformation(self, ti):
            if app._answer_event is not None and app._answer_secret:
                masked = [(style, "*" * len(text)) for style, text in ti.fragments]
                return Transformation(masked)
            return Transformation(ti.fragments)

    return _P()


def _make_console(file, width: int):
    from rich.console import Console
    return Console(file=file, force_terminal=True, color_system="truecolor",
                   width=width, soft_wrap=False)


def _red(text: str) -> str:
    return f"\x1b[38;2;233;110;110m{text}\x1b[0m"


def _fullscreen_approver(app: FullScreenApp, auto: bool):
    def approver(prompt_text: str):
        if auto:
            return True
        ans = app.await_answer(f"{prompt_text} [y/N/a]").strip().lower()
        if ans in ("a", "always"):
            return "always"
        return ans in ("y", "yes")
    return approver


def _fullscreen_asker(app: FullScreenApp):
    def asker(question: str, choices: list[str]) -> str:
        lines = [f"\n  ❓ {question}"]
        for i, c in enumerate(choices, 1):
            lines.append(f"     {i}. {c}")
        app._append("\x1b[38;2;111;183;216m" + "\n".join(lines) + "\x1b[0m\n")
        ans = app.await_answer("answer ❯").strip()
        if choices and ans.isdigit() and 1 <= int(ans) <= len(choices):
            return choices[int(ans) - 1]
        return ans
    return asker


def _fullscreen_secret_capture(app: FullScreenApp):
    def capture(key: str, prompt: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        from ..secret_capture import store_secret_value
        app._append(f"\x1b[38;2;214;161;94m\n  🔑 {prompt}\x1b[0m\n")
        value = app.await_answer(f"{key} ❯", secret=True)
        return store_secret_value(key, value)
    return capture


def run_fullscreen(config: Config, *, model=None, provider_name=None,
                   session: Session | None = None, store: SessionStore | None = None,
                   auto: bool = False) -> None:
    """Launch the full-screen terminal surface. Raises ``_FullscreenUnavailable`` when the
    environment can't support it so the caller can fall back to the classic REPL."""
    try:
        import prompt_toolkit  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        raise _FullscreenUnavailable("prompt_toolkit not installed") from exc

    store = store or SessionStore()
    session = session or Session.create()

    # Placeholder so the bridge closures can reference the app before it exists.
    holder: dict[str, FullScreenApp] = {}

    def approver(prompt_text):
        return _fullscreen_approver(holder["app"], auto)(prompt_text)

    def asker(question, choices):
        return _fullscreen_asker(holder["app"])(question, choices)

    def secret_capture(key, prompt, metadata=None):
        return _fullscreen_secret_capture(holder["app"])(key, prompt, metadata)

    runner, agent = repl.build_terminal_agent(
        config, model=model, provider_name=provider_name,
        session=session, store=store, auto=auto,
        approver=approver, asker=asker, secret_capture=secret_capture,
    )

    try:
        app = FullScreenApp(config, runner, agent, store)
    except Exception as exc:  # noqa: BLE001 - any layout/dep failure → REPL fallback
        raise _FullscreenUnavailable(str(exc)) from exc
    holder["app"] = app
    app.run()
