"""Product-surface upgrades: CLI, plugin manifests, MCP catalog/filtering."""

from __future__ import annotations


def test_cli_parser_exposes_upgrade_commands():
    from aegis.cli.main import build_parser

    parser = build_parser()
    assert parser.parse_args(["tui"]).command == "tui"
    assert parser.parse_args(["trace", "list"]).command == "trace"
    assert parser.parse_args(["eval", "list"]).command == "eval"
    assert parser.parse_args(["plugins", "install", "./plug.py"]).action == "install"
    assert parser.parse_args(["plugins", "enable", "hello"]).action == "enable"
    assert parser.parse_args(["mcp", "catalog"]).action == "catalog"
    assert parser.parse_args(["mcp", "install", "filesystem"]).action == "install"
    assert parser.parse_args(["mcp", "tools", "filesystem"]).action == "tools"
    assert parser.parse_args(["trace", "export", "trace_1", "--out", "x.jsonl"]).action == "export"
    assert parser.parse_args(["trace", "list", "--status", "error"]).status == "error"
    assert parser.parse_args(["eval", "show", "eval_1"]).action == "show"
    assert parser.parse_args(["rpc"]).command == "rpc"
    assert parser.parse_args(["model", "doctor"]).action == "doctor"


def test_tui_command_invokes_fullscreen_surface(monkeypatch):
    from argparse import Namespace

    from aegis.cli import main
    from aegis.cli import tui
    from aegis.config import Config

    seen = {}

    def fake_run(config, **kwargs):
        seen["config"] = config
        seen.update(kwargs)

    monkeypatch.setattr(tui, "run_fullscreen", fake_run)

    rc = main.cmd_tui(
        Namespace(model="m1", provider="p1", resume=None, cont=False, yolo=True),
        Config.load(),
    )

    assert rc == 0
    assert seen["model"] == "m1"
    assert seen["provider_name"] == "p1"
    assert seen["auto"] is True
    assert seen["session"].id


def test_model_doctor_prints_resolver_report(capsys):
    from argparse import Namespace

    from aegis.cli import main
    from aegis.config import Config

    cfg = Config.load()
    cfg.data["model"] = {"provider": "localtest", "default": "local-model"}
    cfg.data["custom_providers"] = [
        {
            "name": "localtest",
            "base_url": "http://local.test/v1",
            "api_mode": "chat_completions",
            "context_length": 70_000,
        }
    ]
    cfg.data["fallback_providers"] = [{"provider": "ollama", "model": "llama3.1"}]
    cfg.data["routing"] = [{"match": "deploy", "provider": "localtest", "model": "local-routed"}]

    assert main.cmd_model(Namespace(action="doctor", provider=None, model=None), cfg) == 0

    out = capsys.readouterr().out
    assert "transport: chat_completions" in out
    assert "capabilities: tools, stream" in out
    assert "auth:      no-auth (local) (ready)" in out
    assert "fallbacks:" in out and "ollama / llama3.1" in out
    assert "routing:" in out and "/deploy/ -> localtest / local-routed (known)" in out


def test_batch_command_records_batch_surface(monkeypatch, tmp_path):
    from argparse import Namespace

    from aegis.cli import main
    from aegis.config import Config
    from aegis.providers import fallback
    from aegis.runs import RunStore
    from aegis.types import LLMResponse
    from conftest import FakeProvider

    prompts = tmp_path / "prompts.txt"
    prompts.write_text("first\n# skip\nsecond\n", encoding="utf-8")
    cfg = Config.load()
    cfg.data["memory"]["enabled"] = False
    provider = FakeProvider([LLMResponse(text="one"), LLMResponse(text="two")])
    monkeypatch.setattr(fallback, "build_with_fallbacks", lambda *_args, **_kwargs: provider)

    assert main.cmd_batch(Namespace(file=str(prompts), model=None, provider=None), cfg) == 0

    runs = RunStore().list(surface="batch", limit=5)
    assert len(runs) == 2
    by_prompt = {row["prompt_preview"]: row for row in runs}
    assert by_prompt["first"]["data"]["batch_source"] == str(prompts)
    assert by_prompt["first"]["data"]["batch_index"] == 1
    assert by_prompt["first"]["data"]["batch_total"] == 2
    assert by_prompt["second"]["data"]["batch_index"] == 2


def test_model_set_rejects_unknown_provider_with_suggestion(capsys):
    from argparse import Namespace

    from aegis.cli import main
    from aegis.config import Config

    cfg = Config.load()

    rc = main.cmd_model(Namespace(action="set", provider="anthropc", model=None), cfg)

    err = capsys.readouterr().err
    assert rc == 1
    assert "Unknown provider 'anthropc'" in err
    assert "anthropic" in err
    assert cfg.get("model.provider") != "anthropc"


def test_chat_query_nonstream_prints_once(monkeypatch, capsys):
    from argparse import Namespace

    from aegis.cli import main, repl
    from aegis.config import Config
    from aegis.providers import fallback
    from aegis.types import LLMResponse
    from conftest import FakeProvider

    cfg = Config.load()
    cfg.data["memory"]["enabled"] = False
    cfg.data.setdefault("agent", {})["stream"] = False
    monkeypatch.setattr(repl, "_console", None)
    monkeypatch.setattr(
        fallback,
        "build_with_fallbacks",
        lambda *_args, **_kwargs: FakeProvider([LLMResponse(text="single answer")]),
    )

    rc = main.cmd_chat(
        Namespace(
            query="hello",
            prompt=[],
            model=None,
            provider=None,
            resume=None,
            cont=False,
            yolo=True,
            image=None,
            worktree=False,
        ),
        cfg,
    )

    assert rc == 0
    assert capsys.readouterr().out.count("single answer") == 1


def test_batch_nonstream_prints_each_result_once(monkeypatch, tmp_path, capsys):
    from argparse import Namespace

    from aegis.cli import main, repl
    from aegis.config import Config
    from aegis.providers import fallback
    from aegis.types import LLMResponse
    from conftest import FakeProvider

    prompts = tmp_path / "prompts.txt"
    prompts.write_text("first\nsecond\n", encoding="utf-8")
    cfg = Config.load()
    cfg.data["memory"]["enabled"] = False
    cfg.data.setdefault("agent", {})["stream"] = False
    provider = FakeProvider([LLMResponse(text="batch one"), LLMResponse(text="batch two")])
    monkeypatch.setattr(repl, "_console", None)
    monkeypatch.setattr(fallback, "build_with_fallbacks", lambda *_args, **_kwargs: provider)

    assert main.cmd_batch(Namespace(file=str(prompts), model=None, provider=None), cfg) == 0
    out = capsys.readouterr().out
    assert out.count("batch one") == 1
    assert out.count("batch two") == 1


def test_tui_formats_events_and_captures_slash():
    from aegis.cli import tui

    assert tui._event_line({"type": "iteration", "n": 2, "max": 5}) == "iteration 2/5"
    assert "tool start" in tui._event_line({
        "type": "tool_start",
        "name": "bash",
        "args": {"command": "pytest"},
    })
    assert "tool ok" in tui._event_line({
        "type": "tool_result",
        "name": "bash",
        "summary": "done",
        "is_error": False,
    })
    result, output = tui._capture_slash("/help", object())
    assert result == ""
    assert "/trace" in output and "/evals" in output


def test_tui_busy_input_modes():
    from aegis.cli import tui
    from aegis.config import Config

    cfg = Config.load()

    class Agent:
        def __init__(self):
            self.steered = []
            self.cancelled = 0

        def steer(self, text):
            self.steered.append(text)
            return True

        def cancel(self):
            self.cancelled += 1

    agent = Agent()
    pending = []

    cfg.data.setdefault("gateway", {})["busy_mode"] = "queue"
    assert tui._handle_busy_input("next", agent, cfg, pending) == "queued"
    assert pending == ["next"] and agent.steered == [] and agent.cancelled == 0

    cfg.data["gateway"]["busy_mode"] = "steer"
    assert tui._handle_busy_input("guide", agent, cfg, pending) == "steered"
    assert agent.steered == ["guide"] and pending == ["next"]

    cfg.data["gateway"]["busy_mode"] = "interrupt"
    assert tui._handle_busy_input("replace", agent, cfg, pending) == "interrupt"
    assert pending == ["replace"] and agent.cancelled == 1

    assert tui._handle_busy_input("stop", agent, cfg, pending) == "cancelled"
    assert pending == ["replace"] and agent.cancelled == 2


