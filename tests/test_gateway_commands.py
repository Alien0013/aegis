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
    from aegis.runs import RunStore
    key = r._key(_ev("x"))
    runs = [row for row in RunStore().list(session_id=key, limit=10)
            if row["surface"] == "gateway" and row["kind"] == "control"]
    assert {row["data"]["command"] for row in runs} >= {"/whoami", "/help"}
    assert all(row["data"]["provider"] == r.config.get("model.provider") for row in runs)
    assert all(row["data"]["model"] == r.config.get("model.default") for row in runs)
    assert all(row["trace_id"].startswith("trace_") for row in runs)


def test_new_closes_cached_agent_before_reset(tmp_path, monkeypatch):
    from aegis.types import Message

    r = _runner(tmp_path, monkeypatch)
    ev = _ev("x")
    key = r._key(ev)
    old = r._session(key)
    old.messages.append(Message.user("old gateway prompt"))
    closed = []

    class CachedAgent:
        session = old

        def end_session(self):
            closed.append([m.content for m in self.session.messages])

    r._agents[key] = CachedAgent()

    out = r.dispatch(_ev("/new"))

    assert "Started a fresh session" in out
    assert closed == [["old gateway prompt"]]
    assert key not in r._agents
    assert r._session(key).messages == []


def test_agent_cache_eviction_closes_cached_agent(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from aegis.gateway.base import MessageEvent
    from aegis.types import Message

    import aegis.gateway.runner as rmod

    r = _runner(tmp_path, monkeypatch)
    r._agent_cap = 1
    closed = []

    class FakeMemory:
        def __init__(self, sid):
            self.sid = sid

        def shutdown(self):
            closed.append((self.sid, "memory"))

    class FakeTransport:
        def __init__(self, sid):
            self.sid = sid

        def close(self):
            closed.append((self.sid, "transport"))

    class FakeAgent:
        def __init__(self, session):
            self.session = session
            self.memory = FakeMemory(session.id)
            self.provider = SimpleNamespace(transport=FakeTransport(session.id))
            self.budget = SimpleNamespace(api_call_count=0)

        def end_session(self):
            closed.append((self.session.id, "end"))

    monkeypatch.setattr(rmod.Agent, "create",
                        staticmethod(lambda *args, **kwargs: FakeAgent(kwargs["session"])))
    monkeypatch.setattr(r._surface_runner, "run_prompt",
                        lambda *args, **kwargs: SimpleNamespace(message=Message.assistant("ok")))

    ev1 = _ev("first")
    ev2 = MessageEvent(platform="telegram", chat_id="c2", text="second",
                       user_id="u1", user_name="alien")
    key1 = r._key(ev1)
    key2 = r._key(ev2)

    assert r.dispatch(ev1) == "ok"
    assert r.dispatch(ev2) == "ok"

    assert key1 not in r._agents
    assert key2 in r._agents
    assert closed == [(key1, "end"), (key1, "memory"), (key1, "transport")]


def test_model_session_override(tmp_path, monkeypatch):
    r = _runner(tmp_path, monkeypatch)
    key = r._key(_ev("x"))
    assert "Switch for this session" in r.dispatch(_ev("/model"))
    closed = []

    class CachedAgent:
        def end_session(self):
            closed.append("closed")

    r._agents[key] = CachedAgent()                  # a cached agent to invalidate
    out = r.dispatch(_ev("/model gpt-5.5-pro"))
    assert "→ gpt-5.5-pro" in out
    assert r._session(key).meta["model"] == "gpt-5.5-pro"
    assert r._session(key).meta["runtime_controls"]["model"] == "gpt-5.5-pro"
    assert key not in r._agents                     # cache dropped -> rebuilt next turn
    assert closed == ["closed"]
    assert "gpt-5.5-pro" in r.dispatch(_ev("/model"))
    from aegis.runs import RunStore
    run = next(row for row in RunStore().list(session_id=key, limit=10)
               if row["kind"] == "control" and row["data"].get("model") == "gpt-5.5-pro")
    assert run["surface"] == "gateway"
    assert run["data"]["provider"] == r.config.get("model.provider")
    assert run["data"]["model"] == "gpt-5.5-pro"
    assert r._session(key).meta["last_run_id"] == RunStore().list(session_id=key, limit=1)[0]["id"]


def test_model_provider_session_override(tmp_path, monkeypatch):
    r = _runner(tmp_path, monkeypatch)
    key = r._key(_ev("x"))
    r._agents[key] = object()

    out = r.dispatch(_ev("/model anthropic/claude-sonnet-4-6"))

    assert "→ anthropic/claude-sonnet-4-6" in out
    assert r._session(key).meta["provider"] == "anthropic"
    assert r._session(key).meta["model"] == "claude-sonnet-4-6"
    assert r._session(key).meta["runtime_controls"]["provider"] == "anthropic"
    assert key not in r._agents
    assert "anthropic/claude-sonnet-4-6" in r.dispatch(_ev("/model"))
    from aegis.runs import RunStore
    run = next(row for row in RunStore().list(session_id=key, limit=10)
               if row["kind"] == "control" and row["data"].get("model") == "claude-sonnet-4-6")
    assert run["data"]["provider"] == "anthropic"
    assert run["data"]["model"] == "claude-sonnet-4-6"


def test_model_only_override_preserves_gateway_session_provider(tmp_path, monkeypatch):
    from aegis.providers import registry

    r = _runner(tmp_path, monkeypatch)
    key = r._key(_ev("x"))
    r._session(key).meta["runtime_controls"] = {
        "provider": "openrouter",
        "model": "old-model",
    }
    captured = {}

    def fake_validate(provider, model, config):
        captured.update({"provider": provider, "model": model, "config": config})
        return {"ok": True, "provider": provider, "model": model}

    monkeypatch.setattr(registry, "validate_model_choice", fake_validate)
    monkeypatch.setattr(registry, "model_validation_message", lambda _validation: "")

    out = r.dispatch(_ev("/model newer-model"))

    assert "→ newer-model" in out
    assert captured == {"provider": "openrouter", "model": "newer-model", "config": r.config}
    controls = r._session(key).meta["runtime_controls"]
    assert controls["provider"] == "openrouter"
    assert controls["model"] == "newer-model"
    assert "openrouter/newer-model" in r.dispatch(_ev("/model"))


def test_model_provider_override_rejects_unknown_provider(tmp_path, monkeypatch):
    r = _runner(tmp_path, monkeypatch)
    key = r._key(_ev("x"))
    cached = object()
    r._agents[key] = cached

    out = r.dispatch(_ev("/model anthropc/claude-sonnet-4-6"))

    assert "Unknown provider 'anthropc'" in out
    assert "anthropic" in out
    assert "runtime_controls" not in r._session(key).meta
    assert r._agents[key] is cached


def test_provider_and_reasoning_runtime_controls_are_session_scoped(tmp_path, monkeypatch):
    r = _runner(tmp_path, monkeypatch)
    key = r._key(_ev("x"))

    assert "provider:" in r.dispatch(_ev("/provider"))
    assert "→ openrouter" in r.dispatch(_ev("/provider openrouter"))
    assert r._session(key).meta["runtime_controls"]["provider"] == "openrouter"
    assert "display=summary" in r.dispatch(_ev("/reasoning"))
    assert "display → live" in r.dispatch(_ev("/reasoning live"))
    assert "effort → high" in r.dispatch(_ev("/reasoning high"))
    assert "usage" in r.dispatch(_ev("/reasoning chaos"))

    controls = r._session(key).meta["runtime_controls"]
    assert controls["provider"] == "openrouter"
    assert controls["reasoning_display"] == "live"
    assert controls["reasoning_effort"] == "high"
    who = r.dispatch(_ev("/whoami"))
    assert "provider: openrouter" in who
    assert "reasoning: display=live · effort=high" in who
    from aegis.runs import RunStore
    run = next(row for row in RunStore().list(session_id=key, limit=10)
               if row["kind"] == "control" and row["data"].get("command") == "/provider")
    assert run["data"]["provider"] == "openrouter"
    assert run["data"]["model"] == r.config.get("model.default")


def test_provider_override_rejects_unknown_provider(tmp_path, monkeypatch):
    r = _runner(tmp_path, monkeypatch)
    key = r._key(_ev("x"))
    cached = object()
    r._agents[key] = cached

    out = r.dispatch(_ev("/provider anthropc"))

    assert "Unknown provider 'anthropc'" in out
    assert "anthropic" in out
    assert "runtime_controls" not in r._session(key).meta
    assert r._agents[key] is cached


def test_busy_mode_set_and_validate(tmp_path, monkeypatch):
    r = _runner(tmp_path, monkeypatch)
    assert "queue" in r.dispatch(_ev("/busy"))
    assert "usage" in r.dispatch(_ev("/busy chaos"))
    assert "→ steer" in r.dispatch(_ev("/busy steer"))
    from aegis.config import Config
    assert Config.load().get("gateway.busy_mode") == "steer"   # persisted
    key = r._key(_ev("x"))
    assert r._session(key).meta["runtime_controls"]["busy_mode"] == "steer"
    from aegis.runs import RunStore
    modes = [row["data"].get("mode") for row in RunStore().list(session_id=key, limit=10)
             if row["kind"] == "control" and row["data"].get("command") == "/busy"]
    assert {"", "chaos", "steer"} <= set(modes)


def test_compress_command(tmp_path, monkeypatch):
    r = _runner(tmp_path, monkeypatch)
    key = r._key(_ev("x"))
    from aegis.types import Message
    s = r._session(key)
    s.messages = [Message.system("sys")] + [Message.user(f"m{i}") for i in range(30)]
    s.meta["runtime_controls"] = {"provider": "p-compress", "model": "m-compress"}

    class FakeAgent:
        session = s
    seen = {}
    import aegis.gateway.runner as rmod

    def fake_create(*args, **kwargs):
        seen["kwargs"] = kwargs
        return FakeAgent()

    monkeypatch.setattr(rmod.Agent, "create", staticmethod(fake_create))

    def fake_compact(agent, session, emit=None, **_kwargs):
        if emit:
            emit({"type": "compacting", "reason": "manual_context_compression"})
        session.messages = session.messages[:3]
        if emit:
            emit({"type": "compacted", "messages_before": 31, "messages_after": 3})
        return session
    import aegis.agent.loop as loop
    monkeypatch.setattr(loop, "compact_now", fake_compact)
    out = r.dispatch(_ev("/compress"))
    assert "31 → 3" in out
    assert seen["kwargs"]["provider_name"] == "p-compress"
    assert seen["kwargs"]["model"] == "m-compress"
    from aegis.runs import RunStore
    from aegis.tracing import TraceStore
    run = next(r for r in RunStore().list(session_id=key, limit=10)
               if r["surface"] == "gateway" and r["kind"] == "compaction")
    assert run["data"]["platform"] == "telegram"
    trace = TraceStore.from_config(r.config).get_trace(run["trace_id"])
    assert trace["session_id"] == key
    assert trace["kind_counts"]["turn"] == 1


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
    from aegis.runs import RunStore
    runs = [row for row in RunStore().list(session_id=key, limit=10)
            if row["kind"] == "control" and row["data"].get("command") == "/goal"]
    assert any(row["data"].get("rejected") is True for row in runs)
    assert any(row["data"].get("start_turn") is False for row in runs)
    # not running -> setting a goal works (and returns nothing yet — it falls through
    # to run the turn, which needs a provider; just check state here)
    from aegis import goals
    reply, start = goals.handle_command(r._session(key), "/goal ship it")
    assert start and goals.get(r._session(key))["text"] == "ship it"
