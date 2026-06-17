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


def test_gateway_session_keys_are_thread_aware(tmp_path, monkeypatch):
    r = _runner(tmp_path, monkeypatch)
    first = _ev("topic one")
    second = _ev("topic two")
    first.thread_id = "topic-a"
    second.thread_id = "topic-b"

    assert r._key(first) == "telegram:c1:thread:topic-a:u1"
    assert r._key(second) == "telegram:c1:thread:topic-b:u1"
    assert r._key(first) != r._key(second)
    assert r._key(_ev("plain")) == "telegram:c1:u1"


def test_gateway_send_via_adapter_passes_thread_metadata_with_fallback(tmp_path, monkeypatch):
    from aegis.gateway.base import BasePlatformAdapter

    r = _runner(tmp_path, monkeypatch)

    class MetadataAdapter(BasePlatformAdapter):
        name = "slack"

        def __init__(self):
            self.sent = []

        def send(self, chat_id: str, text: str, *, metadata=None):  # noqa: ANN001
            self.sent.append((chat_id, text, metadata))

    class LegacyAdapter(BasePlatformAdapter):
        name = "legacy"

        def __init__(self):
            self.sent = []

        def send(self, chat_id: str, text: str):
            self.sent.append((chat_id, text))

    metadata_adapter = MetadataAdapter()
    legacy_adapter = LegacyAdapter()
    r.add(metadata_adapter)
    r.add(legacy_adapter)

    assert r._send_via_adapter("slack", "C1", "hello", metadata={"thread_id": "167.1"}) is True
    assert r._send_via_adapter("legacy", "C2", "fallback", metadata={"thread_id": "ignored"}) is True
    assert metadata_adapter.sent == [("C1", "hello", {"thread_id": "167.1"})]
    assert legacy_adapter.sent == [("C2", "fallback")]


def test_handoff_is_adopted_before_control_commands(tmp_path, monkeypatch):
    from aegis import handoff
    from aegis.session import Session

    r = _runner(tmp_path, monkeypatch)
    handed = Session.create("terminal session")
    handed.meta["runtime_controls"] = {
        "provider": "openrouter",
        "model": "handoff-model",
    }
    r.store.save(handed)
    handoff.set_handoff("telegram", "c1", handed.id)

    out = r.dispatch(_ev("/status"))
    key = r._key(_ev("x"))

    assert "provider=openrouter" in out
    assert "model=handoff-model" in out
    assert r._session(key).id == handed.id
    assert handoff.pop_handoff("telegram", "c1") is None
    from aegis.runs import RunStore
    run = next(row for row in RunStore().list(session_id=handed.id, limit=5)
               if row["kind"] == "control" and row["data"].get("command") == "/status")
    assert run["data"]["provider"] == "openrouter"
    assert run["data"]["model"] == "handoff-model"