def test_tui_session_signature_tracks_switch_and_rewrite():
    from aegis.cli import tui
    from aegis.session import Session
    from aegis.types import Message

    alpha = Session.create("alpha")
    alpha.messages = [Message.user("alpha prompt"), Message.assistant("alpha answer")]
    beta = Session.create("beta")
    beta.messages = [Message.user("beta prompt")]

    assert tui._session_signature(alpha) != tui._session_signature(beta)
    before = tui._session_signature(alpha)
    alpha.messages[-1] = Message.assistant("changed answer")
    assert tui._session_signature(alpha) != before
    assert tui._render_session(alpha) == "user> alpha prompt\n\nassistant> changed answer"


def test_terminal_slash_help_is_searchable():
    from aegis.cli import repl

    session_help = "\n".join(repl.slash_help_lines("session"))
    assert "/sessions" in session_help
    assert "/resume" in session_help
    assert "/branch" in session_help
    assert "pick recent sessions" in session_help


def test_terminal_slash_completer_uses_command_metadata():
    from prompt_toolkit.document import Document

    from aegis.cli import repl

    completer = repl.make_slash_completer()
    assert completer is not None
    completions = list(completer.get_completions(Document("/tr", cursor_position=3), None))

    trace = next(c for c in completions if c.text == "/trace")
    assert "/trace [id]" in str(trace.display)
    assert "observability" in str(trace.display_meta)


def test_terminal_status_state_summarizes_progress():
    from aegis.cli.repl import TerminalStatusState

    state = TerminalStatusState()
    state.update({"type": "iteration", "n": 2, "max": 5})
    state.update({"type": "tool_start", "name": "bash"})
    assert "iter 2/5" in state.segment()
    assert "tool bash" in state.segment()
    state.update({"type": "tool_result", "name": "bash", "is_error": False})
    state.update({"type": "budget_exhausted"})
    assert "last tool bash" in state.segment()
    assert "budget exhausted" in state.segment()


def test_terminal_session_picker_resume_and_branch(monkeypatch):
    from types import SimpleNamespace

    from aegis.cli import repl
    from aegis.config import Config
    from aegis.session import Session, SessionStore
    from aegis.types import Message

    store = SessionStore()
    alpha = Session.create("alpha build")
    alpha.messages = [Message.user("alpha prompt"), Message.assistant("alpha answer")]
    alpha.meta["summary"] = "alpha summary"
    alpha.meta["last_run_id"] = "run_alpha123456"
    alpha.meta["last_trace_id"] = "trace_alpha123456"
    beta = Session.create("beta launch")
    beta.messages = [Message.user("beta prompt"), Message.assistant("beta answer")]
    store.save(alpha)
    store.save(beta)

    agent = SimpleNamespace(
        session=beta,
        tool_context=SimpleNamespace(session=beta),
        config=Config.load(),
        refresh_volatile=lambda: None,
    )
    lines = []
    monkeypatch.setattr(repl, "_out", lambda text="", style=None: lines.append(str(text)))

    assert repl.handle_slash("/sessions alpha", agent, store=store) == ""
    picker = "\n".join(lines)
    assert "alpha build" in picker
    assert "run run_alpha123" in picker
    assert "trace trace_alpha1" in picker
    assert repl.handle_slash("/resume 1", agent, store=store) == ""
    assert agent.session.id == alpha.id
    assert agent.tool_context.session.id == alpha.id

    assert repl.handle_slash("/branch alpha experiment", agent, store=store, surface="tui") == ""
    assert agent.session.parent_id == alpha.id
    assert agent.session.title == "alpha experiment"
    assert agent.session.meta["branch_surface"] == "tui"
    assert store.load(agent.session.id).parent_id == alpha.id


def test_terminal_goal_continuation_uses_surface_runner(monkeypatch):
    from aegis import goals
    from aegis.cli import repl
    from aegis.config import Config
    from aegis.providers import registry
    from aegis.runs import RunStore
    from aegis.session import Session, SessionStore
    from aegis.surface import SurfaceRunner
    from aegis.types import LLMResponse
    from conftest import FakeProvider

    cfg = Config.load()
    cfg.data["memory"]["enabled"] = False
    provider = FakeProvider([LLMResponse(text="first"), LLMResponse(text="second")])
    monkeypatch.setattr(registry, "build_provider", lambda *_args, **_kwargs: provider)
    verdicts = iter([(False, "one more step"), (True, "complete")])
    monkeypatch.setattr(goals, "judge", lambda *_args, **_kwargs: next(verdicts))

    store = SessionStore()
    runner = SurfaceRunner(cfg, store=store, include_mcp=False)
    session = Session.create("terminal goal")
    agent = runner.make_agent(session=session, include_mcp=False)
    goals.set_goal(agent.session, "finish the terminal flow", max_turns=3)

    repl.run_terminal_turn(
        "start",
        agent,
        runner,
        store,
        surface="tui",
        on_event=lambda _event: None,
        notify=lambda _line: None,
    )

    assert provider.calls == 2
    assert goals.get(agent.session) is None
    runs = RunStore().list(session_id=agent.session.id, limit=10)
    assert len([r for r in runs if r["surface"] == "tui"]) == 2
    assert any("[Continuing toward your standing goal]" in r["prompt_preview"] for r in runs)
    assert agent.session.meta["last_run_id"] in {r["id"] for r in runs}


def test_terminal_goal_command_returns_start_prompt(monkeypatch):
    from aegis.cli import repl
    from aegis.config import Config
    from aegis.providers import registry
    from aegis.session import Session, SessionStore
    from aegis.surface import SurfaceRunner
    from conftest import FakeProvider

    cfg = Config.load()
    cfg.data["memory"]["enabled"] = False
    monkeypatch.setattr(registry, "build_provider", lambda *_args, **_kwargs: FakeProvider())
    store = SessionStore()
    runner = SurfaceRunner(cfg, store=store, include_mcp=False)
    agent = runner.make_agent(session=Session.create(), include_mcp=False)
    lines = []

    prompt = repl.handle_goal_command(
        "/goal polish the TUI",
        agent,
        store,
        out=lambda text, _style=None: lines.append(text),
    )

    assert prompt == "polish the TUI"
    assert "Goal set" in lines[0]
    assert store.load(agent.session.id).meta["goal"]["text"] == "polish the TUI"


def test_terminal_runtime_controls_persist_and_resume(monkeypatch):
    from aegis.cli import repl
    from aegis.config import Config
    from aegis.providers import registry
    from aegis.session import Session, SessionStore
    from aegis.surface import SurfaceRunner
    from conftest import FakeProvider

    built = []

    def fake_build(_config, model=None, name=None):
        provider = FakeProvider()
        provider.name = name or "fake"
        provider.model = model or "fake-model"
        built.append((provider.name, provider.model))
        return provider

    monkeypatch.setattr(registry, "build_provider", fake_build)
    cfg = Config.load()
    cfg.data["memory"]["enabled"] = False
    cfg.data["custom_providers"] = [{
        "name": "tuned",
        "base_url": "http://tuned.local/v1",
        "api_mode": "chat_completions",
        "default_model": "test-model",
        "context_length": 70_000,
    }]
    store = SessionStore()
    runner = SurfaceRunner(cfg, store=store, include_mcp=False)
    session = Session.create("runtime controls")
    store.save(session)
    agent = runner.make_agent(session=session, include_mcp=False)

    repl.handle_slash("/model tuned/test-model", agent, runner=runner, store=store)
    repl.handle_slash("/think high", agent, runner=runner, store=store)
    repl.handle_slash("/reasoning live", agent, runner=runner, store=store)
    repl.handle_slash("/busy interrupt", agent, runner=runner, store=store)

    saved = store.load(agent.session.id)
    assert saved.meta["runtime_controls"] == {
        "provider": "tuned",
        "model": "test-model",
        "reasoning_effort": "high",
        "reasoning_display": "live",
        "busy_mode": "interrupt",
    }
    child = store.fork(saved)
    assert child.meta["runtime_controls"] == saved.meta["runtime_controls"]

    cfg2 = Config.load()
    cfg2.data["memory"]["enabled"] = False
    cfg2.data.setdefault("display", {})["reasoning"] = "summary"
    cfg2.data.setdefault("gateway", {})["busy_mode"] = "queue"
    resumed = SurfaceRunner(cfg2, store=store, include_mcp=False).make_agent(
        session=store.load(saved.id),
        include_mcp=False,
    )

    assert built[-1] == ("tuned", "test-model")
    assert resumed.reasoning == "high"
    assert resumed.config.get("display.reasoning") == "live"
    assert resumed.config.get("gateway.busy_mode") == "interrupt"


