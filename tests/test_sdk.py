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
    run = RunStore().get(result.run_id)
    assert run["surface"] == "sdk"
    assert run["data"]["provider"] == "fake"
    assert run["data"]["model"] == "fake-model"
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


def test_sdk_provider_metadata_includes_run_id(tmp_path):
    from aegis.config import Config
    from aegis.sdk import AegisClient
    from aegis.types import LLMResponse

    class MetadataProvider:
        context_length = 200_000
        name = "fake"
        model = "fake-model"
        api_mode = None
        auth = None

        def __init__(self):
            self.metadata = {}

        def describe(self):
            return "fake"

        def complete(self, messages, tools=None, stream=False, on_delta=None, metadata=None):
            self.metadata = dict(metadata or {})
            return LLMResponse(text="ok")

    cfg = Config.load()
    cfg.data["memory"]["enabled"] = False
    provider = MetadataProvider()
    client = AegisClient(config=cfg, provider_factory=lambda **_: provider, cwd=tmp_path, include_mcp=False)

    result = client.run("sdk breadcrumbs", title="sdk metadata")

    assert provider.metadata["session_id"] == result.session_id
    assert provider.metadata["trace_id"] == result.trace_id
    assert provider.metadata["turn_id"] == result.turn_id
    assert provider.metadata["run_id"] == result.run_id


