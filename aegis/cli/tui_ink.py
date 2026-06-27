"""Launch the Node/Ink terminal UI against an in-process Python gateway.

``aegis`` (interactive TTY) starts the Python WebSocket gateway on a daemon thread, then
spawns the bundled Node/Ink client (``aegis/tui_ink/dist/entry.js``) handing it the real
terminal. When the Ink client exits, the gateway is torn down. If Node or the built bundle
isn't available, this raises :class:`~aegis.cli.repl._FullscreenUnavailable` so the caller
falls back to the classic REPL.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
from pathlib import Path

from ..config import Config
from ..session import Session, SessionStore
from .repl import _FullscreenUnavailable


def _ink_entry() -> Path:
    return Path(__file__).resolve().parent.parent / "tui_ink" / "dist" / "entry.js"


def ink_available() -> bool:
    """Whether the Node runtime and the built Ink bundle are both present."""
    return bool(shutil.which("node")) and _ink_entry().is_file()


def launch_ink_tui(config: Config, *, model=None, provider_name=None,
                   session: Session | None = None, store: SessionStore | None = None,
                   auto: bool = False, dev: bool = False) -> None:
    """Start the gateway + Node/Ink client and block until the client exits."""
    node = shutil.which("node")
    if not node:
        raise _FullscreenUnavailable("node runtime not found")
    entry = _ink_entry()
    if not entry.is_file():
        raise _FullscreenUnavailable("ink bundle not built (run: npm --prefix aegis/tui_ink run build)")

    from ..tui_gateway import start_gateway_thread

    host, port, token, stop = start_gateway_thread(
        config, model=model, provider_name=provider_name,
        session=session, store=store, auto=auto,
    )
    if not port:
        stop()
        raise _FullscreenUnavailable("gateway failed to bind")

    env = dict(os.environ)
    env["AEGIS_TUI_WS"] = f"ws://{host}:{port}"
    env["AEGIS_TUI_TOKEN"] = token
    # Hand the configured theme to the Ink client so the terminal palette tracks the
    # dashboard's theme names (AEGIS_TUI_THEME in the environment still wins if set).
    if "AEGIS_TUI_THEME" not in env:
        env["AEGIS_TUI_THEME"] = str(config.get("display.theme", "aegis-dark") or "aegis-dark")
    if dev:
        env["AEGIS_TUI_DEV"] = "1"

    # The child owns the terminal; ignore SIGINT in the parent so ^C reaches Ink, which
    # turns it into an interrupt/exit rather than killing the gateway out from under it.
    prev_sigint = signal.getsignal(signal.SIGINT)
    try:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
    except (ValueError, OSError):
        prev_sigint = None
    try:
        proc = subprocess.Popen([node, str(entry)], env=env)
        proc.wait()
    finally:
        if prev_sigint is not None:
            try:
                signal.signal(signal.SIGINT, prev_sigint)
            except (ValueError, OSError):
                pass
        stop()
