"""First-class /ultracode: force-loads the ultracode skill and runs the task through the
autonomous plan→test→implement→verify loop."""

from __future__ import annotations

from types import SimpleNamespace

from aegis.cli.repl import handle_ultracode_command
from aegis.config import Config
from aegis.skills import SkillsLoader


def test_ultracode_injects_skill_body_and_task():
    agent = SimpleNamespace(skills=SkillsLoader(Config.load()))
    p = handle_ultracode_command("/ultracode add pagination to the list endpoint", agent)
    assert p and "add pagination to the list endpoint" in p
    assert "ULTRACODE SKILL" in p                 # the skill body was loaded into the turn
    assert "todo_write" in p and "FAILING test" in p   # the enforcement directive
    assert "proven by tool output" in p


def test_ultracode_requires_a_task():
    assert handle_ultracode_command("/ultracode", SimpleNamespace(skills=None)) is None


def test_ultracode_degrades_without_skill():
    # no skills loader -> still emits the loop directive, just without the bundled body
    p = handle_ultracode_command("/ultracode do the thing", SimpleNamespace(skills=None))
    assert p and "do the thing" in p
    assert "ULTRACODE SKILL" not in p
    assert "ultracode loop above" in p