def test_terminal_model_override_rejects_unknown_provider(monkeypatch, tmp_path):
    from aegis.agent.agent import Agent
    from aegis.cli import repl
    from aegis.config import Config
    from aegis.session import Session, SessionStore
    from conftest import FakeProvider

    cfg = Config.load()
    cfg.data["memory"]["enabled"] = False
    provider = FakeProvider()
    agent = Agent(config=cfg, provider=provider, session=Session.create(), cwd=tmp_path)
    out = []
    monkeypatch.setattr(repl, "_out", lambda text="", style=None: out.append(str(text)))

    repl.handle_slash("/model anthropc/claude-sonnet-4-6", agent, store=SessionStore())

    joined = "\n".join(out)
    assert "Unknown provider 'anthropc'" in joined
    assert "anthropic" in joined
    assert "runtime_controls" not in agent.session.meta
    assert agent.provider is provider


def test_terminal_resume_reapplies_session_runtime(monkeypatch):
    from aegis.cli import repl
    from aegis.config import Config
    from aegis.providers import registry
    from aegis.session import Session, SessionStore
    from aegis.surface import SurfaceRunner
    from conftest import FakeProvider

    def fake_build(_config, model=None, name=None):
        provider = FakeProvider()
        provider.name = name or "fake"
        provider.model = model or "fake-model"
        return provider

    monkeypatch.setattr(registry, "build_provider", fake_build)
    cfg = Config.load()
    cfg.data["memory"]["enabled"] = False
    store = SessionStore()
    alpha = Session.create("alpha")
    alpha.meta["runtime_controls"] = {
        "provider": "alpha-provider",
        "model": "alpha-model",
        "reasoning_effort": "xhigh",
        "reasoning_display": "off",
        "busy_mode": "steer",
    }
    beta = Session.create("beta")
    beta.meta["runtime_controls"] = {"model": "beta-model", "busy_mode": "queue"}
    store.save(alpha)
    store.save(beta)
    runner = SurfaceRunner(cfg, store=store, include_mcp=False)
    agent = runner.make_agent(session=beta, include_mcp=False)

    repl.handle_slash(f"/resume {alpha.id}", agent, runner=runner, store=store)

    assert agent.session.id == alpha.id
    assert agent.provider.name == "alpha-provider"
    assert agent.provider.model == "alpha-model"
    assert agent.reasoning == "xhigh"
    assert agent.config.get("display.reasoning") == "off"
    assert agent.config.get("gateway.busy_mode") == "steer"


def test_dashboard_session_detail_exposes_prompt_assembly():
    from aegis.dashboard import _dashboard_session_detail
    from aegis.session import Session, SessionStore
    from aegis.types import Message

    session = Session.create("prompt detail")
    session.messages = [Message.system("system prompt"), Message.user("hi")]
    session.meta["system_prompt_hash"] = "hash_prompt"
    session.meta["system_prompt_chars"] = len("system prompt")
    session.meta["system_prompt_tokens"] = 3
    session.meta["prompt_parts"] = [
        {"tier": "stable", "name": "identity", "hash": "h1", "chars": 5, "tokens": 1},
        {"tier": "context", "name": "project_rules", "hash": "h2", "chars": 7, "tokens": 2},
    ]
    session.meta["runtime_controls"] = {"model": "gpt-test", "busy_mode": "steer"}
    session.meta["last_context_references"] = {
        "count": 1,
        "injected_chars": 12,
        "warnings": [],
        "references": [{"raw": "@note.md", "kind": "file", "target": "note.md", "chars": 12}],
    }
    session.meta["context_references"] = [session.meta["last_context_references"]]
    SessionStore().save(session)

    detail = _dashboard_session_detail(session.id)

    assert detail["prompt"]["hash"] == "hash_prompt"
    assert detail["prompt"]["preview"] == "system prompt"
    assert detail["prompt"]["part_count"] == 2
    assert detail["prompt"]["tiers"]["stable"][0]["name"] == "identity"
    assert detail["prompt"]["tiers"]["context"][0]["name"] == "project_rules"
    assert detail["prompt"]["runtime_controls"]["busy_mode"] == "steer"
    assert detail["prompt"]["context_references"]["references"][0]["target"] == "note.md"
    assert detail["prompt"]["context_reference_history"][0]["count"] == 1


def test_dashboard_chat_response_includes_cockpit_breadcrumbs():
    from types import SimpleNamespace

    from aegis.dashboard import _dashboard_chat_response
    from aegis.session import Session

    class Runner:
        def run_prompt(self, prompt, **kwargs):
            assert kwargs["surface"] == "dashboard"
            assert kwargs["meta"]["surface_route"] == "/api/chat"
            on_event = kwargs["on_event"]
            on_event({"type": "iteration", "n": 1, "max": 3})
            on_event({"type": "tool_start", "name": "search", "args": {"query": "aegis"}})
            on_event({"type": "tool_result", "name": "search", "summary": "found", "preview": "ok"})
            return SimpleNamespace(
                text=f"reply:{prompt}",
                session=Session(id="sess_dashchat", title="dash chat"),
                trace_id="trace_dashchat",
                turn_id="turn_dashchat",
                run_id="run_dashchat",
            )

    data = _dashboard_chat_response({"message": "hello"}, Runner())

    assert data["reply"] == "reply:hello"
    assert data["session_id"] == "sess_dashchat"
    assert data["trace_id"] == "trace_dashchat"
    assert data["turn_id"] == "turn_dashchat"
    assert data["run_id"] == "run_dashchat"
    assert [e["type"] for e in data["events"]] == ["iteration", "tool_start", "tool_result"]
    assert data["events"][1]["name"] == "search"
    assert data["events"][1]["target"] == "aegis"


def test_dashboard_chat_stream_emits_progress_and_final():
    from types import SimpleNamespace

    from aegis.dashboard import _dashboard_chat_stream
    from aegis.session import Session

    class Runner:
        def run_prompt(self, prompt, **kwargs):
            assert kwargs["surface"] == "dashboard"
            assert kwargs["meta"]["surface_route"] == "/api/chat/stream"
            on_event = kwargs["on_event"]
            on_event({"type": "iteration", "n": 1, "max": 2})
            on_event({"type": "tool_start", "name": "grep", "args": {"query": "Hermes"}})
            return SimpleNamespace(
                text=f"stream:{prompt}",
                session=Session(id="sess_streamchat", title="stream chat"),
                trace_id="trace_streamchat",
                turn_id="turn_streamchat",
                run_id="run_streamchat",
            )

    sent = []
    final = _dashboard_chat_stream({"message": "hello"}, Runner(), sent.append)

    assert [row["type"] for row in sent] == ["start", "event", "event", "final"]
    assert sent[1]["event"]["summary"] == "1/2"
    assert sent[2]["event"]["name"] == "grep"
    assert sent[2]["event"]["target"] == "Hermes"
    assert final["reply"] == "stream:hello"
    assert final["session_id"] == "sess_streamchat"
    assert final["trace_id"] == "trace_streamchat"
    assert final["run_id"] == "run_streamchat"


def test_dashboard_chat_events_also_feed_live_activity():
    import queue
    from types import SimpleNamespace

    from aegis.dashboard import _dashboard_chat_stream
    from aegis.eventbus import BUS
    from aegis.session import Session

    class Runner:
        def run_prompt(self, prompt, **kwargs):
            on_event = kwargs["on_event"]
            on_event({"type": "iteration", "n": 1, "max": 2})
            on_event({"type": "tool_start", "name": "read_file", "args": {"path": "a.py"}})
            return SimpleNamespace(
                text=f"done:{prompt}",
                session=Session(id="sess_livechat", title="live chat"),
                trace_id="trace_livechat",
                turn_id="turn_livechat",
                run_id="run_livechat",
            )

    sub = BUS.subscribe()
    try:
        _dashboard_chat_stream(
            {"message": "hello", "session_id": "sess_livechat", "cwd": "/tmp/project"},
            Runner(),
            lambda _row: None,
        )
        events = []
        while True:
            try:
                events.append(sub.get_nowait())
            except queue.Empty:
                break
    finally:
        BUS.unsubscribe(sub)

    assert [e["type"] for e in events] == ["chat_start", "iteration", "tool_start", "chat_final"]
    assert events[0]["platform"] == "dashboard"
    assert events[0]["text"] == "hello"
    assert events[2]["name"] == "read_file"
    assert events[-1]["run_id"] == "run_livechat"
    assert events[-1]["cwd"] == "/tmp/project"


