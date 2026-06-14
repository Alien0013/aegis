"""The terminal tool-trail rendering: per-tool verbs/previews, failure detection,
and the Hermes-style compaction/skill nudges. We drive the Renderer with events
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
    assert "read" in out and "✓ read 12 lines" in out
    assert "✗ Error: exit 1" in out                 # non-is_error failure still flagged
    assert "freed" in out and "75%" in out           # compaction delta line
