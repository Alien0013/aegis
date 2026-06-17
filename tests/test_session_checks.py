from __future__ import annotations

from datetime import datetime, timedelta, timezone


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

    trace_mismatch = Session(id="sess-trace-mismatch", title="trace mismatch")
    trace_run = runs.start(surface="dashboard", kind="chat", session_id=trace_mismatch.id, prompt="trace")
    runs.finish(trace_run["id"], status="ok", result="trace", trace_id="trace-run")
    trace_mismatch.meta["last_run_id"] = trace_run["id"]
    trace_mismatch.meta["last_trace_id"] = "trace-session"
    store.save(trace_mismatch)

    bad_generation = Session(id="sess-bad-generation", title="bad generation")
    bad_generation.meta["_gateway_generation"] = "not-int"
    store.save(bad_generation)

    bad_resume = Session(id="sess-bad-resume", title="bad resume")
    bad_resume.meta["resume_pending"] = True
    bad_resume.meta["last_resume_marked_at"] = "not-a-date"
    store.save(bad_resume)

    stale_resume = Session(id="sess-stale-resume", title="stale resume")
    stale_resume.meta["resume_pending"] = True
    stale_resume.meta["resume_reason"] = "restart_interrupted"
    stale_resume.meta["last_resume_marked_at"] = (
        datetime.now(timezone.utc) - timedelta(days=2)
    ).isoformat()
    store.save(stale_resume)

    orphan_run = runs.start(surface="serve", kind="serve", session_id="missing-session", prompt="orphan")
    runs.finish(orphan_run["id"], status="ok", result="orphan")

    runs.start(surface="serve", kind="serve", session_id=clean.id, prompt="stale")

    report = cross_session_integrity_report(stale_running_seconds=0, stale_resume_pending_seconds=60)
    codes = {issue["code"] for issue in report["issues"]}

    assert report["ok"] is False
    assert report["counts"]["sessions"] >= 5
    assert "missing_parent_session" in codes
    assert "missing_last_run" in codes
    assert "last_run_session_mismatch" in codes
    assert "last_trace_mismatch" in codes
    assert "malformed_gateway_generation" in codes
    assert "missing_resume_reason" in codes
    assert "malformed_resume_pending" in codes
    assert "stale_resume_pending" in codes
    assert "run_missing_session" in codes
    assert "stale_running_run" in codes
    assert any(check["id"] == "session_store" and check["ok"] is True for check in report["checks"])
    assert any(check["id"] == "session_run_links" and check["ok"] is False for check in report["checks"])
    assert any(check["id"] == "resume_pending" and check["ok"] is False for check in report["checks"])
    assert report["counts"]["resume_pending_sessions"] == 2


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
    session.meta["last_trace_id"] = "trace-clean-only"
    session.meta["_gateway_generation"] = 0
    session.meta["resume_pending"] = True
    session.meta["resume_reason"] = "restart_interrupted"
    session.meta["last_resume_marked_at"] = datetime.now(timezone.utc).isoformat()
    store.save(session)
    runs.finish(run["id"], status="ok", result="pong", trace_id="trace-clean-only")

    report = cross_session_integrity_report(stale_running_seconds=60, stale_resume_pending_seconds=3600)

    assert report["ok"] is True
    assert report["issue_count"] == 0
    assert report["counts"]["sessions_with_last_run"] == 1
    assert report["counts"]["resume_pending_sessions"] == 1
    assert any(check["id"] == "resume_pending" and check["ok"] is True for check in report["checks"])
