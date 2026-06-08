"""Inline arrow-key selectors for onboarding — single-select and checkbox multi-select.

Renders in place (no fullscreen takeover): ↑/↓ to move, Space to toggle, Enter to
confirm. Falls back to None when the session isn't a real terminal, so callers keep
their typed-input path (and tests, which pass fake input, are unaffected).
"""

from __future__ import annotations

import sys

CYAN = "\x1b[36m"
DIM = "\x1b[2m"
REV = "\x1b[7m"
GREEN = "\x1b[32m"
BOLD = "\x1b[1m"
RST = "\x1b[0m"


def interactive() -> bool:
    try:
        import termios  # noqa: F401
    except Exception:  # noqa: BLE001  (Windows / no termios)
        return False
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except Exception:  # noqa: BLE001
        return False


def _read_key() -> str:
    ch = sys.stdin.read(1)
    if ch == "\x1b":
        seq = sys.stdin.read(2)
        return {"[A": "up", "[B": "down", "[C": "right", "[D": "left"}.get(seq, "esc")
    if ch in ("\r", "\n"):
        return "enter"
    if ch == " ":
        return "space"
    if ch == "\x03":
        return "ctrl-c"
    return ch


def _run(draw, handle) -> bool:
    """Raw-mode key loop. draw(first) renders; handle(key)->'done'|'go'|None."""
    import termios
    import tty
    draw(first=True)
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        while True:
            action = handle(_read_key())
            if action == "done":
                return True
            if action == "cancel":
                return False
            draw(first=False)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def select_one(prompt: str, options: list[tuple[str, str]], default: int = 0) -> str | None:
    """Arrow-key single select. Returns the chosen value, or None if not interactive."""
    if not interactive() or not options:
        return None
    n = len(options)
    idx = max(0, min(default, n - 1))
    sys.stdout.write(f"{BOLD}{CYAN}? {prompt}{RST}  {DIM}(↑/↓ then Enter){RST}\r\n")

    def draw(first=False):
        if not first:
            sys.stdout.write(f"\x1b[{n}A")
        for i, (_, label) in enumerate(options):
            if i == idx:
                sys.stdout.write(f"\x1b[2K  {CYAN}❯{RST} {REV} {label} {RST}\r\n")
            else:
                sys.stdout.write(f"\x1b[2K    {label}\r\n")
        sys.stdout.flush()

    def handle(k):
        nonlocal idx
        if k == "up":
            idx = (idx - 1) % n
        elif k == "down":
            idx = (idx + 1) % n
        elif k.isdigit() and 1 <= int(k) <= n:
            idx = int(k) - 1
        elif k == "enter":
            return "done"
        elif k in ("ctrl-c", "esc"):
            raise KeyboardInterrupt
        return None

    _run(draw, handle)
    sys.stdout.write(f"{DIM}  → {options[idx][1]}{RST}\r\n")
    sys.stdout.flush()
    return options[idx][0]


def select_many(prompt: str, options: list[tuple[str, str]],
                preselected: list[str] | None = None) -> list[str] | None:
    """Checkbox multi-select. Returns chosen values, or None if not interactive."""
    if not interactive() or not options:
        return None
    n = len(options)
    pre = set(preselected or [])
    chosen = {i for i, (v, _) in enumerate(options) if v in pre}
    idx = 0
    sys.stdout.write(f"{BOLD}{CYAN}? {prompt}{RST}  "
                     f"{DIM}(↑/↓ move · Space toggle · Enter confirm){RST}\r\n")

    def draw(first=False):
        if not first:
            sys.stdout.write(f"\x1b[{n}A")
        for i, (_, label) in enumerate(options):
            box = f"{GREEN}◉{RST}" if i in chosen else "◯"
            label_txt = f"{REV} {label} {RST}" if i == idx else f" {label}"
            pointer = f"{CYAN}❯{RST}" if i == idx else " "
            sys.stdout.write(f"\x1b[2K  {pointer} {box}{label_txt}\r\n")
        sys.stdout.flush()

    def handle(k):
        nonlocal idx
        if k == "up":
            idx = (idx - 1) % n
        elif k == "down":
            idx = (idx + 1) % n
        elif k == "space":
            chosen.symmetric_difference_update({idx})
        elif k == "enter":
            return "done"
        elif k in ("ctrl-c", "esc"):
            raise KeyboardInterrupt
        return None

    _run(draw, handle)
    picked = [options[i][0] for i in range(n) if i in chosen]
    labels = ", ".join(options[i][1] for i in range(n) if i in chosen) or "none"
    sys.stdout.write(f"{DIM}  → {labels}{RST}\r\n")
    sys.stdout.flush()
    return picked
