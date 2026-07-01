from types import SimpleNamespace

from aegis.cli import repl


def _capture_output(monkeypatch):
    lines: list[str] = []
    monkeypatch.setattr(repl, "_out", lambda text="", style=None: lines.append(str(text)))
    return lines


def test_handle_slash_expands_unique_prefix_and_preserves_args(monkeypatch):
    lines = _capture_output(monkeypatch)

    assert repl.handle_slash("/hel session", SimpleNamespace()) == ""

    rendered = "\n".join(lines)
    assert "slash commands matching 'session':" in rendered
    assert "/sessions" in rendered
    assert "/resume" in rendered
    assert "Anything else is sent to the agent." in rendered
    assert "unknown command" not in rendered


def test_handle_slash_reports_ambiguous_prefix(monkeypatch):
    lines = _capture_output(monkeypatch)

    assert repl.handle_slash("/re", SimpleNamespace()) == ""

    assert len(lines) == 1
    assert lines[0].startswith("ambiguous command /re; did you mean: ")
    assert "/reset" in lines[0]
    assert "/retry" in lines[0]


def test_slash_prefix_resolution_prefers_exact_then_unique_shortest():
    resolved, ambiguous = repl.resolve_slash_prefix("/status")
    assert resolved == "/status"
    assert ambiguous == []

    resolved, ambiguous = repl.resolve_slash_prefix("/stat")
    assert resolved == "/status"
    assert ambiguous == []


def test_terminal_input_expands_prefix_before_preprocessor(monkeypatch):
    calls: list[str] = []

    def fake_plan_command(user, agent):
        calls.append(user)
        return ""

    monkeypatch.setattr(repl, "handle_plan_command", fake_plan_command)

    result = repl.process_terminal_input(
        "/pl inspect the registry",
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(),
        on_event=lambda event: None,
    )

    assert result == "handled"
    assert calls == ["/plan inspect the registry"]
