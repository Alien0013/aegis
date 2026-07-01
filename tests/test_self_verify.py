"""Pre-final self-verify gate: opt-in, the loop re-checks its own answer once before
finalizing (bounded so it can never loop), and stays off by default."""

from conftest import FakeProvider


def _agent(tmp_path, monkeypatch, *, self_verify, min_tools=0, script=None, verify_after_edit=False):
    home = tmp_path / "home"
    cwd = tmp_path / "work"
    home.mkdir()
    cwd.mkdir()
    monkeypatch.setenv("AEGIS_HOME", str(home))
    from aegis.agent.agent import Agent
    from aegis.config import Config
    from aegis.session import Session
    cfg = Config.load()
    cfg.data["learn"]["background"] = False        # don't fork a review during the test
    cfg.data["agent"]["self_verify"] = self_verify
    cfg.data["agent"]["self_verify_min_tools"] = min_tools
    cfg.data["agent"]["verify_after_edit"] = verify_after_edit
    cfg.data["tools"]["exec_mode"] = "full"
    return Agent(config=cfg, provider=FakeProvider(script), session=Session.create(), cwd=cwd)


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


def test_self_verify_uses_current_turn_tool_count(tmp_path, monkeypatch):
    from aegis.types import LLMResponse, ToolCall

    script = [
        LLMResponse(text="", tool_calls=[ToolCall("c1", "list_dir", {"path": "."})]),
        LLMResponse(text="first done"),
    ]
    agent = _agent(tmp_path, monkeypatch, self_verify=False, min_tools=1, script=script)

    first = agent.run("use a tool first")
    agent.config.data["agent"]["self_verify"] = True
    agent.provider.script.append(LLMResponse(text="second done"))
    events = []
    second = agent.run("no tools now", events.append)

    assert first.content == "first done"
    assert second.content == "second done"
    assert agent.provider.calls == 3
    assert not any(event["type"] == "self_verify" for event in events)


def test_verify_after_edit_is_opt_in():
    from aegis.agent.verification import build_verify_after_edit_nudge, verify_after_edit_enabled

    assert not verify_after_edit_enabled({})
    assert not build_verify_after_edit_nudge(config={}, changed_paths=["src/app.py"])
    assert verify_after_edit_enabled({"agent": {"verify_after_edit": True}})


def test_verify_after_edit_skips_prose_only_changes():
    from aegis.agent.verification import build_verify_after_edit_nudge

    config = {"agent": {"verify_after_edit": True}}
    nudge = build_verify_after_edit_nudge(
        config=config,
        changed_paths=["README.md", "docs/guide.rst", "notes.txt", "LICENSE"],
    )

    assert nudge is None


def test_verify_after_edit_records_mutating_file_tools_and_bounds_nudge_once():
    from aegis.agent.verification import VerificationAfterEditHarness

    config = {"agent": {"verify_after_edit": True}}
    harness = VerificationAfterEditHarness()

    harness.record_tool_result("read_file", {"path": "src/app.py"})
    harness.record_tool_result("write_file", {"path": "README.md"})
    harness.record_tool_result("edit_file", {"path": "src/app.py"})
    harness.record_tool_result("write_file", {"path": "src/failed.py"}, is_error=True)

    assert harness.changed_paths == ("README.md", "src/app.py")
    assert harness.verifiable_paths == ("src/app.py",)

    nudge = harness.build_nudge(config=config, verify_commands=["pytest"])

    assert nudge is not None
    assert "`src/app.py`" in nudge
    assert "`README.md`" not in nudge
    assert "`pytest`" in nudge
    assert harness.build_nudge(config=config, verify_commands=["pytest"]) is None


def test_verify_after_edit_extracts_apply_patch_paths():
    from aegis.agent.verification import VerificationAfterEditHarness

    harness = VerificationAfterEditHarness()
    harness.record_tool_result(
        "apply_patch",
        {
            "patch": (
                "diff --git a/src/app.py b/src/app.py\n"
                "--- a/src/app.py\n"
                "+++ b/src/app.py\n"
                "@@ -1 +1 @@\n"
                "-old\n"
                "+new\n"
            )
        },
    )

    assert harness.changed_paths == ("src/app.py",)


def test_verify_after_edit_prefers_landed_paths_from_tool_result():
    from aegis.agent.verification import VerificationAfterEditHarness

    harness = VerificationAfterEditHarness()
    harness.record_tool_result(
        "apply_patch",
        {"patch": ""},
        result_data={"files_modified": ["src/landed_a.py", "src/landed_b.py"]},
    )
    harness.record_tool_result(
        "write_file",
        {"path": "src/requested.py"},
        result='{"resolved_path": "/tmp/project/src/resolved.py"}',
    )

    assert harness.changed_paths == (
        "src/landed_a.py",
        "src/landed_b.py",
        "/tmp/project/src/resolved.py",
    )


def test_verify_after_edit_loop_nudges_before_final_after_code_edit(tmp_path, monkeypatch):
    from aegis.types import LLMResponse, ToolCall

    script = [
        LLMResponse(
            text="",
            tool_calls=[
                ToolCall("c1", "write_file", {"path": "src/app.py", "content": "print('hi')\n"})
            ],
        ),
        LLMResponse(text="done without verification"),
        LLMResponse(text="verified final"),
    ]
    agent = _agent(
        tmp_path,
        monkeypatch,
        self_verify=False,
        script=script,
        verify_after_edit=True,
    )
    events = []

    out = agent.run("write code", events.append)

    assert out.content == "verified final"
    assert agent.provider.calls == 3
    assert (agent.cwd / "src" / "app.py").read_text() == "print('hi')\n"
    assert any(event["type"] == "verify_after_edit" for event in events)
    nudges = [m.content for m in agent.session.messages if m.role == "user" and "verify_after_edit" in m.content]
    assert len(nudges) == 1
    assert "`src/app.py`" in nudges[0]


def test_verify_after_edit_loop_skips_docs_only_edit(tmp_path, monkeypatch):
    from aegis.types import LLMResponse, ToolCall

    script = [
        LLMResponse(
            text="",
            tool_calls=[
                ToolCall("c1", "write_file", {"path": "README.md", "content": "# Notes\n"})
            ],
        ),
        LLMResponse(text="docs final"),
    ]
    agent = _agent(
        tmp_path,
        monkeypatch,
        self_verify=False,
        script=script,
        verify_after_edit=True,
    )
    events = []

    out = agent.run("write docs", events.append)

    assert out.content == "docs final"
    assert agent.provider.calls == 2
    assert not any(event["type"] == "verify_after_edit" for event in events)