def test_status_reports_cached_agent_model_and_context(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from aegis.types import Message

    r = _runner(tmp_path, monkeypatch)
    ev = _ev("/status")
    key = r._key(ev)
    session = r._session(key)
    session.messages.append(Message.system("system"))
    session.messages.append(Message.user("hello from the chat"))
    r._agents[key] = SimpleNamespace(
        provider=SimpleNamespace(name="cached-provider", model="cached-model", context_length=1000)
    )

    out = r.dispatch(ev)

    assert "provider=cached-provider" in out
    assert "model=cached-model" in out
    assert "context≈" in out
    assert "/1,000 tokens" in out
    assert "messages=2" in out


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


def test_gateway_generation_guard_blocks_late_save_after_reset(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import aegis.gateway.runner as rmod
    from aegis.session import Session
    from aegis.types import Message

    r = _runner(tmp_path, monkeypatch)
    ev = _ev("work on this")
    key = r._key(ev)

    class FakeAgent:
        def __init__(self, session):
            self.session = session
            self.budget = SimpleNamespace(api_call_count=0)

    monkeypatch.setattr(
        rmod.Agent,
        "create",
        staticmethod(lambda _config, **kwargs: FakeAgent(kwargs["session"])),
    )

    def fake_run_prompt(_prompt, **kwargs):
        stale = kwargs["session"]
        new_generation = r._bump_generation(key)
        fresh = r._stamp_generation(key, Session(id=key, title=key), new_generation)
        r._sessions[key] = fresh
        r.store.save(fresh)

        stale.messages.append(Message.user("late user"))
        stale.messages.append(Message.assistant("late reply"))
        r.store.save(stale)
        return SimpleNamespace(message=Message.assistant("late reply"), session=stale)

    monkeypatch.setattr(r._surface_runner, "run_prompt", fake_run_prompt)

    assert r.dispatch(ev) == ""
    assert r._session(key).messages == []
    assert r.store.load(key).messages == []


def test_gateway_refreshes_session_on_message_count_drift_without_newer_timestamp(tmp_path, monkeypatch):
    from aegis.types import Message

    r = _runner(tmp_path, monkeypatch)
    ev = _ev("work on this")
    key = r._key(ev)
    stale = r._session(key)
    stale.messages.append(Message.user("old in-memory prompt"))
    r.store.save(stale)

    latest = r.store.load(key)
    latest.messages.append(Message.assistant("external persisted reply"))
    r.store.save(latest)
    stale.updated_at = latest.updated_at
    r._sessions[key] = stale
    closed = []

    class CachedAgent:
        session = stale

        def end_session(self):
            closed.append("closed")

    r._agents[key] = CachedAgent()

    fresh = r._fresh_session_if_drifted(key, stale)

    assert [m.content for m in fresh.messages] == [
        "old in-memory prompt",
        "external persisted reply",
    ]
    assert closed == ["closed"]
    assert key not in r._agents
    assert r._generation(key) == 1
    assert fresh.meta["_gateway_generation"] == 1


def test_session_store_rejects_stale_gateway_generation(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))

    from aegis.session import Session, SessionStore
    from aegis.types import Message

    store = SessionStore()
    old = Session(id="telegram:c1:u1", title="telegram:c1:u1")
    old.meta["_gateway_generation"] = 1
    old.messages.append(Message.user("old"))
    store.save(old)

    fresh = Session(id=old.id, title=old.id)
    fresh.meta["_gateway_generation"] = 2
    store.save(fresh)

    old.messages.append(Message.assistant("late reply"))
    store.save(old)

    assert store.load(old.id).messages == []


def test_stop_ignores_idle_cached_agent(tmp_path, monkeypatch):
    import threading

    r = _runner(tmp_path, monkeypatch)
    ev = _ev("/stop")
    key = r._key(ev)
    r._session(key)
    cancelled = []

    class CachedAgent:
        cancel_event = threading.Event()

        def cancel(self):
            cancelled.append("cancel")

    r._agents[key] = CachedAgent()

    out = r.dispatch(ev)

    assert "No active turn is running" in out
    assert cancelled == []
    assert r._generation(key) == 0


def test_stop_cancels_active_locked_agent(tmp_path, monkeypatch):
    import threading

    r = _runner(tmp_path, monkeypatch)
    ev = _ev("/stop")
    key = r._key(ev)
    r._session(key)
    lock = threading.Lock()
    r._key_locks[key] = lock
    cancelled = []

    class RunningAgent:
        cancel_event = threading.Event()

        def cancel(self):
            cancelled.append("cancel")

    r._agents[key] = RunningAgent()
    lock.acquire()
    try:
        out = r.dispatch(ev)
    finally:
        lock.release()

    assert "stop requested" in out
    assert cancelled == ["cancel"]
    assert r._generation(key) == 1


def test_gateway_wires_exec_approval_to_platform_adapter(tmp_path, monkeypatch):
    import threading
    from types import SimpleNamespace

    import aegis.gateway.runner as rmod
    from aegis.gateway.base import BasePlatformAdapter
    from aegis.types import Message

    r = _runner(tmp_path, monkeypatch)
    approvals = []
    seen = {}

    class ApprovalAdapter(BasePlatformAdapter):
        name = "telegram"

        def send(self, chat_id: str, text: str) -> None:
            return None

        def ask_exec_approval(self, ev, prompt: str, *, timeout: float = 3600) -> str:
            approvals.append((ev.chat_id, prompt, timeout))
            return "approve"

    class FakeAgent:
        def __init__(self, session):
            self.config = r.config
            self.session = session
            self.cwd = tmp_path
            self.tool_context = SimpleNamespace(session=session)
            self.provider = SimpleNamespace(name="fake", model="fake-model")
            self.budget = SimpleNamespace(api_call_count=0)
            self.cancel_event = threading.Event()
            self.tools_used = 0

    monkeypatch.setattr(
        rmod.Agent,
        "create",
        staticmethod(lambda _config, **kwargs: FakeAgent(kwargs["session"])),
    )

    def fake_run_prompt(_prompt, **kwargs):
        agent = kwargs["agent"]
        seen["approved"] = agent.tool_context.approver("Allow bash(ls)?")
        seen["approver_kwarg"] = kwargs.get("approver")
        return SimpleNamespace(message=Message.assistant("approved"), session=kwargs["session"])

    r.add(ApprovalAdapter())
    monkeypatch.setattr(r._surface_runner, "run_prompt", fake_run_prompt)

    assert r.dispatch(_ev("needs shell")) == "approved"
    assert seen["approved"] is True
    assert callable(seen["approver_kwarg"])
    assert approvals and approvals[0][0] == "c1"
    assert approvals[0][1] == "Allow bash(ls)?"


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


def test_gateway_reply_context_prefixes_prompt(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from aegis.gateway.base import MessageEvent
    from aegis.types import Message
    import aegis.gateway.runner as rmod

    r = _runner(tmp_path, monkeypatch)
    captured = {}

    class FakeAgent:
        def __init__(self, session):
            self.session = session
            self.budget = SimpleNamespace(api_call_count=0)

    monkeypatch.setattr(rmod.Agent, "create",
                        staticmethod(lambda *args, **kwargs: FakeAgent(kwargs["session"])))

    def fake_run_prompt(prompt, **kwargs):
        captured["prompt"] = prompt
        return SimpleNamespace(message=Message.assistant("ok"), session=kwargs["session"])

    monkeypatch.setattr(r._surface_runner, "run_prompt", fake_run_prompt)
    ev = MessageEvent(
        platform="telegram",
        chat_id="c1",
        text="What's the best time to go?",
        user_id="u1",
        user_name="alien",
        reply_to_message_id="42",
        reply_to_text="Japan is great for culture, food, and efficiency.",
    )

    assert r.dispatch(ev) == "ok"
    assert captured["prompt"].content.startswith(
        '[Replying to: "Japan is great for culture, food, and efficiency."]\n'
    )
    assert "What's the best time to go?" in captured["prompt"].content


def test_gateway_memory_notification_modes(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from aegis.types import Message
    import aegis.gateway.runner as rmod

    r = _runner(tmp_path, monkeypatch)

    class FakeAgent:
        def __init__(self, session):
            self.session = session
            self.budget = SimpleNamespace(api_call_count=0)

    monkeypatch.setattr(rmod.Agent, "create",
                        staticmethod(lambda *args, **kwargs: FakeAgent(kwargs["session"])))

    def fake_run_prompt(_prompt, **kwargs):
        kwargs["on_event"]({
            "type": "review_done",
            "kind": "memory",
            "actions": [
                "remembered in MEMORY.md (1/10)",
                "removed 1 entry from USER.md",
            ],
            "action_details": [
                {
                    "tool": "memory",
                    "action": "add",
                    "target": "memory",
                    "content": "Repo lives at /workspace/aegis.",
                    "summary": "remembered in MEMORY.md",
                },
                {
                    "tool": "memory",
                    "action": "remove",
                    "target": "user",
                    "old_text": "old preference",
                    "summary": "removed 1 entry from USER.md",
                },
            ],
        })
        return SimpleNamespace(message=Message.assistant("ok"), session=kwargs["session"])

    monkeypatch.setattr(r._surface_runner, "run_prompt", fake_run_prompt)

    assert r.dispatch(_ev("default")) == "ok\n\n— 💾 Memory updated · 💾 User profile updated"

    r.config.data.setdefault("display", {})["memory_notifications"] = "verbose"
    verbose = r.dispatch(_ev("verbose"))
    assert "💾 Memory ➕ Repo lives at /workspace/aegis." in verbose
    assert "💾 User profile ➖ old preference" in verbose

    r.config.data.setdefault("display", {})["memory_notifications"] = "off"
    assert r.dispatch(_ev("off")) == "ok"

    r.config.data.setdefault("display", {})["platforms"] = {
        "telegram": {"memory_notifications": "verbose"}
    }
    platform_override = r.dispatch(_ev("platform override"))
    assert "💾 Memory ➕ Repo lives at /workspace/aegis." in platform_override
    assert "💾 User profile ➖ old preference" in platform_override


def test_gateway_skill_notification_modes(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from aegis.types import Message
    import aegis.gateway.runner as rmod

    r = _runner(tmp_path, monkeypatch)

    class FakeAgent:
        def __init__(self, session):
            self.session = session
            self.budget = SimpleNamespace(api_call_count=0)

    monkeypatch.setattr(rmod.Agent, "create",
                        staticmethod(lambda *args, **kwargs: FakeAgent(kwargs["session"])))

    def fake_run_prompt(_prompt, **kwargs):
        kwargs["on_event"]({
            "type": "review_done",
            "kind": "skill",
            "actions": ["patched skill review-skill"],
            "action_details": [
                {
                    "tool": "skill_manage",
                    "action": "patch",
                    "name": "review-skill",
                    "old_string": "OLD_STEP",
                    "new_string": "NEW_STEP",
                    "summary": "patched skill review-skill",
                    "change": {
                        "action": "patch",
                        "name": "review-skill",
                        "old": "OLD_STEP",
                        "new": "NEW_STEP",
                    },
                },
            ],
        })
        return SimpleNamespace(message=Message.assistant("ok"), session=kwargs["session"])

    monkeypatch.setattr(r._surface_runner, "run_prompt", fake_run_prompt)

    assert r.dispatch(_ev("default")) == "ok\n\n— 📝 Skill 'review-skill' patched"

    r.config.data.setdefault("display", {})["memory_notifications"] = "verbose"
    verbose = r.dispatch(_ev("verbose"))
    assert "📝 Skill 'review-skill' patched: \"OLD_STEP\" → \"NEW_STEP\"" in verbose

    r.config.data.setdefault("display", {})["memory_notifications"] = "off"
    assert r.dispatch(_ev("off")) == "ok"

    r.config.data.setdefault("display", {})["platforms"] = {
        "telegram": {"memory_notifications": "verbose"}
    }
    platform_override = r.dispatch(_ev("platform override"))
    assert "📝 Skill 'review-skill' patched: \"OLD_STEP\" → \"NEW_STEP\"" in platform_override


def test_reply_pointer_truncates_and_escapes_quotes():
    from aegis.gateway.base import MessageEvent
    from aegis.gateway.runner import _with_reply_pointer

    ev = MessageEvent(
        platform="telegram",
        chat_id="c1",
        text="follow up",
        reply_to_text='"' + ("x" * 800),
    )

    out = _with_reply_pointer(ev, "follow up")

    assert out.startswith('[Replying to: "\\"' + ("x" * 499) + '"]')
    assert "x" * 500 not in out
    assert out.endswith("\nfollow up")


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
    status = r.dispatch(_ev("/status"))
    assert "provider=anthropic" in status
    assert "model=claude-sonnet-4-6" in status
    assert "provider=anthropic" in r.dispatch(_ev("/help"))
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
    assert "fast_mode: normal" in r.dispatch(_ev("/fast"))
    assert "fast_mode → priority" in r.dispatch(_ev("/fast on"))

    controls = r._session(key).meta["runtime_controls"]
    assert controls["provider"] == "openrouter"
    assert controls["reasoning_display"] == "live"
    assert controls["reasoning_effort"] == "high"
    assert controls["service_tier"] == "priority"
    who = r.dispatch(_ev("/whoami"))
    assert "provider: openrouter" in who
    assert "reasoning: display=live · effort=high" in who
    assert "fast_mode: priority" in who
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


def test_provider_switch_escapes_broken_config_provider(tmp_path, monkeypatch):
    r = _runner(tmp_path, monkeypatch)
    key = r._key(_ev("x"))
    r.config.data["model"] = {"provider": "missing-provider", "default": "bad-model"}
    r._agents[key] = object()

    out = r.dispatch(_ev("/provider openrouter"))

    assert "→ openrouter" in out
    assert r._session(key).meta["runtime_controls"]["provider"] == "openrouter"
    assert key not in r._agents


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


def test_gateway_goal_continuation_uses_surface_runner(tmp_path, monkeypatch):
    import threading
    from types import SimpleNamespace

    from aegis import goals
    from aegis.runs import RunStore
    from aegis.types import Message

    import aegis.gateway.runner as rmod

    r = _runner(tmp_path, monkeypatch)
    r.config.data["memory"]["enabled"] = False
    verdicts = iter([(False, "one more step"), (True, "complete")])
    monkeypatch.setattr(goals, "judge", lambda *_args, **_kwargs: next(verdicts))
    built = []

    class FakeAgent:
        def __init__(self, config, session):
            self.config = config
            self.session = session
            self.cwd = tmp_path
            self.provider = SimpleNamespace(name="fake", model="fake-model")
            self.tool_context = SimpleNamespace(session=session)
            self.budget = SimpleNamespace(api_call_count=0)
            self.cancel_event = threading.Event()
            self.tools_used = 0
            self.calls = 0

        def run(self, prompt, on_event=None):
            self.calls += 1
            self._trace_context = {
                "trace_id": f"trace_gateway_goal_{self.calls}",
                "turn_id": f"turn_gateway_goal_{self.calls}",
            }
            self.session.messages.append(
                Message.user(prompt.content if isinstance(prompt, Message) else str(prompt))
            )
            message = Message.assistant(f"reply {self.calls}")
            self.session.messages.append(message)
            return message

    def create(config, **kwargs):
        agent = FakeAgent(config, kwargs["session"])
        built.append(agent)
        return agent

    monkeypatch.setattr(rmod.Agent, "create", staticmethod(create))

    out = r.dispatch(_ev("/goal ship it"))

    assert "Goal achieved" in out
    assert built[0].calls == 2
    key = r._key(_ev("x"))
    assert goals.get(r._session(key)) is None
    runs = RunStore().list(session_id=key, limit=10)
    gateway_runs = [row for row in runs if row["surface"] == "gateway"]
    assert len(gateway_runs) == 2
    assert any(row["data"].get("goal_continuation") is True for row in gateway_runs)
    assert any("[Continuing toward your standing goal]" in row["prompt_preview"]
               for row in gateway_runs)


def test_gateway_goal_command_bypasses_mention_gate(tmp_path, monkeypatch):
    import threading
    from types import SimpleNamespace

    from aegis import goals
    from aegis.runs import RunStore
    from aegis.types import Message

    import aegis.gateway.runner as rmod

    r = _runner(tmp_path, monkeypatch)
    r.config.data["memory"]["enabled"] = False
    r.require_mention = True
    r.mention_triggers = ["@aegis"]
    monkeypatch.setattr(goals, "judge", lambda *_args, **_kwargs: (True, "done"))
    seen = []

    class FakeAgent:
        def __init__(self, session):
            self.config = r.config
            self.session = session
            self.cwd = tmp_path
            self.provider = SimpleNamespace(name="fake", model="fake-model")
            self.tool_context = SimpleNamespace(session=session)
            self.budget = SimpleNamespace(api_call_count=0)
            self.cancel_event = threading.Event()
            self.tools_used = 0

        def run(self, prompt, on_event=None):
            seen.append(prompt.content if isinstance(prompt, Message) else str(prompt))
            self._trace_context = {"trace_id": "trace_goal_gate", "turn_id": "turn_goal_gate"}
            self.session.messages.append(
                Message.user(prompt.content if isinstance(prompt, Message) else str(prompt))
            )
            message = Message.assistant("goal response")
            self.session.messages.append(message)
            return message

    monkeypatch.setattr(
        rmod.Agent,
        "create",
        staticmethod(lambda _config, **kwargs: FakeAgent(kwargs["session"])),
    )

    out = r.dispatch(_ev("/goal ship it"))

    assert "goal response" in out
    assert len(seen) == 1 and seen[0].startswith("ship it")
    key = r._key(_ev("x"))
    run = next(row for row in RunStore().list(session_id=key, limit=5)
               if row["surface"] == "gateway")
    assert run["prompt_preview"].startswith("ship it")


def test_gateway_resume_pending_directive_clears_after_success(tmp_path, monkeypatch):
    import threading
    from types import SimpleNamespace

    import aegis.gateway.runner as rmod
    from aegis.types import Message

    r = _runner(tmp_path, monkeypatch)
    r.config.data["memory"]["enabled"] = False
    ev = _ev("continue")
    key = r._key(ev)
    session = r._session(key)
    session.meta["resume_pending"] = True
    session.meta["resume_reason"] = "SIGTERM"
    session.meta["last_resume_marked_at"] = "2026-06-16T00:00:00+00:00"
    r.store.save(session)
    seen = []

    class FakeAgent:
        def __init__(self, session):
            self.config = r.config
            self.session = session
            self.cwd = tmp_path
            self.provider = SimpleNamespace(name="fake", model="fake-model")
            self.tool_context = SimpleNamespace(session=session)
            self.budget = SimpleNamespace(api_call_count=0)
            self.cancel_event = threading.Event()
            self.tools_used = 0

        def run(self, prompt, on_event=None):
            seen.append(prompt.content if isinstance(prompt, Message) else str(prompt))
            self._trace_context = {"trace_id": "trace_gateway_resume", "turn_id": "turn_gateway_resume"}
            self.session.messages.append(
                Message.user(prompt.content if isinstance(prompt, Message) else str(prompt))
            )
            message = Message.assistant("resumed")
            self.session.messages.append(message)
            return message

    monkeypatch.setattr(
        rmod.Agent,
        "create",
        staticmethod(lambda _config, **kwargs: FakeAgent(kwargs["session"])),
    )

    out = r.dispatch(ev)

    assert "resumed" in out
    assert seen[0].startswith("[Gateway recovery:")
    assert "Do not re-run previous tool calls" in seen[0]
    loaded = r.store.load(key)
    assert "resume_pending" not in loaded.meta
    assert "resume_reason" not in loaded.meta
    assert "last_resume_marked_at" not in loaded.meta


def test_gateway_process_notification_injects_internal_turn(tmp_path, monkeypatch):
    import threading
    import time
    from types import SimpleNamespace

    import aegis.gateway.pairing as pairing
    import aegis.gateway.runner as rmod
    from aegis.config import Config
    from aegis.gateway.base import BasePlatformAdapter
    from aegis.gateway.runner import GatewayRunner
    from aegis.types import Message

    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    monkeypatch.setattr(
        pairing.PairingStore,
        "is_authorized",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("auth called")),
    )

    class Adapter(BasePlatformAdapter):
        name = "telegram"

        def __init__(self):
            self.sent = []

        def send(self, chat_id: str, text: str) -> None:
            self.sent.append((chat_id, text))

    seen = []

    class FakeAgent:
        def __init__(self, session):
            self.config = cfg
            self.session = session
            self.cwd = tmp_path
            self.provider = SimpleNamespace(name="fake", model="fake-model")
            self.tool_context = SimpleNamespace(session=session)
            self.budget = SimpleNamespace(api_call_count=0)
            self.cancel_event = threading.Event()
            self.tools_used = 0

        def run(self, prompt, on_event=None):
            seen.append(prompt.content if isinstance(prompt, Message) else str(prompt))
            self._trace_context = {"trace_id": "trace_process_notify", "turn_id": "turn_process_notify"}
            self.session.messages.append(
                Message.user(prompt.content if isinstance(prompt, Message) else str(prompt))
            )
            message = Message.assistant("processed notification")
            self.session.messages.append(message)
            return message

    cfg = Config.load()
    runner = GatewayRunner(cfg, cwd=tmp_path)
    adapter = Adapter()
    runner.add(adapter)
    adapter._init_inbound_queue(runner.dispatch)
    monkeypatch.setattr(
        rmod.Agent,
        "create",
        staticmethod(lambda _config, **kwargs: FakeAgent(kwargs["session"])),
    )

    text = "[IMPORTANT: Background process proc_1 completed (exit code 0).]"
    submitted = runner._submit_process_notification(
        {
            "type": "completion",
            "session_id": "proc_1",
            "session_key": "telegram:c1:u1",
            "platform": "telegram",
            "chat_id": "c1",
            "user_id": "u1",
            "user_name": "alien",
            "thread_id": "topic",
            "message_id": "msg1",
        },
        text,
    )

    deadline = time.time() + 2
    while time.time() < deadline and not adapter.sent:
        time.sleep(0.01)

    assert submitted is True
    assert seen == [text]
    assert adapter.sent == [("c1", "processed notification")]
    assert runner._session("telegram:c1:u1").messages[0].content == text


def test_gateway_process_notification_requeues_when_adapter_missing(tmp_path, monkeypatch):
    from aegis.config import Config
    from aegis.gateway.runner import GatewayRunner
    from aegis.tools.process_registry import process_registry

    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    process_registry.drain_notifications()
    process_registry.completion_queue.put({
        "type": "completion",
        "session_id": "proc_missing_adapter",
        "session_key": "telegram:c1:u1",
        "platform": "telegram",
        "chat_id": "c1",
        "command": "echo done",
        "exit_code": 0,
        "output": "done",
    })
    runner = GatewayRunner(Config.load(), cwd=tmp_path)

    assert runner._drain_process_notifications() == 0
    events = process_registry.drain_notifications(max_events=1)

    assert len(events) == 1
    assert events[0][0]["session_id"] == "proc_missing_adapter"
    process_registry.drain_notifications()


def test_gateway_tracks_child_session_after_run_split(tmp_path, monkeypatch):
    import threading
    from types import SimpleNamespace

    from aegis.session import Session
    from aegis.types import Message

    import aegis.gateway.runner as rmod

    r = _runner(tmp_path, monkeypatch)
    r.config.data["memory"]["enabled"] = False
    seen_sessions = []

    class FakeAgent:
        def __init__(self, session):
            self.config = r.config
            self.session = session
            self.cwd = tmp_path
            self.provider = SimpleNamespace(name="fake", model="fake-model")
            self.tool_context = SimpleNamespace(session=session)
            self.budget = SimpleNamespace(api_call_count=0)
            self.cancel_event = threading.Event()
            self.tools_used = 0
            self.calls = 0

        def run(self, prompt, on_event=None):
            self.calls += 1
            seen_sessions.append(self.session.id)
            self._trace_context = {
                "trace_id": f"trace_gateway_split_{self.calls}",
                "turn_id": f"turn_gateway_split_{self.calls}",
            }
            if self.calls == 1:
                child = Session.create("gateway child", parent_id=self.session.id)
                child.messages = [
                    Message.user(prompt.content if isinstance(prompt, Message) else str(prompt))
                ]
                self.session = child
                self.tool_context.session = child
                r.store.save(child)
            else:
                self.session.messages.append(
                    Message.user(prompt.content if isinstance(prompt, Message) else str(prompt))
                )
            message = Message.assistant(f"reply {self.calls}")
            self.session.messages.append(message)
            return message

    monkeypatch.setattr(
        rmod.Agent,
        "create",
        staticmethod(lambda _config, **kwargs: FakeAgent(kwargs["session"])),
    )

    key = r._key(_ev("x"))
    parent_id = r._session(key).id
    assert r.dispatch(_ev("first")) == "reply 1"
    child_id = r._session(key).id
    assert child_id != parent_id

    assert r.dispatch(_ev("second")) == "reply 2"
    assert seen_sessions == [parent_id, child_id]
