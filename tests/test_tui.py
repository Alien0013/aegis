"""Terminal UI compatibility command."""

from __future__ import annotations

from argparse import Namespace
import os
from types import SimpleNamespace


def test_cli_parser_accepts_tui_alias():
    from aegis.cli.main import build_parser

    parser = build_parser()
    args = parser.parse_args([
        "tui",
        "--once",
        "--no-color",
        "--model",
        "gpt-test",
        "--provider",
        "openrouter",
        "--resume",
        "sess_123",
        "--classic",
        "--dev",
        "--yolo",
    ])

    assert args.command == "tui"
    assert args.once is True
    assert args.no_color is True
    assert args.model == "gpt-test"
    assert args.provider == "openrouter"
    assert args.resume == "sess_123"
    assert args.classic is True
    assert args.tui_dev is True
    assert args.yolo is True


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


def test_tui_interactive_prefers_ink_terminal(monkeypatch):
    from aegis.cli import tui
    from aegis.config import Config

    calls = []
    monkeypatch.setattr(tui, "_open_ink_terminal_agent", lambda config, **kw: calls.append(("ink", config, kw)) or 0)
    monkeypatch.setattr(tui, "_open_classic_terminal_agent", lambda config, **kw: calls.append(("classic", config, kw)) or 0)
    monkeypatch.setattr(tui.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(tui.sys, "stdout", SimpleNamespace(isatty=lambda: True))

    cfg = Config.load()
    args = Namespace(once=False, watch=False, interval=5.0, model="m", provider="p", yolo=True, classic=False, tui_dev=True)
    assert tui.cmd_tui(args, cfg) == 0
    assert len(calls) == 1
    name, seen_cfg, kwargs = calls[0]
    assert (name, seen_cfg) == ("ink", cfg)
    assert kwargs["model"] == "m"
    assert kwargs["provider_name"] == "p"
    assert kwargs["auto"] is True
    assert kwargs["dev"] is True
    assert kwargs["session"] is not None
    assert kwargs["store"] is not None


def test_tui_interactive_passes_resume_session(monkeypatch):
    from aegis.cli import tui
    from aegis.config import Config

    session = object()

    class Store:
        def load(self, value):
            assert value == "sess_123"
            return session

        def latest(self):
            raise AssertionError("resume should not fall back to latest")

    calls = []
    monkeypatch.setattr("aegis.session.SessionStore", Store)
    monkeypatch.setattr(tui, "_open_terminal_agent", lambda config, **kw: calls.append((config, kw)) or 0)
    monkeypatch.setattr(tui.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(tui.sys, "stdout", SimpleNamespace(isatty=lambda: True))

    cfg = Config.load()
    args = Namespace(
        once=False,
        watch=False,
        interval=5.0,
        model=None,
        provider=None,
        yolo=False,
        classic=False,
        tui_dev=False,
        resume="sess_123",
    )
    assert tui.cmd_tui(args, cfg) == 0
    assert calls == [(
        cfg,
        {
            "model": None,
            "provider_name": None,
            "auto": False,
            "classic": False,
            "dev": False,
            "session": session,
            "store": calls[0][1]["store"],
        },
    )]


def test_tui_interactive_falls_back_to_classic_when_ink_unavailable(monkeypatch):
    from aegis.cli import repl, tui
    from aegis.config import Config

    calls = []

    def fail_ink(config, **kw):
        calls.append(("ink", kw))
        raise repl._FullscreenUnavailable("missing bundle")

    monkeypatch.setattr(tui, "_open_ink_terminal_agent", fail_ink)
    monkeypatch.setattr(tui, "_open_classic_terminal_agent", lambda config, **kw: calls.append(("classic", kw)) or 0)
    monkeypatch.setattr(tui.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(tui.sys, "stdout", SimpleNamespace(isatty=lambda: True))

    cfg = Config.load()
    args = Namespace(once=False, watch=False, interval=5.0, model=None, provider=None, yolo=False, classic=False, tui_dev=False)
    assert tui.cmd_tui(args, cfg) == 0
    assert [item[0] for item in calls] == ["ink", "classic"]
    assert calls[0][1]["model"] is None
    assert calls[0][1]["provider_name"] is None
    assert calls[0][1]["auto"] is False
    assert calls[0][1]["dev"] is False
    assert calls[0][1]["session"] is calls[1][1]["session"]
    assert calls[0][1]["store"] is calls[1][1]["store"]
    assert calls[1][1]["model"] is None
    assert calls[1][1]["provider_name"] is None
    assert calls[1][1]["auto"] is False


def test_classic_terminal_agent_sets_fullscreen_opt_out(monkeypatch):
    from aegis.cli import repl, tui
    from aegis.config import Config

    seen = []
    monkeypatch.delenv("AEGIS_CLASSIC_TUI", raising=False)

    class Store:
        def latest(self):
            return None

    class Session:
        @staticmethod
        def create():
            return object()

    monkeypatch.setattr("aegis.session.SessionStore", Store)
    monkeypatch.setattr("aegis.session.Session", Session)
    monkeypatch.setattr(repl, "interactive", lambda *a, **kw: seen.append(os.environ.get("AEGIS_CLASSIC_TUI")))

    assert tui._open_classic_terminal_agent(Config.load()) == 0
    assert seen == ["1"]
    assert "AEGIS_CLASSIC_TUI" not in os.environ
