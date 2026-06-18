"""Cross-session harness integrity checks.

The dashboard, API adapter, gateway, and eval tooling all join around the same
session/run IDs.  This module keeps those joins auditable after restarts.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


_GATEWAY_GENERATION_META = "_gateway_generation"
_RESUME_PENDING_META = "resume_pending"
_RESUME_REASON_META = "resume_reason"
_RESUME_MARKED_AT_META = "last_resume_marked_at"


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _issue(
    issues: list[dict[str, Any]],
    *,
    code: str,
    severity: str,
    message: str,
    session_id: str = "",
    run_id: str = "",
    details: dict[str, Any] | None = None,
) -> None:
    row: dict[str, Any] = {
        "code": code,
        "severity": severity,
        "message": message,
    }
    if session_id:
        row["session_id"] = session_id
    if run_id:
        row["run_id"] = run_id
    if details:
        row["details"] = details
    issues.append(row)


def cross_session_integrity_report(
    *,
    session_limit: int = 500,
    run_limit: int = 500,
    stale_running_seconds: float = 6 * 60 * 60,
    stale_resume_pending_seconds: float = 24 * 60 * 60,
) -> dict[str, Any]:
    """Return a Hermes-style restart/replay health report for session state.

    The report is intentionally read-only.  It validates the durable joins that
    power session restore, run replay, approval/event lookups, and gateway
    generation stale-write protection.
    """

    from .runs import RunStore
    from .session import SessionStore

    session_limit = max(1, int(session_limit or 1))
    run_limit = max(1, int(run_limit or 1))
    stale_running_seconds = max(0.0, float(stale_running_seconds or 0))
    stale_resume_pending_seconds = max(0.0, float(stale_resume_pending_seconds or 0))
    issues: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []

    store = SessionStore()
    runs = RunStore()
    session_rows = store.list(session_limit, include_internal=True)
    sessions = {}
    for row in session_rows:
        sid = str(row.get("id") or "")
        if not sid:
            continue
        session = store.load(sid)
        if session is not None:
            sessions[sid] = session

    run_rows = runs.list(limit=run_limit)
    run_by_id = {str(row.get("id") or ""): row for row in run_rows if row.get("id")}
    now = datetime.now(timezone.utc)

    for sid, session in sessions.items():
        if session.parent_id and store.load(session.parent_id) is None:
            _issue(
                issues,
                code="missing_parent_session",
                severity="error",
                session_id=sid,
                message=f"session parent is missing: {session.parent_id}",
                details={"parent_id": session.parent_id},
            )

        meta = session.meta if isinstance(session.meta, dict) else {}
        generation = meta.get(_GATEWAY_GENERATION_META)
        if generation is not None:
            try:
                if int(generation) < 0:
                    raise ValueError("negative generation")
            except (TypeError, ValueError):
                _issue(
                    issues,
                    code="malformed_gateway_generation",
                    severity="error",
                    session_id=sid,
                    message="gateway generation marker is not a non-negative integer",
                    details={"value": generation},
                )

        last_run_id = str(meta.get("last_run_id") or "")
        if last_run_id:
            run = run_by_id.get(last_run_id) or runs.get(last_run_id)
            if run is None:
                _issue(
                    issues,
                    code="missing_last_run",
                    severity="warning",
                    session_id=sid,
                    run_id=last_run_id,
                    message="session meta points at a run that is not in RunStore",
                )
            else:
                run_session_id = str(run.get("session_id") or "")
                if run_session_id and run_session_id != sid:
                    _issue(
                        issues,
                        code="last_run_session_mismatch",
                        severity="error",
                        session_id=sid,
                        run_id=str(run.get("id") or last_run_id),
                        message="session last_run_id points at a run owned by another session",
                        details={"run_session_id": run_session_id},
                    )
                last_trace_id = str(meta.get("last_trace_id") or meta.get("trace_id") or "")
                run_trace_id = str(run.get("trace_id") or "")
                if last_trace_id and run_trace_id and last_trace_id != run_trace_id:
                    _issue(
                        issues,
                        code="last_trace_mismatch",
                        severity="warning",
                        session_id=sid,
                        run_id=str(run.get("id") or last_run_id),
                        message="session last_trace_id does not match the last run trace_id",
                        details={"session_trace_id": last_trace_id, "run_trace_id": run_trace_id},
                    )

        if meta.get(_RESUME_PENDING_META):
            reason = str(meta.get(_RESUME_REASON_META) or "")
            marked_raw = str(meta.get(_RESUME_MARKED_AT_META) or "")
            marked_at = _utc(_parse_iso(marked_raw))
            if not reason:
                _issue(
                    issues,
                    code="missing_resume_reason",
                    severity="warning",
                    session_id=sid,
                    message="resume_pending session is missing a resume reason",
                )
            if marked_at is None:
                _issue(
                    issues,
                    code="malformed_resume_pending",
                    severity="warning",
                    session_id=sid,
                    message="resume_pending session is missing or has an invalid timestamp",
                    details={"last_resume_marked_at": marked_raw},
                )
            else:
                age = max(0.0, (now - marked_at).total_seconds())
                if age >= stale_resume_pending_seconds:
                    _issue(
                        issues,
                        code="stale_resume_pending",
                        severity="warning",
                        session_id=sid,
                        message="resume_pending session has not been resumed within the stale threshold",
                        details={
                            "age_seconds": int(age),
                            "threshold_seconds": int(stale_resume_pending_seconds),
                            "resume_reason": reason,
                        },
                    )

    for run in run_rows:
        run_id = str(run.get("id") or "")
        session_id = str(run.get("session_id") or "")
        if session_id and store.load(session_id) is None:
            _issue(
                issues,
                code="run_missing_session",
                severity="warning",
                session_id=session_id,
                run_id=run_id,
                message="run references a session that is not in SessionStore",
            )
        status = str(run.get("status") or "").lower()
        started_at = _utc(_parse_iso(str(run.get("started_at") or "")))
        if status == "running" and started_at is not None:
            age = max(0.0, (now - started_at).total_seconds())
            if age >= stale_running_seconds:
                _issue(
                    issues,
                    code="stale_running_run",
                    severity="warning",
                    session_id=session_id,
                    run_id=run_id,
                    message="run is still marked running after the stale threshold",
                    details={"age_seconds": int(age), "threshold_seconds": int(stale_running_seconds)},
                )

    running_by_session: dict[str, list[dict[str, Any]]] = {}
    for run in run_rows:
        if str(run.get("status") or "").lower() != "running":
            continue
        session_id = str(run.get("session_id") or "")
        if not session_id:
            continue
        running_by_session.setdefault(session_id, []).append(run)
    for session_id, rows in running_by_session.items():
        if len(rows) <= 1:
            continue
        run_ids = [str(run.get("id") or "") for run in rows if run.get("id")]
        _issue(
            issues,
            code="duplicate_running_runs",
            severity="warning",
            session_id=session_id,
            message="session has multiple runs still marked running",
            details={"run_ids": run_ids},
        )

    error_count = sum(1 for issue in issues if issue.get("severity") == "error")
    warning_count = sum(1 for issue in issues if issue.get("severity") == "warning")
    status = "error" if error_count else ("degraded" if warning_count else "ok")
    checks.extend([
        {
            "id": "session_store",
            "ok": True,
            "count": len(sessions),
            "detail": f"{len(sessions)} sessions loaded",
        },
        {
            "id": "run_store",
            "ok": True,
            "count": len(run_rows),
            "detail": f"{len(run_rows)} runs loaded",
        },
        {
            "id": "session_run_links",
            "ok": not any(issue["code"] in {"missing_last_run", "last_run_session_mismatch", "last_trace_mismatch"}
                          for issue in issues),
            "detail": "session last_run_id and trace links are consistent",
        },
        {
            "id": "lineage",
            "ok": not any(issue["code"] == "missing_parent_session" for issue in issues),
            "detail": "session parent links are consistent",
        },
        {
            "id": "gateway_generation",
            "ok": not any(issue["code"] == "malformed_gateway_generation" for issue in issues),
            "detail": "gateway generation markers are valid",
        },
        {
            "id": "stale_running_runs",
            "ok": not any(issue["code"] in {"stale_running_run", "duplicate_running_runs"} for issue in issues),
            "detail": "no stale or duplicate running runs detected",
        },
        {
            "id": "resume_pending",
            "ok": not any(
                issue["code"] in {"missing_resume_reason", "malformed_resume_pending", "stale_resume_pending"}
                for issue in issues
            ),
            "detail": "resume-pending gateway sessions are fresh and well formed",
        },
    ])
    return {
        "ok": status == "ok",
        "status": status,
        "error_count": error_count,
        "warning_count": warning_count,
        "issue_count": len(issues),
        "checks": checks,
        "issues": issues,
        "limits": {
            "sessions": session_limit,
            "runs": run_limit,
            "stale_running_seconds": stale_running_seconds,
            "stale_resume_pending_seconds": stale_resume_pending_seconds,
        },
        "counts": {
            "sessions": len(sessions),
            "runs": len(run_rows),
            "running_runs": sum(
                1 for run in run_rows
                if str(run.get("status") or "").lower() == "running"
            ),
            "sessions_with_duplicate_running_runs": sum(
                1 for rows in running_by_session.values()
                if len(rows) > 1
            ),
            "sessions_with_last_run": sum(
                1 for session in sessions.values()
                if isinstance(session.meta, dict) and session.meta.get("last_run_id")
            ),
            "resume_pending_sessions": sum(
                1 for session in sessions.values()
                if isinstance(session.meta, dict) and session.meta.get(_RESUME_PENDING_META)
            ),
        },
    }


def repair_cross_session_integrity(
    *,
    run_limit: int = 500,
    stale_running_seconds: float = 6 * 60 * 60,
    resume_reason: str = "cross_session_repair",
) -> dict[str, Any]:
    """Conservatively repair durable run/session state after a restart.

    This does not delete transcripts or rewrite user-visible messages.  It only
    closes stale ``running`` run records as ``interrupted`` and marks their
    linked sessions ``resume_pending`` so the next gateway turn gets the same
    recovery directive used by gateway startup recovery.
    """

    from .runs import RunStore
    from .session import SessionStore

    run_limit = max(1, int(run_limit or 1))
    stale_running_seconds = max(0.0, float(stale_running_seconds or 0))
    reason = str(resume_reason or "cross_session_repair")
    runs = RunStore()
    store = SessionStore()
    now = datetime.now(timezone.utc)
    repaired: list[dict[str, Any]] = []
    marked_resume = 0
    skipped = 0
    stale_interrupted = 0
    duplicate_interrupted = 0
    repaired_ids: set[str] = set()

    for run in runs.list(status="running", limit=run_limit):
        started_at = _utc(_parse_iso(str(run.get("started_at") or "")))
        if started_at is None:
            skipped += 1
            continue
        age = max(0.0, (now - started_at).total_seconds())
        if age < stale_running_seconds:
            continue
        run_id = str(run.get("id") or "")
        session_id = str(run.get("session_id") or "")
        marked = False
        if session_id and store.load(session_id) is not None:
            try:
                marked = store.mark_resume_pending(session_id, reason)
            except Exception:  # noqa: BLE001
                marked = False
        if marked:
            marked_resume += 1
        try:
            runs.finish(
                run_id,
                status="interrupted",
                error="Run was still marked running during cross-session repair.",
                data={
                    "recovered_by_session_check": True,
                    "resume_pending": marked,
                    "repair_reason": reason,
                },
            )
        except Exception as exc:  # noqa: BLE001
            skipped += 1
            repaired.append({
                "run_id": run_id,
                "session_id": session_id,
                "status": "error",
                "error": f"{type(exc).__name__}: {exc}",
            })
            continue
        repaired.append({
            "run_id": run_id,
            "session_id": session_id,
            "status": "interrupted",
            "age_seconds": int(age),
            "resume_pending": marked,
            "repair_kind": "stale_running",
        })
        stale_interrupted += 1
        repaired_ids.add(run_id)

    running_by_session: dict[str, list[dict[str, Any]]] = {}
    for run in runs.list(status="running", limit=run_limit):
        session_id = str(run.get("session_id") or "")
        if session_id:
            running_by_session.setdefault(session_id, []).append(run)
    for session_id, rows in running_by_session.items():
        if len(rows) <= 1:
            continue
        rows.sort(
            key=lambda row: (
                _utc(_parse_iso(str(row.get("started_at") or "")))
                or datetime.min.replace(tzinfo=timezone.utc)
            ),
            reverse=True,
        )
        for run in rows[1:]:
            run_id = str(run.get("id") or "")
            if not run_id or run_id in repaired_ids:
                continue
            marked = False
            if store.load(session_id) is not None:
                try:
                    marked = store.mark_resume_pending(session_id, reason)
                except Exception:  # noqa: BLE001
                    marked = False
            if marked:
                marked_resume += 1
            try:
                runs.finish(
                    run_id,
                    status="interrupted",
                    error="Duplicate running run was interrupted during cross-session repair.",
                    data={
                        "recovered_by_session_check": True,
                        "resume_pending": marked,
                        "repair_reason": reason,
                        "repair_kind": "duplicate_running",
                    },
                )
            except Exception as exc:  # noqa: BLE001
                skipped += 1
                repaired.append({
                    "run_id": run_id,
                    "session_id": session_id,
                    "status": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                })
                continue
            duplicate_interrupted += 1
            repaired_ids.add(run_id)
            repaired.append({
                "run_id": run_id,
                "session_id": session_id,
                "status": "interrupted",
                "resume_pending": marked,
                "repair_kind": "duplicate_running",
            })

    return {
        "ok": skipped == 0,
        "repaired_running_runs": stale_interrupted,
        "repaired_duplicate_running_runs": duplicate_interrupted,
        "marked_resume_pending": marked_resume,
        "skipped": skipped,
        "runs": repaired,
    }