def test_dashboard_chat_accepts_cwd_and_records_project_worktree(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from aegis.agent.agent import Agent
    from aegis.config import Config
    from aegis.dashboard import _dashboard_chat_response
    from aegis.runs import RunStore
    from aegis.surface import SurfaceRunner
    from aegis.types import Message

    project = tmp_path / "project"
    project.mkdir()
    seen = {}

    class FakeAgent:
        stream = False

        def __init__(self, session, cwd):
            self.session = session
            self.cwd = cwd
            self.config = Config.load()
            self.provider = SimpleNamespace(name="fake", model="fake-model", api_mode="fake")
            self.budget = SimpleNamespace(usage=SimpleNamespace(input_tokens=0, output_tokens=0,
                                                                cache_read=0, cache_write=0))
            self.tool_context = SimpleNamespace(session=session)
            self._trace_context = {"trace_id": "trace_dash_cwd", "turn_id": "turn_dash_cwd"}

        def run(self, prompt, on_event=None):
            seen["prompt"] = prompt
            seen["cwd"] = self.cwd
            return Message.assistant(f"cwd:{self.cwd}")

    monkeypatch.setattr(
        Agent,
        "create",
        staticmethod(lambda config, session=None, cwd=None, **_kwargs: FakeAgent(session, cwd)),
    )

    cfg = Config.load()
    cfg.data["memory"]["enabled"] = False
    runner = SurfaceRunner(cfg, cwd=tmp_path, include_mcp=False)
    data = _dashboard_chat_response(
        {"message": "where am I?", "session_id": "dash:cwd", "cwd": str(project)},
        runner,
    )

    assert data["reply"] == f"cwd:{project}"
    assert data["cwd"] == str(project)
    assert seen["cwd"] == project
    run = RunStore().get(data["run_id"])
    assert run["surface"] == "dashboard"
    assert run["data"]["cwd"] == str(project)
    assert run["data"]["project"] == str(project)
    assert run["data"]["dashboard_cwd"] == str(project)


def test_dashboard_models_exposes_resolver_report():
    from aegis.config import Config
    from aegis.dashboard import _dashboard_models

    cfg = Config.load()
    cfg.data["model"] = {"provider": "localtest", "default": "local-model"}
    cfg.data["custom_providers"] = [
        {
            "name": "localtest",
            "base_url": "http://local.test/v1",
            "api_mode": "chat_completions",
            "context_length": 70_000,
        }
    ]
    cfg.data["fallback_providers"] = [{"provider": "ollama", "model": "llama3.1"}]
    cfg.data["routing"] = [{"match": "deploy", "provider": "localtest", "model": "local-routed"}]

    data = _dashboard_models(cfg)

    assert data["provider"] == "localtest"
    assert data["active"]["context_length"] == 70_000
    assert data["active"]["capabilities"]["tool_calls"] is True
    assert data["active"]["capabilities"]["images"] is False
    assert data["chain"][1]["name"] == "ollama"
    assert data["routing"][0]["provider"] == "localtest"
    assert data["routing"][0]["capabilities"]["tool_calls"] is True
    assert "localtest" in data["providers"]
    assert any(row["name"] == "localtest" and row["origin"] == "custom"
               for row in data["provider_catalog"])


def test_tui_retry_uses_shared_surface_runner(monkeypatch):
    from aegis.cli import tui
    from aegis.config import Config
    from aegis.providers import registry
    from aegis.runs import RunStore
    from aegis.session import Session, SessionStore
    from aegis.surface import SurfaceRunner
    from aegis.types import LLMResponse, Message
    from conftest import FakeProvider

    cfg = Config.load()
    cfg.data["memory"]["enabled"] = False
    provider = FakeProvider([LLMResponse(text="retried")])
    monkeypatch.setattr(registry, "build_provider", lambda *_args, **_kwargs: provider)
    store = SessionStore()
    runner = SurfaceRunner(cfg, store=store, include_mcp=False)
    session = Session.create("retry surface")
    session.messages = [Message.user("try this"), Message.assistant("old answer")]
    agent = runner.make_agent(session=session, include_mcp=False)

    result, output = tui._capture_slash(
        "/retry",
        agent,
        runner=runner,
        store=store,
        surface="tui",
        on_event=lambda _event: None,
    )

    assert result == ""
    assert "trace" in output
    assert provider.calls == 1
    assert agent.session.messages[-1].content == "retried"
    runs = RunStore().list(session_id=agent.session.id, limit=10)
    assert len([r for r in runs if r["surface"] == "tui"]) == 1


def test_tui_compress_uses_context_engine_hooks(monkeypatch, tmp_path):
    from aegis.agent import context_engine
    from aegis.cli import tui
    from aegis.config import Config
    from aegis.providers import registry
    from aegis.runs import RunStore
    from aegis.session import Session, SessionStore
    from aegis.surface import SurfaceRunner
    from aegis.tracing import TraceStore
    from aegis.types import Message
    from conftest import FakeProvider

    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    events = []

    class Engine:
        name = "terminal-test"

        def should_compress(self, messages, context_length, overhead_tokens=0):
            return False

        def compress(self, messages, provider, **kw):
            events.append(("compress", kw))
            return [messages[0], messages[-1]]

        def tools(self):
            return []

        def on_session_start(self, agent):
            events.append(("start", agent.session.id))

        def on_pre_compress(self, agent, session):
            events.append(("pre", session.id))

        def on_session_switch(self, agent, old_session, new_session, reason=""):
            events.append(("switch", old_session.id, new_session.id, reason))

    context_engine.register("terminal-test", Engine)
    cfg = Config.load()
    cfg.data["memory"]["enabled"] = False
    cfg.data.setdefault("agent", {})["context_engine"] = "terminal-test"
    cfg.data.setdefault("agent", {}).setdefault("compression", {})["split_sessions"] = True
    monkeypatch.setattr(registry, "build_provider", lambda *_args, **_kwargs: FakeProvider())

    store = SessionStore()
    runner = SurfaceRunner(cfg, store=store, include_mcp=False)
    session = Session.create("compress surface")
    session.messages = [
        Message.user("one"),
        Message.assistant("two"),
        Message.user("three"),
        Message.assistant("four"),
    ]
    store.save(session)
    agent = runner.make_agent(session=session, include_mcp=False)
    parent_id = agent.session.id

    result, output = tui._capture_slash(
        "/compress focus parity",
        agent,
        runner=runner,
        store=store,
        surface="tui",
        on_event=lambda event: events.append((event["type"], event)),
    )

    assert result == ""
    assert "context compressed" in output
    assert agent.session.id != parent_id
    assert store.load(parent_id).meta["end_reason"] == "manual_compression"
    assert store.load(agent.session.id).parent_id == parent_id
    assert any(e[0] == "pre" for e in events)
    assert any(e[0] == "switch" and e[3] == "manual_compression" for e in events)
    assert any(e[0] == "compress" and e[1]["focus"] == "parity" for e in events)
    assert any(e[0] == "compacted" for e in events)
    runs = [r for r in RunStore().list(session_id=agent.session.id, limit=10)
            if r["surface"] == "tui" and r["kind"] == "compaction"]
    assert len(runs) == 1
    assert agent.session.meta["last_run_id"] == runs[0]["id"]
    assert agent.session.meta["last_trace_id"] == runs[0]["trace_id"]
    trace = TraceStore.from_config(cfg).get_trace(runs[0]["trace_id"])
    assert trace["session_id"] == agent.session.id
    assert trace["compactions"] == 1


def test_aux_router_routes_and_summarizes(monkeypatch):
    from aegis.auxiliary import AuxRouter, router_for
    from aegis.types import LLMResponse

    class Provider:
        name = "aux"
        model = "small"

        def __init__(self):
            self.calls = []

        def complete(self, messages, **kwargs):
            self.calls.append(messages)
            return LLMResponse(text="summary")

    provider = Provider()

    def build_aux_provider(_config):
        return provider

    monkeypatch.setattr("aegis.providers.registry.build_aux_provider", build_aux_provider)
    router = AuxRouter(config=object(), fallback_provider=object())

    assert router.provider_for("compaction") is provider
    assert router.provider_for("compaction") is provider
    assert router.summarize_text("long text", purpose="session_summary") == "summary"
    assert provider.calls[-1][0].role == "system"

    class Agent:
        pass

    agent = Agent()
    agent.config = object()
    agent.provider = provider
    assert router_for(agent).provider_for("compaction") is provider
    assert router_for(agent) is agent._aux_router


def test_aux_provider_purpose_overrides_and_live_fallback(monkeypatch):
    from aegis.config import Config
    from aegis.providers import registry

    class Provider:
        def __init__(self, name, model):
            self.name = name
            self.model = model

    built = []

    def fake_build(config, model=None, name=None):
        built.append((name, model, config.get("model.context_length", 0)))
        return Provider(name or "main", model or "main-model")

    monkeypatch.setattr(registry, "build_provider", fake_build)
    cfg = Config.load()
    cfg.data["auxiliary"]["provider"] = "global"
    cfg.data["auxiliary"]["model"] = "global-small"
    cfg.data["auxiliary"]["compaction"] = {
        "provider": "compact",
        "model": "tiny",
        "context_length": 12345,
    }

    provider = registry.build_aux_provider(cfg, purpose="compaction")
    assert (provider.name, provider.model) == ("compact", "tiny")
    assert built[-1] == ("compact", "tiny", 12345)

    fallback = Provider("routed-main", "routed-model")
    cfg.data["auxiliary"] = {"provider": "", "model": ""}
    assert registry.build_aux_provider(cfg, purpose="session_summary", fallback_provider=fallback) is fallback


def test_context_references_shared_across_surfaces(tmp_path):
    from aegis.context_refs import expand_prompt, expand_reference_result
    from aegis.types import Message

    note = tmp_path / "notes.md"
    note.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")

    result = expand_reference_result("review @file:notes.md:2-3", tmp_path)
    assert result.expanded is True
    assert "2: beta" in result.text and "3: gamma" in result.text
    assert result.references[0].kind == "file"

    message = expand_prompt(Message.user("review @notes.md", images=["data:image/png;base64,abc"]), tmp_path)
    assert isinstance(message, Message)
    assert "alpha" in message.content
    assert message.images == ["data:image/png;base64,abc"]

    refused = expand_reference_result("@file:~/.ssh/id_rsa", tmp_path)
    assert "refused" in refused.text


def test_context_references_can_attach_mcp_resource(tmp_path):
    import sys

    from aegis.config import Config
    from aegis.context_refs import expand_reference_result

    server = tmp_path / "mcp_srv.py"
    server.write_text(
        "import json,sys\n"
        "def s(o): sys.stdout.write(json.dumps(o)+chr(10)); sys.stdout.flush()\n"
        "for line in sys.stdin:\n"
        "    line=line.strip()\n"
        "    if not line: continue\n"
        "    m=json.loads(line); mid=m.get('id'); meth=m.get('method')\n"
        "    if meth=='initialize': s({'jsonrpc':'2.0','id':mid,'result':{'protocolVersion':'2025-06-18','capabilities':{},'serverInfo':{'name':'t','version':'1'}}})\n"
        "    elif meth=='resources/read': s({'jsonrpc':'2.0','id':mid,'result':{'contents':[{'uri':m['params']['uri'],'text':'mcp attached context'}]}})\n",
        encoding="utf-8",
    )
    cfg = Config.load()
    cfg.data.setdefault("mcp", {})["servers"] = {
        "t": {"command": sys.executable, "args": [str(server)]}
    }

    result = expand_reference_result("review @mcp:t:note://a", tmp_path, config=cfg)

    assert "<mcp-resource" in result.text
    assert "mcp attached context" in result.text
    assert result.references[0].kind == "mcp"
    assert result.references[0].target == "t:note://a"


def test_mcp_tools_command_lists_resources_and_prompts(tmp_path, capsys):
    import sys
    from argparse import Namespace

    from aegis.cli.main import cmd_mcp
    from aegis.config import Config

    server = tmp_path / "mcp_srv.py"
    server.write_text(
        "import json,sys\n"
        "def s(o): sys.stdout.write(json.dumps(o)+chr(10)); sys.stdout.flush()\n"
        "for line in sys.stdin:\n"
        "    line=line.strip()\n"
        "    if not line: continue\n"
        "    m=json.loads(line); mid=m.get('id'); meth=m.get('method')\n"
        "    if meth=='initialize': s({'jsonrpc':'2.0','id':mid,'result':{'protocolVersion':'2025-06-18','capabilities':{},'serverInfo':{'name':'t','version':'1'}}})\n"
        "    elif meth=='tools/list': s({'jsonrpc':'2.0','id':mid,'result':{'tools':[{'name':'ping','description':'Ping tool','inputSchema':{'type':'object','properties':{}}}]}})\n"
        "    elif meth=='resources/list': s({'jsonrpc':'2.0','id':mid,'result':{'resources':[{'uri':'note://a','name':'Note A'}]}})\n"
        "    elif meth=='prompts/list': s({'jsonrpc':'2.0','id':mid,'result':{'prompts':[{'name':'review','description':'Review prompt'}]}})\n",
        encoding="utf-8",
    )
    cfg = Config.load()
    cfg.data.setdefault("mcp", {})["servers"] = {
        "t": {"command": sys.executable, "args": [str(server)]}
    }

    assert cmd_mcp(Namespace(action="tools", name="t"), cfg) == 0

    out = capsys.readouterr().out
    assert "ping" in out
    assert "resources:" in out and "note://a" in out
    assert "prompts:" in out and "review" in out


def test_mcp_server_uses_full_tool_context_and_visible_inventory(monkeypatch, tmp_path):
    import io
    import json

    from aegis.config import Config
    from aegis.mcp.server import run_mcp_server
    from aegis.tools.base import Tool, ToolResult
    from aegis.tools.registry import ToolRegistry

    seen = {}

    class ContextTool(Tool):
        name = "context_probe"
        description = "Inspect MCP server tool context."
        parameters = {"type": "object", "properties": {}}
        toolset = "core"

        def run(self, args, ctx):
            seen["cwd"] = ctx.cwd
            seen["session_id"] = ctx.session.id
            seen["has_memory"] = ctx.memory is not None
            seen["has_skills"] = ctx.skills is not None
            seen["agent_session_id"] = ctx.agent.session.id
            return ToolResult.ok("context ok")

    class HiddenTool(ContextTool):
        name = "hidden_tool"
        toolset = "browser"

    reg = ToolRegistry()
    reg.register(ContextTool())
    reg.register(HiddenTool())

    class Perms:
        def __init__(self, config):
            self.config = config

        def authorize(self, tool, args, ctx):
            return True, ""

    monkeypatch.setattr("aegis.tools.registry.default_registry", lambda: reg)
    monkeypatch.setattr("aegis.tools.permissions.PermissionEngine", Perms)
    monkeypatch.chdir(tmp_path)
    cfg = Config.load()
    cfg.data.setdefault("tools", {})["toolsets"] = ["core"]

    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "context_probe", "arguments": {}}},
    ]
    monkeypatch.setattr("sys.stdin", io.StringIO("\n".join(json.dumps(m) for m in messages) + "\n"))
    out = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)

    run_mcp_server(cfg)

    rows = [json.loads(line) for line in out.getvalue().splitlines()]
    listed = rows[0]["result"]["tools"]
    assert [t["name"] for t in listed] == ["context_probe"]
    assert rows[1]["result"]["content"][0]["text"] == "context ok"
    assert seen == {
        "cwd": tmp_path,
        "session_id": "mcp:stdio",
        "has_memory": True,
        "has_skills": True,
        "agent_session_id": "mcp:stdio",
    }


