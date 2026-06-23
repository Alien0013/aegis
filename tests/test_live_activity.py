from __future__ import annotations


def test_live_activity_tracks_provider_tool_subagent_and_finish():
    from aegis import activity

    activity_id = "test-live-activity"
    try:
        activity.start(
            activity_id,
            surface="dashboard",
            session_id="sess_live_activity",
            run_id="run_live_activity",
            provider="fake",
            model="model-a",
        )
        activity.update(activity_id, {"type": "iteration", "n": 2, "max": 8})
        activity.update(activity_id, {
            "type": "provider_start",
            "provider": "codex",
            "model": "gpt-5.5",
            "trace_id": "trace_live_activity",
            "turn_id": "turn_live_activity",
        })
        activity.update(activity_id, {"type": "provider_end", "status": "ok"})
        activity.update(activity_id, {"type": "tool_start", "id": "tool_1", "name": "read_file"})
        activity.update(activity_id, {"type": "tool_result", "id": "tool_1", "name": "read_file"})
        activity.update(activity_id, {"type": "subagent_start", "id": "sub_1", "agent_type": "review"})
        activity.update(activity_id, {"type": "subagent_done", "id": "sub_1", "agent_type": "review"})

        current = activity.current(activity_id)
        assert current is not None
        assert current["provider"] == "codex"
        assert current["model"] == "gpt-5.5"
        assert current["trace_id"] == "trace_live_activity"
        assert current["turn_id"] == "turn_live_activity"
        assert current["iteration"] == 2
        assert current["tool_calls"] == 1
        assert current["last_tool"] == "subagent:review"
        assert current["subagents_done"] == 1

        final = activity.finish(activity_id, status="ok")
        assert final is not None
        assert final["status"] == "ok"
        assert final["phase"] == "done"
        assert final["active_tool"] == ""
        assert activity.current(activity_id) is None
    finally:
        activity.finish(activity_id, status="cancelled")
