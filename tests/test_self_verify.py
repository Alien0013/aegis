"""Pre-final self-verify gate: opt-in, the loop re-checks its own answer once before
finalizing (bounded so it can never loop), and stays off by default."""

from conftest import FakeProvider


def _agent(tmp_path, monkeypatch, *, self_verify, min_tools=0):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    from aegis.agent.agent import Agent
    from aegis.config import Config
    from aegis.session import Session
    cfg = Config.load()
    cfg.data["learn"]["background"] = False        # don't fork a review during the test
    cfg.data["agent"]["self_verify"] = self_verify
    cfg.data["agent"]["self_verify_min_tools"] = min_tools
    return Agent(config=cfg, provider=FakeProvider(), session=Session.create())


def test_self_verify_off_by_default(tmp_path, monkeypatch):
    agent = _agent(tmp_path, monkeypatch, self_verify=False)
    agent.run("go")
    assert agent.provider.calls == 1               # finalize immediately, no extra call


def test_self_verify_adds_one_recheck_then_finalizes(tmp_path, monkeypatch):
    agent = _agent(tmp_path, monkeypatch, self_verify=True)
    agent.run("go")
    assert agent.provider.calls == 2               # one verify pass, then finalize (bounded to once)


def test_self_verify_skips_when_under_min_tools(tmp_path, monkeypatch):
    # A no-tool turn with a min-tools threshold of 1 must not trigger the gate.
    agent = _agent(tmp_path, monkeypatch, self_verify=True, min_tools=1)
    agent.run("go")
    assert agent.provider.calls == 1