def test_dashboard_mcp_catalog_live_inventory(tmp_path):
    import sys

    from aegis.config import Config
    from aegis.dashboard import _dashboard_mcp_catalog

    server = tmp_path / "mcp_srv.py"
    server.write_text(
        "import json,sys\n"
        "def s(o): sys.stdout.write(json.dumps(o)+chr(10)); sys.stdout.flush()\n"
        "for line in sys.stdin:\n"
        "    line=line.strip()\n"
        "    if not line: continue\n"
        "    m=json.loads(line); mid=m.get('id'); meth=m.get('method')\n"
        "    if meth=='initialize': s({'jsonrpc':'2.0','id':mid,'result':{'protocolVersion':'2025-06-18','capabilities':{},'serverInfo':{'name':'t','version':'1'}}})\n"
        "    elif meth=='tools/list': s({'jsonrpc':'2.0','id':mid,'result':{'tools':[{'name':'ping','description':'Ping','inputSchema':{'type':'object','properties':{}}}]}})\n"
        "    elif meth=='resources/list': s({'jsonrpc':'2.0','id':mid,'result':{'resources':[{'uri':'note://a'}]}})\n"
        "    elif meth=='prompts/list': s({'jsonrpc':'2.0','id':mid,'result':{'prompts':[{'name':'review'}]}})\n",
        encoding="utf-8",
    )
    cfg = Config.load()
    cfg.data.setdefault("mcp", {})["servers"] = {
        "t": {"command": sys.executable, "args": [str(server)]}
    }

    data = _dashboard_mcp_catalog(cfg, live=True)
    server_info = data["servers"][0]
    assert server_info["status"] == "ok"
    assert server_info["tools"][0]["name"] == "ping"
    assert server_info["resources"][0]["uri"] == "note://a"
    assert server_info["prompts"][0]["name"] == "review"


