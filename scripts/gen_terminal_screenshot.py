#!/usr/bin/env python3
"""Emit a representative AEGIS terminal frame as truecolor ANSI for `freeze`.

Regenerate assets/terminal.png (requires charmbracelet/freeze on PATH):

    python scripts/gen_terminal_screenshot.py > /tmp/frame.ansi
    freeze /tmp/frame.ansi -o assets/terminal.png \\
      --window --background "#1b1d22" --padding "20,24" \\
      --border.radius 8 --border.width 1 --border.color "#3a3f47"
"""
W = 92
AMBER = "214;161;94"
GREEN = "126;207;143"
CYAN = "111;183;216"
RED = "233;110;110"
MUTED = "143;150;143"
TEXT = "243;241;232"
PANEL = "38;42;49"
R = "\x1b[0m"

def fg(c): return f"\x1b[38;2;{c}m"
def bar(segments):
    """A full-width bar with PANEL background; segments = list of (color, text)."""
    body = "".join(f"{fg(c)}{t}" for c, t in segments)
    raw = "".join(t for _, t in segments)
    pad = " " * max(0, W - len(raw))
    return f"\x1b[48;2;{PANEL}m{body}{pad}{R}"

def line(*parts):  # parts: (color, text, bold?)
    out = ""
    for p in parts:
        c, t = p[0], p[1]
        b = "\x1b[1m" if len(p) > 2 and p[2] else ""
        out += f"{b}{fg(c)}{t}{R}"
    return out

def tool(icon, name, preview, secs, extra=""):
    return (f"  {fg(AMBER)}{icon}{R} {fg(MUTED)}\x1b[1m{name}{R} "
            f"{fg(MUTED)}{preview}{R}  {fg(GREEN)}✓ {secs}{R}{extra}")

rows = []
rows.append(bar([(AMBER, " ◆ AEGIS "), (MUTED, " claude-sonnet-4-6 "),
                 (MUTED, "· refactor-auth "), (MUTED, "· v0.1.0 ")]))
rows.append("")
rows.append(line((AMBER, "❯ refactor the auth module to use the new token flow", True)))
rows.append("")
rows.append(f"  {fg(MUTED)}· thinking complete (1,284 chars){R}")
rows.append(tool("▤", "read_file", "src/auth.py", "0.3s"))
rows.append(tool("⌕", "search", "token_flow  (4 hits)", "0.2s"))
rows.append(tool("✎", "edit_file", "src/auth.py", "0.5s",
                 extra=f"  {fg(GREEN)}+12{R} {fg(RED)}-4{R}"))
rows.append(tool("$", "bash", "pytest tests/test_auth.py -q", "1.8s",
                 extra=f"  {fg(MUTED)}14 passed{R}"))
rows.append("")
rows.append(line((TEXT, "Done. I moved auth onto the token-exchange flow: authenticate() now calls")))
rows.append(line((TEXT, "exchange_token() and refreshes on 401. Added 3 tests for the refresh path —")))
rows.append(line((TEXT, "all green. The old cookie path is removed; nothing else referenced it.")))
rows.append("")
rows.append(bar([(MUTED, " ready "), (MUTED, "│ ctx "), (GREEN, "███░░░░░░░ 32% (64.0k/200.0k)"),
                 (MUTED, " │ 12.4k↑ 3.1k↓ │ $0.0312 │ summary/medium │ auto ")]))
rows.append(line((AMBER, " aegis ❯ ", True),
                 (MUTED, "message or /command · ↑ history · \\ + ↵ newline · ⇞ scroll")))

print("\n".join(rows))
