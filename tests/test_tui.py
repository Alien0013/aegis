"""Terminal UI compatibility command."""

from __future__ import annotations

from argparse import Namespace
from types import SimpleNamespace


def test_cli_parser_accepts_tui_alias():
    from aegis.cli.main import build_parser

    parser = build_parser()
    args = parser.parse_args(["tui", "--once", "--no-color"])

    assert args.command == "tui"
    assert args.once is True
    assert args.no_color is True


def test_tui_once_delegates_to_status(monkeypatch):
    from aegis.cli import tui
    from aegis.config import Config

    calls = []
    monkeypatch.setattr(tui, "_render_status", lambda config: calls.append(config) or 0)

    cfg = Config.load()
    assert tui.cmd_tui(Namespace(once=True, watch=False, interval=5.0), cfg) == 0
    assert calls == [cfg]


def test_tui_noninteractive_delegates_to_status(monkeypatch):
    from aegis.cli import tui
    from aegis.config import Config

    calls = []
    monkeypatch.setattr(tui, "_render_status", lambda config: calls.append(config) or 0)
    monkeypatch.setattr(tui.sys, "stdin", SimpleNamespace(isatty=lambda: False))
    monkeypatch.setattr(tui.sys, "stdout", SimpleNamespace(isatty=lambda: True))

    cfg = Config.load()
    assert tui.cmd_tui(Namespace(once=False, watch=False, interval=5.0), cfg) == 0
    assert calls == [cfg]


def test_tui_interactive_opens_terminal_agent(monkeypatch):
    from aegis.cli import tui
    from aegis.config import Config

    calls = []
    monkeypatch.setattr(tui, "_open_terminal_agent", lambda config: calls.append(config) or 0)
    monkeypatch.setattr(tui.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(tui.sys, "stdout", SimpleNamespace(isatty=lambda: True))

    cfg = Config.load()
    assert tui.cmd_tui(Namespace(once=False, watch=False, interval=5.0), cfg) == 0
    assert calls == [cfg]