def test_sdk_resume_redirects_to_compression_tip():
    from aegis.config import Config
    from aegis.sdk import AegisClient
    from aegis.session import Session, SessionStore
    from aegis.types import Message

    cfg = Config.load()
    cfg.data["memory"]["enabled"] = False
    store = SessionStore()
    parent = Session(id="sdk-parent", title="long task")
    parent.messages = [Message.user("before compaction")]
    store.save(parent)
    child = store.fork(parent)
    child.id = "sdk-child"
    child.parent_id = parent.id
    child.meta["creator_kind"] = "compression"
    child.messages.append(Message.user("after compaction"))
    store.save(child)

    client = AegisClient(config=cfg, store=store, include_mcp=False)

    resumed = client.resume(parent.id)

    assert resumed.id == child.id
    assert [m.content for m in resumed.messages if m.role == "user"] == ["after compaction"]


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
    from aegis.runs import RunStore
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
        "service_tier": "priority",
    }
    session.meta["runtime"] = {
        "provider": "stale-provider",
        "model": "stale-model",
        "reasoning_effort": "low",
        "reasoning_display": "summary",
        "busy_mode": "queue",
        "service_tier": "normal",
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
            self.last_service_tier = None

        def describe(self):
            return f"custom/{self.model}"

        def complete(
            self,
            messages,
            tools=None,
            stream=False,
            on_delta=None,
            reasoning="off",
            service_tier="",
            **_kwargs,
        ):
            self.last_reasoning = reasoning
            self.last_service_tier = service_tier
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
    run = RunStore().get(result.run_id)
    assert run["data"]["provider"] == "custom"
    assert run["data"]["model"] == "session-model"
    assert run["data"]["service_tier"] == "priority"
    assert captured["provider_obj"].last_reasoning == "high"
    assert captured["provider_obj"].last_service_tier == "priority"
    assert cfg.get("display.reasoning") == "live"
    assert cfg.get("gateway.busy_mode") == "interrupt"


def test_sdk_respects_session_runtime_metadata_without_controls():
    from aegis.config import Config
    from aegis.runs import RunStore
    from aegis.sdk import AegisClient
    from aegis.session import Session, SessionStore
    from aegis.types import LLMResponse

    cfg = Config.load()
    cfg.data["memory"]["enabled"] = False
    session = Session.create("sdk runtime")
    session.meta["runtime"] = {
        "provider": "runtime-provider",
        "model": "runtime-model",
        "reasoning_effort": "high",
        "reasoning_display": "live",
        "busy_mode": "steer",
    }
    SessionStore().save(session)
    captured = {}

    class Provider:
        context_length = 200_000
        name = "runtime-provider"
        api_mode = None
        auth = None

        def __init__(self, model):
            self.model = model
            self.last_reasoning = None

        def complete(self, messages, **kwargs):
            self.last_reasoning = kwargs.get("reasoning")
            return LLMResponse(text="runtime ok")

    def factory(**kwargs):
        captured.update(kwargs)
        provider = Provider(kwargs.get("model"))
        captured["provider_obj"] = provider
        return provider

    client = AegisClient(config=cfg, provider_factory=factory, include_mcp=False)

    result = client.run("hello", session_id=session.id)

    assert result.text == "runtime ok"
    assert captured["provider_name"] == "runtime-provider"
    assert captured["model"] == "runtime-model"
    assert captured["provider_obj"].last_reasoning == "high"
    assert cfg.get("display.reasoning") == "live"
    assert cfg.get("gateway.busy_mode") == "steer"
    run = RunStore().get(result.run_id)
    assert run["data"]["provider"] == "runtime-provider"
    assert run["data"]["model"] == "runtime-model"


def test_sdk_run_metadata_updates_to_final_provider(tmp_path):
    from aegis.config import Config
    from aegis.runs import RunStore
    from aegis.sdk import AegisClient
    from aegis.types import LLMResponse

    class Provider:
        context_length = 200_000
        name = "initial-provider"
        model = "initial-model"
        api_mode = None
        auth = None

        def complete(self, messages, **_kwargs):
            self.name = "final-provider"
            self.model = "final-model"
            return LLMResponse(text="routed")

    cfg = Config.load()
    cfg.data["memory"]["enabled"] = False
    provider = Provider()
    client = AegisClient(
        config=cfg,
        provider_factory=lambda **_: provider,
        cwd=tmp_path,
        include_mcp=False,
    )

    result = client.run("route me")

    assert result.provider == "final-provider"
    assert result.model == "final-model"
    run = RunStore().get(result.run_id)
    assert run["data"]["provider"] == "final-provider"
    assert run["data"]["model"] == "final-model"


def test_sdk_retargets_run_after_session_switch(monkeypatch, tmp_path):
    from aegis.config import Config
    from aegis.runs import RunStore
    from aegis.sdk import AegisClient
    from aegis.session import Session, SessionStore
    from aegis.tracing import TraceStore
    from aegis.types import LLMResponse, Message
    from conftest import FakeProvider

    cfg = Config.load()
    cfg.data["memory"]["enabled"] = False
    store = SessionStore()
    parent = Session.create("sdk parent")
    child = Session.create("sdk child", parent_id=parent.id)
    store.save(parent)
    trace_id = "trace_sdk_child"
    turn_id = "turn_sdk_child"

    def fake_run(self, _user_input, _emit=None):
        trace_store = TraceStore.from_config(cfg)
        span = trace_store.start_span(
            trace_id=trace_id,
            session_id=parent.id,
            turn_id=turn_id,
            kind="turn",
        )
        trace_store.finish_span(span["span_id"], status="ok")
        self.session = child
        self.tool_context.session = child
        self.store.save(child)
        self._trace_context = {"trace_id": trace_id, "turn_id": turn_id}
        return Message.assistant("child reply")

    monkeypatch.setattr("aegis.sdk.Agent.run", fake_run)
    client = AegisClient(
        config=cfg,
        store=store,
        provider_factory=lambda **_kwargs: FakeProvider([LLMResponse(text="unused")]),
        cwd=tmp_path,
        include_mcp=False,
    )

    result = client.run("split", session_id=parent.id)

    assert result.session_id == child.id
    assert RunStore().get(result.run_id)["session_id"] == child.id
    assert TraceStore.from_config(cfg).get_trace(result.trace_id)["session_id"] == child.id


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


def test_sdk_mcp_context_reference_uses_client_config(tmp_path):
    import sys

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

        def describe(self):
            return "fake"

        def complete(self, messages, **_kwargs):
            self.messages.append([m.content for m in messages])
            return LLMResponse(text="ok")

    server = tmp_path / "mcp_srv.py"
    server.write_text(
        "import json,sys\n"
        "def s(o): sys.stdout.write(json.dumps(o)+chr(10)); sys.stdout.flush()\n"
        "for line in sys.stdin:\n"
        "    line=line.strip()\n"
        "    if not line: continue\n"
        "    m=json.loads(line); mid=m.get('id'); meth=m.get('method')\n"
        "    if meth=='initialize': s({'jsonrpc':'2.0','id':mid,'result':{'protocolVersion':'2025-06-18','capabilities':{},'serverInfo':{'name':'t','version':'1'}}})\n"
        "    elif meth=='resources/read': s({'jsonrpc':'2.0','id':mid,'result':{'contents':[{'uri':m['params']['uri'],'text':'sdk mcp attached context'}]}})\n",
        encoding="utf-8",
    )
    cfg = Config.load()
    cfg.data["memory"]["enabled"] = False
    cfg.data.setdefault("mcp", {})["servers"] = {
        "sdk": {"command": sys.executable, "args": [str(server)]}
    }
    provider = Provider()
    client = AegisClient(config=cfg, provider_factory=lambda **_: provider, cwd=tmp_path, include_mcp=False)

    result = client.run("review @mcp:sdk:note://a")
    session = client.resume(result.session_id)

    assert any("sdk mcp attached context" in content for content in provider.messages[0])
    assert session.meta["last_context_references"]["references"][0]["kind"] == "mcp"
    assert session.meta["last_context_references"]["references"][0]["target"] == "sdk:note://a"
