"""ultracode autonomous loop: a higher step budget, and the loop refusing to stop
while the plan still has open todo items (bounded so it can't loop forever)."""

from conftest import FakeProvider


def _agent(tmp_path, monkeypatch, todos, ultracode):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    from aegis.agent.agent import Agent
    from aegis.config import Config
    from aegis.session import Session
    cfg = Config.load()
    cfg.data["tools"]["exec_mode"] = "full"
    cfg.data["learn"]["background"] = False        # don't fork a review during the test
    sess = Session.create()
    sess.todos = todos
    agent = Agent(config=cfg, provider=FakeProvider(), session=sess)
    agent._ultracode_active = ultracode
    return agent


def test_ultracode_continues_while_todos_open(tmp_path, monkeypatch):
    # The model keeps returning "done." with no tool calls; with ultracode active and
    # an open todo, the loop must push it to continue rather than stopping at once.
    agent = _agent(tmp_path, monkeypatch, [{"content": "do the thing", "status": "pending"}], True)
    agent.run("go")
    assert agent.provider.calls >= 3              # pushed several continuations
    assert agent._ultracode_active is False       # flag is scoped to the one turn


def test_ultracode_stops_when_todos_complete(tmp_path, monkeypatch):
    agent = _agent(tmp_path, monkeypatch, [{"content": "do the thing", "status": "completed"}], True)
    agent.run("go")
    assert agent.provider.calls == 1              # nothing open -> finalize immediately


def test_normal_turn_is_not_pushed(tmp_path, monkeypatch):
    # Without ultracode mode, an open todo does NOT force continuation.
    agent = _agent(tmp_path, monkeypatch, [{"content": "do the thing", "status": "pending"}], False)
    agent.run("go")
    assert agent.provider.calls == 1


def test_ultracode_continuation_is_bounded(tmp_path, monkeypatch):
    # Even with a permanently-open todo, the loop stops after the cap (no infinite loop).
    agent = _agent(tmp_path, monkeypatch, [{"content": "never done", "status": "pending"}], True)
    agent.run("go")
    from aegis.agent.loop import _ULTRACODE_MAX_CONTINUES
    assert agent.provider.calls <= _ULTRACODE_MAX_CONTINUES + 2


def test_ultracode_command_raises_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    from types import SimpleNamespace
    from aegis.config import Config
    from aegis.cli.repl import handle_ultracode_command
    agent = SimpleNamespace(config=Config.load(),
                            budget=SimpleNamespace(max_iterations=50), skills=None)
    prompt = handle_ultracode_command("/ultracode build it", agent)
    assert agent.budget.max_iterations == 250 and agent._ultracode_active is True
    assert "EXECUTION" in prompt
