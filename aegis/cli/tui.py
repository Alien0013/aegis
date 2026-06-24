"""Terminal UI compatibility entrypoint.

AEGIS' terminal product surface is the chat REPL.  The old store-backed
``aegis tui`` status dashboard was intentionally removed as a separate UX so the CLI
matches AEGIS' model more closely: one terminal agent surface, with status
available through ``aegis status``.
"""

from __future__ import annotations

import sys
import time
from argparse import Namespace

from ..config import Config


def _render_status(config: Config) -> int:
    from .main import cmd_status

    return cmd_status(Namespace(json=False), config)


def _open_terminal_agent(config: Config) -> int:
    from ..session import Session, SessionStore
    from . import repl

    store = SessionStore()
    repl.interactive(config, session=store.latest() or Session.create(), store=store)
    return 0


def cmd_tui(args: Namespace, config: Config) -> int:
    """Open the terminal agent.

    ``--once`` and non-interactive invocations are kept as compatibility aliases
    for ``aegis status`` so legacy snapshot scripts still get useful read-only
    output without seeing a separate terminal UI.
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

    return _open_terminal_agent(config)
