"""gstack sprint orchestrator — exercised with an injected runner (no live model)."""

from aegis import gstack


def test_run_sprint_threads_session_and_runs_all_phases():
    calls = []

    def runner(prompt, session_id, cwd):
        calls.append((prompt, session_id))
        # backend assigns a session on the first turn; reused thereafter
        return f"out:{len(calls)}", session_id or "sess-1"

    result = gstack.run_sprint("ship a feature", config=None, runner=runner)

    # one call per phase, in order
    assert [name for name, _ in result.outputs] == gstack.PHASE_NAMES
    assert len(calls) == len(gstack.PHASES)
    # first phase starts with no session, the rest reuse the assigned one
    assert calls[0][1] is None
    assert all(sid == "sess-1" for _, sid in calls[1:])
    assert result.session_id == "sess-1"
    # the goal appears only in the first phase prompt
    assert "ship a feature" in calls[0][0]
    assert "ship a feature" not in calls[1][0]


def test_select_phases_subset_and_start():
    sub = gstack.select_phases(["think", "build"])
    assert [p.name for p in sub] == ["think", "build"]

    frm = gstack.select_phases(start="review")
    assert frm[0].name == "review"
    assert frm[-1].name == "reflect"

    # empty selection falls back to the full sprint
    assert gstack.select_phases([]) == gstack.PHASES


def test_on_phase_callbacks_fire_start_and_done():
    events = []

    def runner(prompt, session_id, cwd):
        return "x", "s"

    gstack.run_sprint("goal", config=None, runner=runner,
                      phases=gstack.select_phases(["think", "plan"]),
                      on_phase=lambda p, state, text: events.append((p.name, state)))
    assert events == [("think", "start"), ("think", "done"), ("plan", "start"), ("plan", "done")]


def test_repl_sprint_prompt_lists_goal_and_phases():
    prompt = gstack.repl_sprint_prompt("build a parser")
    assert "build a parser" in prompt
    for p in gstack.PHASES:
        assert p.name in prompt
