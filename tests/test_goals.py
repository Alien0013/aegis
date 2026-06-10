"""Persistent goals: command handling, judge fail-open, continuation loop."""

from __future__ import annotations


def _session():
    from aegis.session import Session
    return Session.create()


def test_goal_commands_lifecycle():
    from aegis import goals
    s = _session()
    reply, start = goals.handle_command(s, "/goal status")
    assert "No active goal" in reply and not start

    reply, start = goals.handle_command(s, "/goal fix all the tests")
    assert start and "20-turn budget" in reply
    assert goals.get(s)["text"] == "fix all the tests"

    reply, _ = goals.handle_command(s, "/subgoal add a regression test")
    assert "Added subgoal 1" in reply
    reply, _ = goals.handle_command(s, "/subgoal")
    assert "regression test" in reply
    reply, _ = goals.handle_command(s, "/subgoal remove 1")
    assert "Removed" in reply and goals.get(s)["subgoals"] == []

    goals.handle_command(s, "/goal pause")
    assert goals.get(s)["status"] == "paused"
    goals.handle_command(s, "/goal resume")
    assert goals.get(s)["status"] == "active" and goals.get(s)["turns_used"] == 0
    goals.handle_command(s, "/goal clear")
    assert goals.get(s) is None


def test_judge_fail_open(monkeypatch):
    from aegis import goals
    import aegis.providers as providers

    def boom(cfg):
        raise RuntimeError("no provider")
    monkeypatch.setattr(providers, "build_provider", boom)
    g = {"text": "x", "subgoals": []}
    done, reason = goals.judge(None, g, "whatever")
    assert done is False and "continuing" in reason.lower()


def test_run_loop_continues_then_stops(monkeypatch):
    from aegis import goals

    s = _session()
    goals.set_goal(s, "make four files", max_turns=20)
    verdicts = iter([(False, "1 of 4 done"), (False, "2 of 4 done"), (True, "all done")])
    monkeypatch.setattr(goals, "judge", lambda cfg, g, last: next(verdicts))

    class FakeAgent:
        config = None
        session = s

        def __init__(self):
            import threading
            self.cancel_event = threading.Event()
            self.prompts = []

        def run(self, prompt, on_event=None):
            self.prompts.append(prompt)
            return type("R", (), {"content": "progress"})()

    agent = FakeAgent()
    notes = []
    final = goals.run_loop(agent, "started", notes.append)
    assert final == "progress"
    assert len(agent.prompts) == 2                         # two continuations, then done
    assert all("standing goal" in p for p in agent.prompts)
    assert any("✓ Goal achieved" in n for n in notes)
    assert goals.get(s) is None                            # cleared on completion


def test_run_loop_pauses_at_budget(monkeypatch):
    from aegis import goals

    s = _session()
    goals.set_goal(s, "endless task", max_turns=2)
    monkeypatch.setattr(goals, "judge", lambda cfg, g, last: (False, "keep going"))

    class FakeAgent:
        config = None
        session = s

        def __init__(self):
            import threading
            self.cancel_event = threading.Event()
            self.calls = 0

        def run(self, prompt, on_event=None):
            self.calls += 1
            return type("R", (), {"content": "more"})()

    agent = FakeAgent()
    notes = []
    goals.run_loop(agent, "t", notes.append)
    assert agent.calls == 2
    assert goals.get(s)["status"] == "paused"
    assert any("⏸ Goal paused" in n for n in notes)


def test_session_recap_summarizes_locally():
    from aegis.cli.repl import session_recap
    from aegis.session import Session
    from aegis.types import Message, ToolCall

    s = Session.create()
    assert session_recap(s) == []                       # empty session -> no recap
    s.messages = [
        Message.user("fix the bug in parser.py"),
        Message(role="assistant", content="",
                tool_calls=[ToolCall(id="1", name="edit_file",
                                     arguments={"path": "parser.py"})]),
        Message.tool("1", "edit_file", "Edited parser.py"),
        Message.assistant("Fixed the off-by-one."),
    ]
    lines = "\n".join(session_recap(s))
    assert "1 user / 1 assistant" in lines
    assert "edit_file×1" in lines
    assert "parser.py" in lines and "off-by-one" in lines
