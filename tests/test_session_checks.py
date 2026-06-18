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
    runs.start(surface="serve", kind="serve", session_id=clean.id, prompt="duplicate")

    report = cross_session_integrity_report(stale_running_seconds=0, stale_resume_pending_seconds=60)
    codes = {issue["code"] for issue in report["issues"]}

    assert report["ok"] is False
    assert report["status"] == "error"
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
    assert "duplicate_running_runs" in codes
    assert any(check["id"] == "session_store" and check["ok"] is True for check in report["checks"])
    assert any(check["id"] == "session_run_links" and check["ok"] is False for check in report["checks"])
    assert any(check["id"] == "resume_pending" and check["ok"] is False for check in report["checks"])
    assert report["counts"]["sessions_with_duplicate_running_runs"] == 1
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
    assert report["status"] == "ok"
    assert report["issue_count"] == 0
    assert report["counts"]["sessions_with_last_run"] == 1
    assert report["counts"]["resume_pending_sessions"] == 1
    assert any(check["id"] == "resume_pending" and check["ok"] is True for check in report["checks"])


def test_cross_session_integrity_report_degrades_on_warning_only_state(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))

    from aegis.runs import RunStore
    from aegis.session import Session, SessionStore
    from aegis.session_checks import cross_session_integrity_report

    store = SessionStore()
    runs = RunStore()
    session = Session(id="sess-warning-only", title="warning only")
    store.save(session)
    run = runs.start(surface="serve", kind="serve", session_id=session.id, prompt="stale")
    run["started_at"] = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    runs.write(run)

    report = cross_session_integrity_report(stale_running_seconds=60, stale_resume_pending_seconds=3600)

    assert report["ok"] is False
    assert report["status"] == "degraded"
    assert report["error_count"] == 0
    assert report["warning_count"] == 1
    assert {issue["code"] for issue in report["issues"]} == {"stale_running_run"}
    assert any(check["id"] == "stale_running_runs" and check["ok"] is False for check in report["checks"])


def test_repair_cross_session_integrity_interrupts_stale_running_runs(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))

    from aegis.runs import RunStore
    from aegis.session import Session, SessionStore
    from aegis.session_checks import cross_session_integrity_report, repair_cross_session_integrity

    store = SessionStore()
    runs = RunStore()
    session = Session(id="sess-repair", title="repair me")
    store.save(session)
    run = runs.start(surface="gateway", kind="chat", session_id=session.id, prompt="stale")
    run["started_at"] = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    runs.write(run)

    repair = repair_cross_session_integrity(stale_running_seconds=0, run_limit=10)

    saved = runs.get(run["id"])
    loaded = store.load(session.id)
    report = cross_session_integrity_report(stale_running_seconds=0, stale_resume_pending_seconds=3600)

    assert repair["repaired_running_runs"] == 1
    assert repair["marked_resume_pending"] == 1
    assert saved["status"] == "interrupted"
    assert saved["data"]["recovered_by_session_check"] is True
    assert loaded.meta["resume_pending"] is True
    assert loaded.meta["resume_reason"] == "cross_session_repair"
    assert "stale_running_run" not in {issue["code"] for issue in report["issues"]}


def test_repair_cross_session_integrity_interrupts_duplicate_running_runs(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))

    from aegis.runs import RunStore
    from aegis.session import Session, SessionStore
    from aegis.session_checks import cross_session_integrity_report, repair_cross_session_integrity

    store = SessionStore()
    runs = RunStore()
    session = Session(id="sess-duplicates", title="duplicate runs")
    store.save(session)
    older = runs.start(surface="gateway", kind="chat", session_id=session.id, prompt="older")
    older["started_at"] = (datetime.now(timezone.utc) - timedelta(minutes=3)).isoformat()
    runs.write(older)
    newer = runs.start(surface="gateway", kind="chat", session_id=session.id, prompt="newer")
    newer["started_at"] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    runs.write(newer)

    before = cross_session_integrity_report(stale_running_seconds=3600, stale_resume_pending_seconds=3600)
    repair = repair_cross_session_integrity(stale_running_seconds=3600, run_limit=10)
    after = cross_session_integrity_report(stale_running_seconds=3600, stale_resume_pending_seconds=3600)

    assert "duplicate_running_runs" in {issue["code"] for issue in before["issues"]}
    assert repair["repaired_duplicate_running_runs"] == 1
    assert runs.get(older["id"])["status"] == "interrupted"
    assert runs.get(older["id"])["data"]["repair_kind"] == "duplicate_running"
    assert runs.get(newer["id"])["status"] == "running"
    assert store.load(session.id).meta["resume_pending"] is True
    assert "duplicate_running_runs" not in {issue["code"] for issue in after["issues"]}
