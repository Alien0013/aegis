"""Gateway slash commands: /whoami, /model, /busy, /compress, mid-run /goal guard."""

from __future__ import annotations


def _runner(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.gateway.pairing as pairing
    monkeypatch.setattr(pairing.PairingStore, "is_authorized", lambda *a, **k: True)
    from aegis.config import Config
    from aegis.gateway.runner import GatewayRunner
    return GatewayRunner(Config.load(), cwd=tmp_path)


def _ev(text):
    from aegis.gateway.base import MessageEvent
    return MessageEvent(platform="telegram", chat_id="c1", text=text,
                        user_id="u1", user_name="alien")


def test_whoami_and_help(tmp_path, monkeypatch):
    r = _runner(tmp_path, monkeypatch)
    out = r.dispatch(_ev("/whoami"))
    assert "telegram" in out and "@alien" in out and "busy_mode: queue" in out
    assert "/whoami" in r.dispatch(_ev("/help"))


def test_model_session_override(tmp_path, monkeypatch):
    r = _runner(tmp_path, monkeypatch)
    key = r._key(_ev("x"))
    assert "Switch for this session" in r.dispatch(_ev("/model"))
    r._agents[key] = object()                       # a cached agent to invalidate
    out = r.dispatch(_ev("/model gpt-5.5-pro"))
    assert "→ gpt-5.5-pro" in out
    assert r._session(key).meta["model"] == "gpt-5.5-pro"
    assert key not in r._agents                     # cache dropped -> rebuilt next turn
    assert "gpt-5.5-pro" in r.dispatch(_ev("/model"))


def test_busy_mode_set_and_validate(tmp_path, monkeypatch):
    r = _runner(tmp_path, monkeypatch)
    assert "queue" in r.dispatch(_ev("/busy"))
    assert "usage" in r.dispatch(_ev("/busy chaos"))
    assert "→ steer" in r.dispatch(_ev("/busy steer"))
    from aegis.config import Config
    assert Config.load().get("gateway.busy_mode") == "steer"   # persisted


def test_compress_command(tmp_path, monkeypatch):
    r = _runner(tmp_path, monkeypatch)
    key = r._key(_ev("x"))
    from aegis.types import Message
    s = r._session(key)
    s.messages = [Message.system("sys")] + [Message.user(f"m{i}") for i in range(30)]

    class FakeAgent:
        session = s
    import aegis.gateway.runner as rmod
    monkeypatch.setattr(rmod.Agent, "create", staticmethod(lambda *a, **k: FakeAgent()))

    def fake_compact(agent, session):
        session.messages = session.messages[:3]
        return session
    import aegis.agent.loop as loop
    monkeypatch.setattr(loop, "_force_compact", fake_compact)
    out = r.dispatch(_ev("/compress"))
    assert "31 → 3" in out


def test_goal_rejected_mid_run_but_control_allowed(tmp_path, monkeypatch):
    import threading
    r = _runner(tmp_path, monkeypatch)
    key = r._key(_ev("x"))
    lock = r._key_locks.setdefault(key, threading.Lock())
    lock.acquire()                                   # simulate a turn in progress
    try:
        assert "send 'stop' first" in r.dispatch(_ev("/goal do something new"))
        assert "No active goal" in r.dispatch(_ev("/goal status"))   # control-plane is fine
    finally:
        lock.release()
    # not running -> setting a goal works (and returns nothing yet — it falls through
    # to run the turn, which needs a provider; just check state here)
    from aegis import goals
    reply, start = goals.handle_command(r._session(key), "/goal ship it")
    assert start and goals.get(r._session(key))["text"] == "ship it"
