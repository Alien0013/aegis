from __future__ import annotations


def test_sdk_run_saves_session_and_trace():
    from aegis.config import Config
    from aegis.runs import RunStore
    from aegis.sdk import AegisClient
    from aegis.types import LLMResponse, Usage
    from conftest import FakeProvider

    cfg = Config.load()
    cfg.data["memory"]["enabled"] = False
    provider = FakeProvider([
        LLMResponse(
            text="sdk ok",
            usage=Usage(input_tokens=7, output_tokens=3, cache_read=1, cache_write=2),
        )
    ])
    events = []
    client = AegisClient(config=cfg, provider_factory=lambda **_: provider, include_mcp=False)

    result = client.run("hello from python", title="sdk smoke", on_event=events.append)

    assert result.text == "sdk ok"
    assert result.run_id.startswith("run_")
    assert RunStore().get(result.run_id)["surface"] == "sdk"
    assert result.session_id
    assert result.trace_id.startswith("trace_")
    assert result.provider == "fake"
    assert result.model == "fake-model"
    assert result.usage.input_tokens == 7
    assert result.usage.cache_read == 1
    assert any(e["type"] == "assistant_message" for e in result.events)
    assert events == result.events

    session = client.resume(result.session_id)
    assert session.title == "sdk smoke"
    assert [m.role for m in session.messages][-2:] == ["user", "assistant"]
    assert session.meta["runtime"]["provider"] == "fake"
    assert session.meta["runtime"]["model"] == "fake-model"
    assert session.meta["usage"]["input_tokens"] == 7
    assert session.meta["usage"]["cache_write"] == 2
    assert session.meta["trace_id"] == result.trace_id
    assert session.meta["last_trace_id"] == result.trace_id
    assert session.meta["last_run_id"] == result.run_id
    assert session.meta["tool_call_count"] == 0
    assert client.list_sessions(limit=1)[0]["id"] == result.session_id
    assert client.get_run(result.run_id)["surface"] == "sdk"
    assert client.list_runs(session_id=result.session_id)[0]["id"] == result.run_id

    trace = client.get_trace(result.trace_id)
    assert trace is not None
    assert trace["session_id"] == result.session_id
    assert trace["cache_read"] == 1
    assert {span["kind"] for span in trace["spans"]} >= {"turn", "provider_call"}
    turn_span = next(span for span in trace["spans"] if span["kind"] == "turn")
    assert turn_span["data"]["prompt"]["system_prompt_hash"] == session.meta["system_prompt_hash"]
    assert turn_span["data"]["prompt"]["prompt_parts"]


def test_sdk_resume_branch_replay_and_eval_suite(tmp_path):
    import json

    from aegis.config import Config
    from aegis.sdk import AegisClient
    from aegis.types import LLMResponse
    from conftest import FakeProvider

    cfg = Config.load()
    cfg.data["memory"]["enabled"] = False
    provider = FakeProvider([LLMResponse(text="first"), LLMResponse(text="second")])
    client = AegisClient(config=cfg, provider_factory=lambda **_: provider, include_mcp=False)

    first = client.run("start")
    second = client.run("continue", session_id=first.session_id)

    assert second.session_id == first.session_id
    replay = client.replay_session(first.session_id)
    assert replay["source"] == "session"
    assert [step["role"] for step in replay["steps"] if step["kind"] == "message"].count("user") == 2
    assert replay["meta"]["system_prompt_hash"]
    assert replay["meta"]["prompt_parts"]

    branch = client.branch_session(first.session_id, title="branch")
    assert branch.parent_id == first.session_id
    assert branch.title == "branch"

    grade = client.evaluate_trace(second.trace_id)
    assert grade["passed"] is True

    suite = tmp_path / "suite.jsonl"
    suite.write_text(
        json.dumps({
            "name": "session-final",
            "session_id": first.session_id,
            "expected_contains": "second",
        }) + "\n",
        encoding="utf-8",
    )
    run = client.run_eval_suite(suite)
    assert run["total"] == 1
    assert run["passed"] == 1
    assert client.list_eval_runs(limit=1)[0]["id"] == run["id"]
    assert client.get_eval_run(run["id"])["results"][0]["case"] == "session-final"


def test_sdk_reuses_session_scoped_agent_provider():
    from aegis.config import Config
    from aegis.sdk import AegisClient
    from aegis.types import LLMResponse
    from conftest import FakeProvider

    cfg = Config.load()
    cfg.data["memory"]["enabled"] = False
    created = []

    def factory(**_kwargs):
        provider = FakeProvider([LLMResponse(text="first"), LLMResponse(text="second")])
        created.append(provider)
        return provider

    client = AegisClient(config=cfg, provider_factory=factory, include_mcp=False)

    first = client.run("one")
    second = client.run("two", session_id=first.session_id)

    assert first.text == "first"
    assert second.text == "second"
    assert len(created) == 1
    assert created[0].calls == 2


def test_sdk_respects_session_runtime_controls():
    from aegis.config import Config
    from aegis.sdk import AegisClient
    from aegis.session import Session, SessionStore
    from aegis.types import LLMResponse

    cfg = Config.load()
    cfg.data["memory"]["enabled"] = False
    session = Session.create("sdk controls")
    session.meta["runtime_controls"] = {
        "provider": "custom",
        "model": "session-model",
        "reasoning_effort": "high",
        "reasoning_display": "live",
        "busy_mode": "interrupt",
    }
    SessionStore().save(session)
    captured = {}

    class Provider:
        context_length = 200_000
        name = "custom"
        api_mode = None
        auth = None

        def __init__(self, model):
            self.model = model
            self.last_reasoning = None

        def describe(self):
            return f"custom/{self.model}"

        def complete(self, messages, tools=None, stream=False, on_delta=None, reasoning="off", **_kwargs):
            self.last_reasoning = reasoning
            return LLMResponse(text="controlled")

    def factory(**kwargs):
        captured.update(kwargs)
        provider = Provider(kwargs.get("model"))
        captured["provider_obj"] = provider
        return provider

    client = AegisClient(config=cfg, provider_factory=factory, include_mcp=False)

    result = client.run("hello", session_id=session.id)

    assert result.text == "controlled"
    assert captured["provider_name"] == "custom"
    assert captured["model"] == "session-model"
    assert captured["provider_obj"].last_reasoning == "high"
    assert cfg.get("display.reasoning") == "live"
    assert cfg.get("gateway.busy_mode") == "interrupt"


def test_sdk_expands_context_references_and_records_metadata(tmp_path):
    from aegis.config import Config
    from aegis.sdk import AegisClient
    from aegis.types import LLMResponse

    class Provider:
        context_length = 200_000
        name = "fake"
        model = "fake-model"
        api_mode = None
        auth = None

        def __init__(self):
            self.messages = []

        def complete(self, messages, **_kwargs):
            self.messages.append([m.content for m in messages])
            return LLMResponse(text="ok")

    (tmp_path / "brief.md").write_text("sdk attached context", encoding="utf-8")
    cfg = Config.load()
    cfg.data["memory"]["enabled"] = False
    provider = Provider()
    client = AegisClient(config=cfg, provider_factory=lambda **_: provider, cwd=tmp_path, include_mcp=False)

    result = client.run("read @brief.md")
    session = client.resume(result.session_id)

    assert any("sdk attached context" in content for content in provider.messages[0])
    assert session.meta["last_context_references"]["count"] == 1
