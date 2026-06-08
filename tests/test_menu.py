"""Inline arrow-key onboarding menus: safe fallback + navigation logic."""

from __future__ import annotations

import contextlib
import io


def test_menu_falls_back_when_not_a_terminal(monkeypatch):
    from aegis.cli import menu
    monkeypatch.setattr(menu, "interactive", lambda: False)
    assert menu.select_one("p", [("a", "A"), ("b", "B")]) is None
    assert menu.select_many("p", [("a", "A")]) is None


def _drive(monkeypatch, keys):
    from aegis.cli import menu
    monkeypatch.setattr(menu, "interactive", lambda: True)
    it = iter(keys)

    def fake_run(draw, handle):
        for k in it:
            if handle(k) == "done":
                return True
        return True
    monkeypatch.setattr(menu, "_run", fake_run)
    return menu


def test_select_one_arrow_navigation(monkeypatch):
    menu = _drive(monkeypatch, ["down", "down", "enter"])
    with contextlib.redirect_stdout(io.StringIO()):
        v = menu.select_one("pick", [("a", "A"), ("b", "B"), ("c", "C")], default=0)
    assert v == "c"


def test_select_one_digit_jump(monkeypatch):
    menu = _drive(monkeypatch, ["2", "enter"])
    with contextlib.redirect_stdout(io.StringIO()):
        v = menu.select_one("pick", [("a", "A"), ("b", "B"), ("c", "C")])
    assert v == "b"


def test_select_many_space_toggle(monkeypatch):
    menu = _drive(monkeypatch, ["down", "space", "down", "space", "enter"])
    with contextlib.redirect_stdout(io.StringIO()):
        vs = menu.select_many("pick", [("a", "A"), ("b", "B"), ("c", "C")])
    assert vs == ["b", "c"]


def test_select_many_preselected_then_toggle(monkeypatch):
    menu = _drive(monkeypatch, ["space", "enter"])      # toggle idx0 on top of preselect
    with contextlib.redirect_stdout(io.StringIO()):
        vs = menu.select_many("pick", [("a", "A"), ("b", "B")], preselected=["b"])
    assert vs == ["a", "b"]
