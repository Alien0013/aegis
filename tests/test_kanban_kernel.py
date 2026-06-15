"""Kanban orchestration kernel: dependency gating, auto-promotion, runs, events,
heartbeats, structured handoff, created_cards gate, block/unblock, reclaim, workspaces."""

import pytest

from aegis.config import Config


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))


@pytest.fixture
def store():
    from aegis.kanban import KanbanStore
    return KanbanStore()


# -- dependency graph -------------------------------------------------------
def test_child_with_parent_starts_gated(store):
    p = store.create("parent")
    c = store.create("child", parents=[p.id])
    assert p.status == "ready"
    assert c.status == "todo"                      # gated until parent done
    assert store.parents(c.id) == [p.id]
    assert store.children(p.id) == [c.id]


def test_completing_parent_promotes_child(store):
    p = store.create("parent")
    c = store.create("child", parents=[p.id])
    store.complete(p.id, summary="done")
    assert store.show(c.id).status == "ready"      # auto-promoted


def test_child_waits_for_all_parents(store):
    p1, p2 = store.create("p1"), store.create("p2")
    c = store.create("c", parents=[p1.id, p2.id])
    store.complete(p1.id)
    assert store.show(c.id).status == "todo"       # still one parent open
    store.complete(p2.id)
    assert store.show(c.id).status == "ready"


def test_link_after_create_gates_a_ready_card(store):
    p = store.create("p")
    c = store.create("c")                            # starts ready
    assert c.status == "ready"
    store.link(p.id, c.id)
    assert store.show(c.id).status == "todo"        # linking to an open parent gates it
    store.complete(p.id)
    assert store.show(c.id).status == "ready"


def test_manual_promote_recovers_gated_card(store):
    p = store.create("p")
    c = store.create("c", parents=[p.id])
    assert store.promote(c.id) is True
    assert store.show(c.id).status == "ready"


# -- runs / retries ---------------------------------------------------------
def test_runs_record_attempts_with_outcomes(store):
    t = store.create("task")
    r1 = store.start_run(t.id, "worker-1")
    store.end_run(r1, "crashed", error="OOM")
    r2 = store.start_run(t.id, "worker-1")
    store.end_run(r2, "completed", summary="fixed it")
    runs = store.runs(t.id)
    assert [r.outcome for r in runs] == ["crashed", "completed"]
    assert runs[0].error == "OOM" and runs[1].summary == "fixed it"
    assert store.show(t.id).consecutive_failures == 0   # reset on success


def test_consecutive_failures_increment(store):
    t = store.create("task")
    for _ in range(3):
        store.end_run(store.start_run(t.id), "failed", error="boom")
    assert store.show(t.id).consecutive_failures == 3


# -- events -----------------------------------------------------------------
def test_events_log_state_changes(store):
    t = store.create("task")
    store.claim(t.id, "w1")
    store.heartbeat(t.id, "halfway")
    store.complete(t.id, summary="ok")
    kinds = [e.kind for e in store.events(t.id)]
    assert kinds[0] == "created"
    assert "claimed" in kinds and "heartbeat" in kinds and "completed" in kinds


# -- structured handoff -----------------------------------------------------
def test_complete_stores_summary_and_promotes(store):
    t = store.create("task")
    ok = store.complete(t.id, summary="shipped rate limiter", metadata={"tests": 14})
    assert ok
    done = store.show(t.id)
    assert done.status == "done"
    assert any("rate limiter" in cm.text for cm in store.comments(t.id))
    ev = [e for e in store.events(t.id) if e.kind == "completed"][0]
    assert ev.payload["metadata"] == {"tests": 14}


def test_worker_context_includes_parent_handoff(store):
    p = store.create("research")
    rid = store.start_run(p.id, "researcher")
    store.end_run(rid, "completed", summary="vLLM wins", metadata={"rec": "vLLM"})
    store.complete(p.id, summary="vLLM wins")
    c = store.create("synthesize", parents=[p.id])
    ctx = store.worker_context(c.id)
    assert ctx["parents"][0]["summary"] == "vLLM wins"
    assert ctx["parents"][0]["metadata"] == {"rec": "vLLM"}


