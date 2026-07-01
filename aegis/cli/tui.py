"""Terminal UI compatibility entrypoint.

`aegis tui` is the explicit terminal-agent surface.  It prefers the bundled
Node/Ink UI when a real terminal is available, and falls back to the classic
line REPL when the bundle/runtime is missing or the user asks for `--classic`.
`--once` and non-interactive invocations remain read-only status aliases for
scripts.
"""

from __future__ import annotations

import sys
import time
import os
from argparse import Namespace

from ..config import Config


def _render_status(config: Config) -> int:
    from .main import cmd_status

    return cmd_status(Namespace(json=False), config)


def _open_ink_terminal_agent(
    config: Config,
    *,
    model=None,
    provider_name=None,
    auto: bool = False,
    dev: bool = False,
    session=None,
    store=None,
) -> int:
    from .tui_ink import launch_ink_tui

    launch_ink_tui(config, model=model, provider_name=provider_name, auto=auto, dev=dev, session=session, store=store)
    return 0


def _open_classic_terminal_agent(
    config: Config,
    *,
    model=None,
    provider_name=None,
    auto: bool = False,
    session=None,
    store=None,
) -> int:
    from ..session import Session, SessionStore
    from . import repl

    store = store or SessionStore()
    session = session or store.latest() or Session.create()
    previous = os.environ.get("AEGIS_CLASSIC_TUI")
    os.environ["AEGIS_CLASSIC_TUI"] = "1"
    try:
        repl.interactive(
            config,
            model=model,
            provider_name=provider_name,
            session=session,
            store=store,
            auto=auto,
        )
    finally:
        if previous is None:
            os.environ.pop("AEGIS_CLASSIC_TUI", None)
        else:
            os.environ["AEGIS_CLASSIC_TUI"] = previous
    return 0


def _open_terminal_agent(
    config: Config,
    *,
    model=None,
    provider_name=None,
    auto: bool = False,
    classic: bool = False,
    dev: bool = False,
    session=None,
    store=None,
) -> int:
    from . import repl

    if not classic:
        try:
            return _open_ink_terminal_agent(
                config,
                model=model,
                provider_name=provider_name,
                auto=auto,
                dev=dev,
                session=session,
                store=store,
            )
        except repl._FullscreenUnavailable:
            pass
        except Exception as exc:  # noqa: BLE001
            print(f"Ink terminal failed ({exc}); falling back to classic terminal.", file=sys.stderr)
    return _open_classic_terminal_agent(config, model=model, provider_name=provider_name, auto=auto, session=session, store=store)


def cmd_tui(args: Namespace, config: Config) -> int:
    """Open the terminal agent.

    ``--once`` and non-interactive invocations are compatibility aliases for
    ``aegis status`` so scripts get useful read-only output without launching a
    full-screen UI.
    """

    if getattr(args, "watch", False):
        interval = max(0.5, float(getattr(args, "interval", 5.0) or 5.0))
        try:
            while True:
                _render_status(config)
                time.sleep(interval)
        except KeyboardInterrupt:
            return 0

    if getattr(args, "once", False) or not (sys.stdin.isatty() and sys.stdout.isatty()):
        return _render_status(config)

    from ..session import Session, SessionStore

    store = SessionStore()
    resume = str(getattr(args, "resume", "") or "").strip()
    if resume:
        resolver = getattr(store, "resolve_resume_session_id", None)
        resolved = resolver(resume) if callable(resolver) else None
        session = store.load(resolved or resume)
        if session is None:
            print(f"session '{resume}' not found", file=sys.stderr)
            return 1
    else:
        session = store.latest() or Session.create()

    return _open_terminal_agent(
        config,
        model=getattr(args, "model", None),
        provider_name=getattr(args, "provider", None),
        auto=bool(getattr(args, "yolo", False)),
        classic=bool(getattr(args, "classic", False) or getattr(args, "cli", False)),
        dev=bool(getattr(args, "tui_dev", False)),
        session=session,
        store=store,
    )
