"""Plan mode: /plan drafts (no changes) and stashes the task; /proceed runs it for real."""

from __future__ import annotations

from types import SimpleNamespace

from aegis.cli.repl import handle_plan_command


def _agent():
    return SimpleNamespace(session=SimpleNamespace(meta={}))


def test_plan_stashes_then_proceed_executes():
    agent = _agent()
    p = handle_plan_command("/plan add a retry to fetch()", agent)
    assert p and "PLAN MODE" in p and "retry to fetch()" in p
    assert agent.session.meta["pending_plan"] == "add a retry to fetch()"

    e = handle_plan_command("/proceed", agent)
    assert e and "approved" in e.lower() and "retry to fetch()" in e
    assert "pending_plan" not in agent.session.meta      # cleared after proceed


def test_plan_requires_a_task():
    assert handle_plan_command("/plan", _agent()) is None


def test_proceed_without_a_plan():
    assert handle_plan_command("/proceed", _agent()) is None