# -- created_cards gate -----------------------------------------------------
def test_created_cards_gate_accepts_own_rejects_others(store):
    mine = store.create("mine", created_by="worker-1")
    theirs = store.create("theirs", created_by="worker-2")
    ok, bad = store.verify_created_cards([mine.id, theirs.id, "t_phantom"], "worker-1")
    assert ok == [mine.id]
    assert set(bad) == {theirs.id, "t_phantom"}


# -- block / unblock --------------------------------------------------------
def test_block_then_unblock_returns_to_ready(store):
    t = store.create("task")
    store.claim(t.id, "w1")
    store.block(t.id, "review-required: needs eyes")
    assert store.show(t.id).status == "blocked"
    assert any("review-required" in cm.text for cm in store.comments(t.id))
    assert store.unblock(t.id) is True
    assert store.show(t.id).status == "ready"


# -- reclaim stale ----------------------------------------------------------
def test_reclaim_stale_returns_silent_tasks_to_ready(store):
    t = store.create("task")
    store.claim(t.id, "w1")                          # in_progress, heartbeat = now
    # nothing is stale with a long timeout
    assert store.reclaim_stale(timeout_seconds=3600) == []
    # everything older than 0s is stale
    reclaimed = store.reclaim_stale(timeout_seconds=-1)
    assert t.id in reclaimed
    back = store.show(t.id)
    assert back.status == "ready" and back.assignee == ""


# -- workspaces -------------------------------------------------------------
def test_workspace_parsing():
    from aegis.kanban import _parse_workspace
    assert _parse_workspace("scratch") == ("scratch", "")
    assert _parse_workspace("dir:/tmp/x") == ("dir", "/tmp/x")
    assert _parse_workspace("worktree:wt/feature") == ("worktree", "wt/feature")
    assert _parse_workspace("bogus") == ("scratch", "")


def test_resolve_scratch_workspace_is_a_real_dir(store):
    from aegis.kanban_auto import resolve_workspace
    t = store.create("task")                          # default scratch
    ws = resolve_workspace(t)
    assert ws.is_dir()


# -- backward compatibility -------------------------------------------------
def test_legacy_flat_api_still_works(store):
    t = store.create("simple", body="do it", priority=2)
    assert t.status == "ready"
    assert store.claim_next("auto") is not None
    assert store.complete(t.id) is True
    assert store.list(status="done")[0].id == t.id


def test_stats(store):
    store.create("a")
    b = store.create("b")
    store.complete(b.id)
    st = store.stats()
    assert st["by_status"].get("ready") == 1
    assert st["by_status"].get("done") == 1


# -- tool surface -----------------------------------------------------------
def test_tool_create_with_parents_and_structured_complete(tmp_path):
    from aegis.tools.base import ToolContext
    from aegis.tools.kanban_tool import KanbanTool
    t = KanbanTool()
    ctx = ToolContext(cwd=tmp_path, config=Config.load())
    p = t.run({"action": "create", "title": "parent"}, ctx)
    pid = p.content.split()[1].rstrip(":")
    c = t.run({"action": "create", "title": "child", "parents": [pid]}, ctx)
    assert "gated: todo" in c.content
    # complete parent with summary+metadata
    done = t.run({"action": "complete", "id": pid, "text": "built",
                  "metadata": {"tests_run": 9}}, ctx)
    assert not done.is_error
    # child now shows as ready in a list
    assert "child" in t.run({"action": "list", "filter_status": "ready"}, ctx).content


def test_tool_created_cards_gate_rejects_phantoms(tmp_path):
    from aegis.tools.base import ToolContext
    from aegis.tools.kanban_tool import KanbanTool
    t = KanbanTool()
    ctx = ToolContext(cwd=tmp_path, config=Config.load())
    made = t.run({"action": "create", "title": "real card"}, ctx)
    cid = made.content.split()[1].rstrip(":")
    out = t.run({"action": "complete", "id": cid,
                 "created_cards": ["t_phantom"]}, ctx)
    assert out.is_error and "rejected" in out.content
