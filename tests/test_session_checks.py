from __future__ import annotations

def test_cross_session_integrity_report_detects_replay_and_lineage_gaps(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))

    from aegis.runs import RunStore
    from aegis.session import Session, SessionStore
    from aegis.session_checks import cross_session_integrity_report
    from aegis.types import Message

    store = SessionStore()
    runs = RunStore()

    clean = Session(id="sess-clean", title="clean")
    clean.messages = [Message.user("hello"), Message.assistant("there")]
    clean.meta["_gateway_generation"] = 2
    clean_run = runs.start(surface="dashboard", kind="chat", session_id=clean.id, prompt="hello")
    runs.finish(clean_run["id"], status="ok", result="there", trace_id="trace-clean")
    clean.meta["last_run_id"] = clean_run["id"]
    store.save(clean)

    missing_parent = Session(id="sess-child", title="child", parent_id="missing-parent")
    store.save(missing_parent)

    missing_last = Session(id="sess-missing-run", title="missing run")
    missing_last.meta["last_run_id"] = "run_missing"
    store.save(missing_last)

    mismatch = Session(id="sess-mismatch", title="mismatch")
    mismatch_run = runs.start(surface="dashboard", kind="chat", session_id="sess-other", prompt="wrong owner")
    runs.finish(mismatch_run["id"], status="ok", result="wrong owner")
    mismatch.meta["last_run_id"] = mismatch_run["id"]
    store.save(mismatch)

    bad_generation = Session(id="sess-bad-generation", title="bad generation")
    bad_generation.meta["_gateway_generation"] = "not-int"
    store.save(bad_generation)

    orphan_run = runs.start(surface="serve", kind="serve", session_id="missing-session", prompt="orphan")
    runs.finish(orphan_run["id"], status="ok", result="orphan")

    runs.start(surface="serve", kind="serve", session_id=clean.id, prompt="stale")

    report = cross_session_integrity_report(stale_running_seconds=0)
    codes = {issue["code"] for issue in report["issues"]}

    assert report["ok"] is False
    assert report["counts"]["sessions"] >= 5
    assert "missing_parent_session" in codes
    assert "missing_last_run" in codes
    assert "last_run_session_mismatch" in codes
    assert "malformed_gateway_generation" in codes
    assert "run_missing_session" in codes
    assert "stale_running_run" in codes
    assert any(check["id"] == "session_store" and check["ok"] is True for check in report["checks"])
    assert any(check["id"] == "session_run_links" and check["ok"] is False for check in report["checks"])


def test_cross_session_integrity_report_is_clean_for_consistent_state(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))

    from aegis.runs import RunStore
    from aegis.session import Session, SessionStore
    from aegis.session_checks import cross_session_integrity_report
    from aegis.types import Message

    store = SessionStore()
    runs = RunStore()

    session = Session(id="sess-clean-only", title="clean only")
    session.messages = [Message.user("ping"), Message.assistant("pong")]
    run = runs.start(surface="dashboard", kind="chat", session_id=session.id, prompt="ping")
    runs.finish(run["id"], status="ok", result="pong")
    session.meta["last_run_id"] = run["id"]
    session.meta["_gateway_generation"] = 0
    store.save(session)

    report = cross_session_integrity_report(stale_running_seconds=60)

    assert report["ok"] is True
    assert report["issue_count"] == 0
    assert report["counts"]["sessions_with_last_run"] == 1
