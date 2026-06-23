"""The terminal tool-trail rendering: per-tool verbs/previews, failure detection,
and the compaction/skill nudges. We drive the Renderer with events
and assert it produces the right text without raising."""

from aegis.cli import repl
from aegis.cli.repl import Renderer, _oneline, _result_is_failure, _tool_preview, _tool_verb


def test_tool_verb_maps_and_falls_back():
    assert _tool_verb("read_file") == "read"
    assert _tool_verb("web_fetch") == "fetch"
    assert _tool_verb("bash") == "run"
    # An unmapped tool name is truncated, never crashes.
    assert _tool_verb("totally_unknown_tool")[:9]


def test_tool_preview_is_one_line_and_bounded():
    long = {"command": "echo hi\n\n   && ls   -la /tmp/" + "x" * 200}
    out = _tool_preview("bash", long)
    assert "\n" not in out and len(out) <= 72
    assert _tool_preview("memory", {"action": "add", "content": "remember this"}).startswith("add")
    assert "example.com" in _tool_preview("web_fetch", {"url": "https://example.com/page"})


def test_result_failure_detection():
    assert _result_is_failure("Error: boom") is True
    assert _result_is_failure("command failed with exit 1") is True
    assert _result_is_failure("ran ok, exit code 0") is False
    assert _result_is_failure("read 240 lines") is False


def test_oneline_collapses_and_truncates():
    assert _oneline("a\n  b\t c") == "a b c"
    assert _oneline("x" * 100, 10).endswith("…") and len(_oneline("x" * 100, 10)) == 10


def test_renderer_handles_full_event_stream(capsys, monkeypatch):
    # Force the plain-print path so output is captured deterministically.
    monkeypatch.setattr(repl, "_console", None)
    monkeypatch.setenv("AEGIS_ASCII", "1")
    monkeypatch.delenv("AEGIS_UNICODE", raising=False)
    r = Renderer(None)
    for e in (
        {"type": "tool_start", "name": "read_file", "args": {"path": "main.py"}},
        {"type": "tool_result", "name": "read_file", "summary": "read 12 lines", "duration_ms": 30},
        {"type": "tool_start", "name": "bash", "args": {"command": "pytest -q"}},
        {"type": "tool_result", "name": "bash", "summary": "Error: exit 1", "duration_ms": 900},
        {"type": "compacting"},
        {"type": "compacted", "tokens_before": 100_000, "tokens_after": 25_000},
    ):
        r(e)
    out = capsys.readouterr().out
    assert "read" in out and "ok read 12 lines" in out
    assert "x Error: exit 1" in out                 # non-is_error failure still flagged
    assert "freed" in out and "75%" in out           # compaction delta line

    monkeypatch.delenv("AEGIS_ASCII", raising=False)
    monkeypatch.setenv("AEGIS_UNICODE", "1")
    r({"type": "tool_result", "name": "read_file", "summary": "read 12 lines", "duration_ms": 30})
    assert "✓ read 12 lines" in capsys.readouterr().out


def test_renderer_timestamps_and_detailed_tool_progress(capsys, monkeypatch):
    from aegis.config import Config

    monkeypatch.setattr(repl, "_console", None)
    monkeypatch.setenv("AEGIS_ASCII", "1")
    monkeypatch.setattr(repl.time, "strftime", lambda _fmt: "12:34:56")
    cfg = Config.load()
    cfg.data.setdefault("display", {})["timestamps"] = True
    cfg.data["display"]["tool_progress"] = "detailed"

    r = Renderer(cfg)
    r({
        "type": "tool_result",
        "name": "bash",
        "summary": "ran ok",
        "duration_ms": 10,
        "preview": "pytest -q",
        "classification": "success",
        "artifact_ref": "logs/run.txt",
    })

    out = capsys.readouterr().out
    assert "[12:34:56]" in out
    assert "ok ran ok" in out
    assert "preview pytest -q" in out
    assert "classification success" in out
    assert "artifact ref logs/run.txt" in out


def test_renderer_turn_timeline_summarizes_long_runs(capsys, monkeypatch):
    monkeypatch.setattr(repl, "_console", None)
    monkeypatch.setenv("AEGIS_ASCII", "1")
    r = Renderer(None)
    for event in (
        {"type": "terminal_turn_start", "session_id": "sess_123456789", "chars": 42},
        {"type": "iteration", "n": 1, "max": 12},
        {"type": "provider_start", "provider": "openai", "model": "gpt-test"},
        {"type": "provider_end", "status": "ok", "duration_ms": 200},
        {"type": "tool_start", "name": "bash", "args": {"command": "pytest -q"}},
        {"type": "tool_result", "name": "bash", "summary": "Error: exit 1", "duration_ms": 30},
        {"type": "empty_nudge", "n": 1},
        {"type": "terminal_turn_end", "status": "ok", "duration_ms": 1234},
    ):
        r(event)

    out = capsys.readouterr().out
    assert "turn started" in out
    assert "step 1/12" in out
    assert "contacting openai / gpt-test" in out
    assert "model returned empty output" in out
    assert "turn ok" in out
    assert "1.2s" in out
    assert "1 model call(s)" in out
    assert "1 tool(s)" in out
    assert "1 error(s)" in out


def test_run_terminal_turn_emits_turn_boundaries(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from aegis.cli.repl import run_terminal_turn
    from aegis.config import Config
    from aegis.session import Session, SessionStore
    from aegis.types import Usage

    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    cfg = Config.load()
    cfg.data.setdefault("display", {})["status_footer"] = False
    session = Session.create(title="terminal test")
    store = SessionStore()
    agent = SimpleNamespace(
        config=cfg,
        session=session,
        tools_used=0,
        budget=SimpleNamespace(usage=Usage()),
        provider=SimpleNamespace(model="fake-model", context_length=128000),
        reasoning="medium",
        _trace_context={},
    )
    events: list[dict] = []

    class FakeRunner:
        def run_prompt(self, prompt, **kwargs):  # noqa: ANN001
            kwargs["on_event"]({"type": "iteration", "n": 1, "max": 3})
            return SimpleNamespace(
                message=SimpleNamespace(content="done"),
                run_id="run_fake",
                trace_id="trace_fake",
                turn_id="turn_fake",
            )

    message = run_terminal_turn(
        "do the work",
        agent,
        FakeRunner(),
        store,
        surface="cli",
        on_event=events.append,
    )

    assert message.content == "done"
    assert [event["type"] for event in events] == [
        "terminal_turn_start",
        "iteration",
        "terminal_turn_end",
    ]
    assert events[0]["chars"] == len("do the work")
    assert events[-1]["status"] == "ok"
    assert events[-1]["duration_ms"] >= 0