def test_repl_run_once_uses_shared_surface_runner(tmp_path, monkeypatch):
    from aegis.cli import repl
    from aegis.config import Config
    from aegis.providers import registry
    from aegis.runs import RunStore
    from aegis.session import Session, SessionStore
    from aegis.tracing import TraceStore
    from aegis.types import LLMResponse
    from conftest import FakeProvider

    note = tmp_path / "note.md"
    note.write_text("shared terminal surface\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    cfg = Config.load()
    cfg.data["memory"]["enabled"] = False
    provider = FakeProvider([LLMResponse(text="cli ok")])
    monkeypatch.setattr(registry, "build_provider", lambda *_args, **_kwargs: provider)

    store = SessionStore()
    session = Session.create("cli surface")
    assert repl.run_once(cfg, "inspect @file:note.md", session=session, store=store) == "cli ok"

    saved = store.load(session.id)
    assert saved is not None
    assert saved.meta["surface"] == "cli"
    assert saved.meta["last_context_references"]["references"][0]["target"] == "note.md"
    assert saved.meta["trace_id"].startswith("trace_")
    assert saved.meta["system_prompt_hash"]
    assert saved.meta["prompt_parts"]

    runs = RunStore().list(limit=5)
    run = next(row for row in runs if row["session_id"] == session.id)
    assert run["surface"] == "cli"
    assert run["trace_id"] == saved.meta["trace_id"]
    trace = TraceStore.from_config(cfg).get_trace(saved.meta["trace_id"])
    assert trace is not None
    turn = next(span for span in trace["spans"] if span["kind"] == "turn")
    assert turn["data"]["prompt"]["system_prompt_hash"] == saved.meta["system_prompt_hash"]


def test_surface_runner_provider_metadata_includes_run_id(tmp_path):
    from aegis.agent.agent import Agent
    from aegis.config import Config
    from aegis.session import Session, SessionStore
    from aegis.surface import SurfaceRunner
    from aegis.types import LLMResponse

    class MetadataProvider:
        name = "fake"
        model = "fake-model"
        api_mode = "responses"
        context_length = 200_000

        def __init__(self):
            self.metadata = {}

        def describe(self):
            return "fake"

        def complete(self, messages, tools=None, stream=False, on_delta=None, metadata=None):
            self.metadata = dict(metadata or {})
            return LLMResponse(text="ok")

    cfg = Config.load()
    cfg.data["memory"]["enabled"] = False
    store = SessionStore()
    session = Session.create("metadata run")
    provider = MetadataProvider()
    agent = Agent(config=cfg, provider=provider, session=session, cwd=tmp_path, store=store)

    result = SurfaceRunner(cfg, store=store, include_mcp=False).run_prompt(
        "hello",
        session=session,
        agent=agent,
        surface="cli",
    )

    assert provider.metadata["session_id"] == session.id
    assert provider.metadata["trace_id"] == result.trace_id
    assert provider.metadata["turn_id"] == result.turn_id
    assert provider.metadata["run_id"] == result.run_id


def test_renderer_reasoning_display_modes(monkeypatch):
    import contextlib
    import io
    from aegis.cli import repl
    from aegis.config import Config

    monkeypatch.setattr(repl, "_console", None)
    cfg = Config.load()
    cfg.data.setdefault("display", {})["reasoning"] = "summary"
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        r = repl.Renderer(cfg)
        r({"type": "reasoning_delta", "text": "private chain"})
        r({"type": "assistant_message", "text": "answer", "tool_calls": []})
    text = out.getvalue()
    assert "thinking" in text and "private chain" not in text and "answer" in text

    cfg.data["display"]["reasoning"] = "off"
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        r = repl.Renderer(cfg)
        r({"type": "reasoning_delta", "text": "hidden"})
        r({"type": "assistant_message", "text": "answer", "tool_calls": []})
    assert "hidden" not in out.getvalue()


def test_manifest_plugin_enable_disable_and_remove(tmp_path):
    from aegis.config import Config
    from aegis import plugins

    cfg = Config.load()
    pkg = tmp_path / "hello_pkg"
    pkg.mkdir()
    (pkg / "plugin.json").write_text(
        '{"name":"hello","version":"1.0.0","description":"hello plugin","entrypoint":"main.py"}',
        encoding="utf-8",
    )
    (pkg / "main.py").write_text(
        "def register(api):\n"
        "    class T:\n"
        "        name='hello_tool'\n"
        "    api.register_tool(T())\n",
        encoding="utf-8",
    )

    assert plugins.install(str(pkg), cfg) == "hello"
    manifests = plugins.list_manifests(cfg)
    assert manifests[0].name == "hello" and manifests[0].enabled is True
    assert [t.name for t in plugins.load_plugins(config=cfg).tools] == ["hello_tool"]

    assert plugins.disable("hello", cfg) is True
    assert plugins.load_plugins(config=cfg).tools == []
    assert plugins.enable("hello", cfg) is True
    assert [t.name for t in plugins.load_plugins(config=cfg).tools] == ["hello_tool"]
    assert plugins.remove("hello", cfg) is True
    assert plugins.list_manifests(cfg) == []


def test_plugin_enable_does_not_allowlist_unrelated_plugins():
    from aegis import config as cfg_paths
    from aegis import plugins
    from aegis.config import Config

    cfg = Config.load()
    base = cfg_paths.sub("plugins")
    base.mkdir(parents=True, exist_ok=True)
    (base / "one.py").write_text(
        "def register(api):\n"
        "    class T:\n"
        "        name='one_tool'\n"
        "    api.register_tool(T())\n",
        encoding="utf-8",
    )
    (base / "two.py").write_text(
        "def register(api):\n"
        "    class T:\n"
        "        name='two_tool'\n"
        "    api.register_tool(T())\n",
        encoding="utf-8",
    )

    assert plugins.enable("one", cfg) is True
    assert {t.name for t in plugins.load_plugins(config=cfg).tools} == {"one_tool", "two_tool"}
    assert plugins.disable("two", cfg) is True
    assert {t.name for t in plugins.load_plugins(config=cfg).tools} == {"one_tool"}


def test_plugin_provider_bootstraps_before_build_provider():
    from aegis import config as cfg_paths
    from aegis.config import Config
    from aegis.providers.registry import build_provider, list_providers

    cfg = Config.load()
    base = cfg_paths.sub("plugins")
    base.mkdir(parents=True, exist_ok=True)
    (base / "provider_plugin.py").write_text(
        "from aegis.providers.registry import ProviderSpec\n"
        "from aegis.providers.base import ApiMode\n"
        "def register(api):\n"
        "    api.register_provider(ProviderSpec(\n"
        "        name='plugprov', api_mode=ApiMode.CHAT_COMPLETIONS,\n"
        "        base_url='http://plug.local/v1', default_model='plug-model',\n"
        "        context_length=64000, auth_scheme='none'))\n",
        encoding="utf-8",
    )
    cfg.data.setdefault("model", {})["provider"] = "plugprov"
    cfg.data["model"]["default"] = "plug-model"

    provider = build_provider(cfg)

    assert "plugprov" in list_providers()
    assert provider.name == "plugprov"
    assert provider.base_url == "http://plug.local/v1"
    assert provider.model == "plug-model"


def test_disabling_plugin_provider_removes_runtime_registration():
    from aegis import config as cfg_paths
    from aegis import plugins
    from aegis.config import Config
    from aegis.providers.registry import build_provider

    cfg = Config.load()
    base = cfg_paths.sub("plugins")
    base.mkdir(parents=True, exist_ok=True)
    (base / "provider_plugin.py").write_text(
        "from aegis.providers.registry import ProviderSpec\n"
        "from aegis.providers.base import ApiMode\n"
        "def register(api):\n"
        "    api.register_provider(ProviderSpec('toggleprov', ApiMode.CHAT_COMPLETIONS,\n"
        "        'http://toggle.local/v1', 'toggle-model', 64000, auth_scheme='none'))\n",
        encoding="utf-8",
    )
    cfg.data.setdefault("model", {})["provider"] = "toggleprov"
    cfg.data["model"]["default"] = "toggle-model"
    assert build_provider(cfg).name == "toggleprov"

    assert plugins.disable("provider_plugin", cfg) is True

    try:
        build_provider(cfg)
    except ValueError as exc:
        assert "Unknown provider 'toggleprov'" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("disabled plugin provider should not remain registered")


def test_plugin_gateway_channel_builds_like_builtin():
    from aegis import config as cfg_paths
    from aegis.gateway.channels import build_adapter

    base = cfg_paths.sub("plugins")
    base.mkdir(parents=True, exist_ok=True)
    (base / "channel_plugin.py").write_text(
        "from aegis.gateway.base import BasePlatformAdapter, MessageEvent\n"
        "class EchoChannel(BasePlatformAdapter):\n"
        "    name='echo'\n"
        "    def __init__(self): self.sent=[]\n"
        "    def start(self, dispatch):\n"
        "        self.send('local', dispatch(MessageEvent(platform='echo', chat_id='local', text='hi')))\n"
        "    def send(self, chat_id, text): self.sent.append((chat_id, text))\n"
        "def register(api): api.register_channel('echo', EchoChannel)\n",
        encoding="utf-8",
    )

    adapter = build_adapter("echo")
    adapter.start(lambda ev: 'reply:' + ev.text)

    assert adapter.name == "echo"
    assert adapter.sent == [("local", "reply:hi")]


def test_plugin_hooks_are_idempotent_across_loads():
    from aegis import config as cfg_paths
    from aegis import plugins

    base = cfg_paths.sub("plugins")
    base.mkdir(parents=True, exist_ok=True)
    log = cfg_paths.sub("hook.log")
    (base / "hook_plugin.py").write_text(
        "from pathlib import Path\n"
        f"LOG = Path({str(log)!r})\n"
        "def register(api):\n"
        "    api.register_hook('on_session_start', lambda agent: LOG.write_text(LOG.read_text() + 'x' if LOG.exists() else 'x'))\n",
        encoding="utf-8",
    )
    plugins._HOOKS.clear()

    plugins.load_plugins(config=None)
    plugins.load_plugins(config=None)
    plugins.fire_hook("on_session_start", object())

    assert log.read_text(encoding="utf-8") == "x"
    plugins._HOOKS.clear()


def test_mcp_catalog_install_and_tool_filter():
    from aegis.config import Config
    from aegis.mcp.client import _filter_tools, catalog, install_from_catalog

    cfg = Config.load()
    cfg.data.setdefault("mcp", {})["catalog"] = [
        {"name": "fs", "command": "npx", "args": ["server-fs"], "description": "files",
         "tool_filter": {"include": ["read"], "exclude": ["write"]}},
    ]

    assert catalog(cfg)[0]["name"] == "fs"
    spec = install_from_catalog(cfg, "fs")
    assert spec["command"] == "npx"
    assert cfg.get("mcp.servers")["fs"]["tool_filter"]["include"] == ["read"]

    tools = [{"name": "read"}, {"name": "write"}, {"name": "list"}]
    assert _filter_tools(tools, {"include": ["read", "write"], "exclude": ["write"]}) == [{"name": "read"}]


def test_surface_runner_standardizes_agent_factory(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from aegis.agent.agent import Agent
    from aegis.config import Config
    from aegis.runs import RunStore
    from aegis.session import SessionStore
    from aegis.surface import SurfaceRunner
    from aegis.types import Message

    seen = {}
    created = []

    class FakeAgent:
        def __init__(self, session):
            self.session = session
            self.tool_context = SimpleNamespace(session=session)
            self.prompts = []
            self.platforms = []
            self.chat_ids = []

        def run(self, prompt, on_event=None):
            self.prompts.append(prompt)
            self.platforms.append(getattr(self, "platform", None))
            self.chat_ids.append(getattr(self, "chat_id", None))
            seen["prompt"] = prompt
            self._trace_context = {"trace_id": "trace_surface", "turn_id": "turn_surface"}
            return Message.assistant(f"surface ok {len(self.prompts)}")

    def create(_config, session=None):
        seen["session_id"] = session.id
        agent = FakeAgent(session)
        created.append(agent)
        return agent

    monkeypatch.setattr(Agent, "create", staticmethod(create))
    runner = SurfaceRunner(Config.load(), cwd=tmp_path, include_mcp=True)

    result = runner.run_prompt(
        "hello",
        session_id="surface:test",
        surface="serve",
        meta={"request_id": "req_1"},
        platform="telegram",
        chat_id="42",
    )
    again = runner.run_prompt("again", session_id="surface:test", surface="serve")

    assert result.text == "surface ok 1"
    assert again.text == "surface ok 2"
    assert result.run_id.startswith("run_")
    stored = RunStore().get(result.run_id)
    assert stored["surface"] == "serve"
    assert stored["session_id"] == "surface:test"
    assert stored["status"] == "ok"
    assert stored["data"]["cwd"] == str(tmp_path)
    assert stored["data"]["project"] == str(tmp_path)
    assert len(created) == 1
    assert result.session.id == "surface:test"
    assert result.session.meta["surface"] == "serve"
    assert result.session.meta["request_id"] == "req_1"
    assert result.session.meta["last_run_id"] == result.run_id
    saved = SessionStore().load("surface:test")
    assert saved is not None
    assert saved.meta["last_run_id"] == again.run_id
    assert result.session.meta["last_trace_id"] == "trace_surface"
    assert result.session.meta["last_turn_id"] == "turn_surface"
    assert seen == {"prompt": "again", "session_id": "surface:test"}
    assert created[0].prompts == ["hello", "again"]
    assert created[0].platforms == ["telegram", None]
    assert created[0].chat_ids == ["42", None]


def test_surface_runner_expands_prompt_context_references(monkeypatch, tmp_path):
    from aegis.agent.agent import Agent
    from aegis.config import Config
    from aegis.surface import SurfaceRunner
    from aegis.types import Message

    (tmp_path / "brief.txt").write_text("runtime context", encoding="utf-8")
    seen = {}

    class FakeAgent:
        def __init__(self, session):
            self.session = session
            self.tool_context = type("Ctx", (), {"session": session})()

        def run(self, prompt, on_event=None):
            seen["prompt"] = prompt
            return Message.assistant("ok")

    monkeypatch.setattr(Agent, "create", staticmethod(lambda _config, session=None, **_kw: FakeAgent(session)))

    runner = SurfaceRunner(Config.load(), cwd=tmp_path, include_mcp=False)
    result = runner.run_prompt("read @brief.txt", surface="serve")

    assert "runtime context" in seen["prompt"]
    assert result.session.meta["last_context_references"]["count"] == 1
    assert result.session.meta["last_context_references"]["references"][0]["kind"] == "file"


def test_openai_server_conversion_preserves_context_and_images():
    from aegis.server import _convert

    history, last = _convert([
        {"role": "system", "content": "answer as the repo agent"},
        {"role": "developer", "content": [{"type": "text", "text": "prefer concise replies"}]},
        {"role": "user", "content": "earlier prompt"},
        {"role": "assistant", "content": "earlier answer"},
        {"role": "user", "content": [
            {"type": "text", "text": "inspect this"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
        ]},
    ])

    assert "<system_instructions>" in history[0].content
    assert "<developer_instructions>" in history[1].content
    assert [m.role for m in history[-2:]] == ["user", "assistant"]
    assert last.role == "user"
    assert last.content == "inspect this"
    assert last.images == ["data:image/png;base64,abc"]


def test_openai_server_conversion_pops_last_duplicate_user_message():
    from aegis.server import _convert

    history, last = _convert([
        {"role": "user", "content": "same"},
        {"role": "assistant", "content": "middle"},
        {"role": "user", "content": "same"},
    ])

    assert last.content == "same"
    assert [m.role for m in history] == ["user", "assistant"]
    assert history[0].content == "same"


def test_agent_state_tool_sessions_traces_evals_and_background():
    import json
    from types import SimpleNamespace

    from aegis.config import Config
    from aegis.evals import EvalStore
    from aegis.runs import RunStore
    from aegis.session import Session, SessionStore
    from aegis.tools.base import ToolContext
    from aegis.tools.state import AgentStateTool
    from aegis.tracing import TraceStore
    from aegis.types import Message

    cfg = Config.load()
    session = Session.create("state surface")
    session.messages = [Message.user("hi"), Message.assistant("hello")]
    SessionStore().save(session)
    TraceStore.from_config(cfg).write_trace(
        [{"span_id": "span_state", "kind": "turn", "status": "ok"}],
        trace_id="trace_state",
        session_id=session.id,
    )
    EvalStore.from_config(cfg).add_run(
        "state-suite",
        [{"case": "ok", "passed": True, "score": 1.0, "grades": []}],
    )
    run = RunStore().start(
        surface="dashboard",
        kind="dashboard",
        title="state run",
        session_id=session.id,
        trace_id="trace_state",
        prompt="hi",
    )
    RunStore().finish(run["id"], result="hello", trace_id="trace_state")
    agent = SimpleNamespace(
        provider=SimpleNamespace(name="fake", model="fake-model"),
        tools_used=3,
        _surface_run_id=run["id"],
        _trace_context={"trace_id": "trace_state", "turn_id": "turn_state"},
    )
    ctx = ToolContext(config=cfg, session=session, agent=agent)
    tool = AgentStateTool()

    current = json.loads(tool.run({"action": "current"}, ctx).content)
    assert current["session_id"] == session.id
    assert current["trace_id"] == "trace_state"
    assert current["run_id"] == run["id"]
    session.meta["last_run_id"] = run["id"]
    session.meta["last_trace_id"] = "trace_state"
    fallback_current = json.loads(tool.run(
        {"action": "current"},
        ToolContext(config=cfg, session=session, agent=SimpleNamespace(provider=None, tools_used=0)),
    ).content)
    assert fallback_current["run_id"] == run["id"]
    assert fallback_current["trace_id"] == "trace_state"

    session_detail = json.loads(tool.run({"action": "session", "id": session.id}, ctx).content)
    assert session_detail["runs"][0]["id"] == run["id"]
    assert session_detail["traces"][0]["trace_id"] == "trace_state"
    assert session_detail["links"]["run_ids"] == [run["id"]]
    assert session_detail["links"]["latest_trace_id"] == "trace_state"

    branch = json.loads(tool.run({"action": "branch", "title": "state branch"}, ctx).content)
    assert branch["parent_id"] == session.id
    assert SessionStore().load(branch["session_id"]).title == "state branch"

    trace = json.loads(tool.run({"action": "trace", "id": "trace_state"}, ctx).content)
    assert trace["trace_id"] == "trace_state"
    runs = json.loads(tool.run({"action": "runs", "id": session.id}, ctx).content)
    assert runs[0]["id"] == run["id"]
    assert json.loads(tool.run({"action": "run", "id": run["id"]}, ctx).content)["surface"] == "dashboard"
    assert json.loads(tool.run({"action": "traces", "id": session.id}, ctx).content)[0]["trace_id"] == "trace_state"
    assert json.loads(tool.run({"action": "evals"}, ctx).content)[0]["suite"] == "state-suite"
    assert isinstance(json.loads(tool.run({"action": "background"}, ctx).content), list)


def test_dashboard_agents_include_active_runs():
    from aegis.config import Config
    from aegis.dashboard import _dashboard_agent_detail, _dashboard_agents
    from aegis.runs import RunStore
    from aegis.session import Session, SessionStore
    from aegis.tracing import TraceStore
    from aegis.types import Message

    cfg = Config.load()
    session = Session.create("active cockpit")
    session.messages = [Message.user("ship active monitor")]
    SessionStore().save(session)
    TraceStore.from_config(cfg).start_span(
        trace_id="trace_active_run",
        session_id=session.id,
        turn_id="turn_active",
        kind="turn",
    )
    run = RunStore().start(
        surface="dashboard",
        kind="dashboard",
        title="active cockpit run",
        session_id=session.id,
        prompt="ship active monitor",
        data={"provider": "fake", "model": "fake-model"},
    )

    agents = _dashboard_agents(cfg)
    active = next(a for a in agents["active_runs"] if a["run_id"] == run["id"])

    assert active["kind"] == "active_run"
    assert active["status"] == "running"
    assert active["session_id"] == session.id
    assert active["trace_id"] == "trace_active_run"
    assert any(a["id"] == run["id"] for a in agents["agents"])
    primary = next(a for a in agents["agents"] if a["id"] == "primary")
    assert primary["status"] == "running"
    assert primary["active_runs"] >= 1

    detail = _dashboard_agent_detail({"id": [run["id"]]}, cfg)
    assert detail["found"] is True
    assert detail["agent"]["kind"] == "active_run"
    assert detail["run"]["id"] == run["id"]
    assert detail["trace"]["trace"]["id"] == "trace_active_run"
    assert detail["messages"][0]["content"] == "ship active monitor"


def test_dashboard_agents_page_has_active_runs_section():
    from importlib import resources

    html = (resources.files("aegis") / "static" / "dashboard.html").read_text(encoding="utf-8")

    assert "Active runs" in html
    assert "data-runlink" in html


def test_dashboard_run_detail_uses_configured_trace_path(tmp_path):
    from aegis.config import Config
    from aegis.dashboard import _dashboard_run_detail
    from aegis.runs import RunStore
    from aegis.session import Session, SessionStore
    from aegis.tracing import TraceStore
    from aegis.types import Message

    cfg = Config.load()
    cfg.data.setdefault("tracing", {})["path"] = str(tmp_path / "custom-traces.db")
    session = Session.create("custom trace path")
    session.messages = [Message.user("hi"), Message.assistant("there")]
    SessionStore().save(session)
    TraceStore.from_config(cfg).write_trace(
        [{"span_id": "turn", "kind": "turn", "status": "ok"}],
        trace_id="trace_custom",
        session_id=session.id,
    )
    run = RunStore().start(
        surface="tui",
        kind="tui",
        title="custom trace run",
        session_id=session.id,
        trace_id="trace_custom",
        prompt="hi",
    )
    RunStore().finish(run["id"], trace_id="trace_custom", result="there")

    detail = _dashboard_run_detail({"id": [run["id"]]}, cfg)

    assert detail["found"] is True
    assert detail["run"]["trace_id"] == "trace_custom"
    assert detail["trace"]["found"] is True
    assert detail["trace"]["trace"]["id"] == "trace_custom"


def test_dashboard_traces_filters_runtime_store(tmp_path):
    from aegis.config import Config
    from aegis.dashboard import _dashboard_traces
    from aegis.tracing import TraceStore

    cfg = Config.load()
    cfg.data.setdefault("tracing", {})["path"] = str(tmp_path / "filtered-traces.db")
    store = TraceStore.from_config(cfg)
    store.write_trace(
        [{"span_id": "turn_alpha", "kind": "turn", "status": "ok"}],
        trace_id="trace_alpha",
        session_id="sess_alpha",
    )
    store.write_trace(
        [{"span_id": "turn_beta", "kind": "turn", "status": "error"}],
        trace_id="trace_beta",
        session_id="sess_beta",
    )

    data = _dashboard_traces(
        {"limit": ["20"], "session_id": ["sess_alpha"], "status": ["ok"], "source": ["trace_store"]},
        cfg,
    )

    assert data["available"] is True
    assert data["summary"]["filters"]["session_id"] == "sess_alpha"
    assert [row["id"] for row in data["traces"]] == ["trace_alpha"]
