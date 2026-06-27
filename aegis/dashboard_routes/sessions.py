"""Sessions dashboard routes — extracted from dashboard_fastapi.create_app.

register() wires this group's handlers onto the shared FastAPI ``app`` (closing over
``config`` + ``chat_runner``, exactly as the original nested routes did). Module-level
deps are imported from :mod:`aegis.dashboard_fastapi`; relative imports inside the
handlers are one level deeper than the original (this module lives one package down).
register_all preserves the original cross-module order so the catch-alls register last.
"""

from __future__ import annotations

from ..dashboard_fastapi import (
    JSONResponse,
    Request,
    _background_job_cancel_response,
    _background_job_retry_response,
    _background_jobs_payload,
    _delete_sessions,
    _empty_session_count,
    _empty_sessions,
    _load_session,
    _message_from_payload,
    _message_payload,
    _patched_message,
    _prune_sessions,
    _require_request,
    _session_export,
    _session_stats,
    copy,
    dash,
)


def register(app, config, chat_runner):
    @app.get("/api/sessions")
    async def api_sessions_list(request: Request) -> JSONResponse:
        _require_request(request, config)
        limit = int(request.query_params.get("limit") or 100)
        from ..session import SessionStore

        return JSONResponse(SessionStore().list(max(1, min(limit, 1000))))

    @app.get("/api/sessions/stats")
    async def api_sessions_stats(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(_session_stats())

    @app.get("/api/sessions/search")
    async def api_sessions_search(request: Request) -> JSONResponse:
        _require_request(request, config)
        from ..session import SessionStore

        store = SessionStore()
        query = str(request.query_params.get("query") or request.query_params.get("q") or "").strip()
        limit = int(request.query_params.get("limit") or (3 if query else 10))
        current_session_id = request.query_params.get("current_session_id")
        if not query:
            return JSONResponse(store.browse_sessions(limit=limit, current_session_id=current_session_id))
        role_filter = request.query_params.getlist("role")
        if not role_filter and request.query_params.get("role_filter"):
            role_filter = [r.strip() for r in request.query_params["role_filter"].split(",") if r.strip()]
        return JSONResponse(store.discover_sessions(
            query,
            limit=limit,
            role_filter=role_filter or None,
            sort=request.query_params.get("sort"),
            current_session_id=current_session_id,
        ))

    @app.post("/api/sessions/prune")
    async def api_sessions_prune(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        return JSONResponse(_prune_sessions(int(body.get("older_than_days", 30))))

    @app.get("/api/sessions/empty")
    async def api_sessions_empty(request: Request) -> JSONResponse:
        _require_request(request, config)
        older_than_days = float(request.query_params.get("older_than_days") or 0)
        return JSONResponse(_empty_sessions(older_than_days, dry_run=True))

    @app.get("/api/sessions/empty/count")
    async def api_sessions_empty_count(request: Request) -> JSONResponse:
        _require_request(request, config)
        older_than_days = float(request.query_params.get("older_than_days") or 0)
        return JSONResponse(_empty_session_count(older_than_days))

    @app.delete("/api/sessions/empty")
    async def api_sessions_empty_delete(request: Request) -> JSONResponse:
        _require_request(request, config)
        older_than_days = float(request.query_params.get("older_than_days") or 0)
        return JSONResponse(_empty_sessions(older_than_days, dry_run=False))

    @app.post("/api/sessions/prune-empty")
    async def api_sessions_prune_empty(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        return JSONResponse(_empty_sessions(
            float(body.get("older_than_days", 0)),
            dry_run=bool(body.get("dry_run", False)),
        ))

    @app.post("/api/sessions/delete")
    async def api_sessions_delete_many(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        result = _delete_sessions(body.get("ids") if isinstance(body, dict) else None)
        return JSONResponse(result, status_code=200 if result.get("ok") else 400)

    @app.post("/api/sessions/bulk-delete")
    async def api_sessions_bulk_delete(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        ids = body.get("ids") if isinstance(body, dict) else None
        if not ids and isinstance(body, dict):
            ids = body.get("session_ids")
        result = _delete_sessions(ids)
        return JSONResponse(result, status_code=200 if result.get("ok") else 400)

    @app.get("/api/sessions/{session_id}/latest-descendant")
    async def api_session_latest_descendant(session_id: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        from ..session import SessionStore

        lineage = SessionStore().lineage(session_id)
        if not lineage.get("found"):
            return JSONResponse({"ok": False, "error": "Session not found"}, status_code=404)
        current = lineage.get("current") or {}
        requested = str(current.get("id") or session_id)
        children: dict[str, list[dict]] = {}
        for row in lineage.get("descendants", []) or []:
            if isinstance(row, dict) and row.get("id") and row.get("parent_id"):
                children.setdefault(str(row["parent_id"]), []).append(row)
        path = [requested]
        seen = {requested}
        cursor = requested
        while children.get(cursor):
            candidates = [row for row in children[cursor] if str(row.get("id")) not in seen]
            if not candidates:
                break
            candidates.sort(
                key=lambda row: (
                    str(row.get("updated_at") or ""),
                    str(row.get("created_at") or ""),
                    str(row.get("id") or ""),
                ),
                reverse=True,
            )
            cursor = str(candidates[0]["id"])
            path.append(cursor)
            seen.add(cursor)
        return JSONResponse({
            "requested_session_id": requested,
            "session_id": cursor,
            "path": path,
            "changed": cursor != requested,
        })

    @app.get("/api/sessions/{session_id}")
    async def api_session_detail(session_id: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(dash._dashboard_session_detail(session_id, config))

    @app.get("/api/sessions/{session_id}/prompt-audit")
    async def api_session_prompt_audit(session_id: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        result = dash._dashboard_session_prompt_audit(session_id, config)
        return JSONResponse(result, status_code=200 if result.get("found") else 404)

    @app.get("/api/sessions/{session_id}/lineage")
    async def api_session_lineage(session_id: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        from ..session import SessionStore

        result = SessionStore().lineage(session_id)
        return JSONResponse(result, status_code=200 if result.get("found") else 404)

    @app.patch("/api/sessions/{session_id}")
    async def api_session_patch(session_id: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        store, session = _load_session(session_id)
        if session is None:
            return JSONResponse({"ok": False, "error": "session not found", "id": session_id}, status_code=404)
        if "title" in body:
            title = str(body.get("title") or "").strip()
            if not title:
                return JSONResponse({"ok": False, "error": "title cannot be empty"}, status_code=400)
            session.title = title
        if "meta" in body:
            if not isinstance(body["meta"], dict):
                return JSONResponse({"ok": False, "error": "meta must be an object"}, status_code=400)
            session.meta.update(copy.deepcopy(body["meta"]))
        if "todos" in body:
            if not isinstance(body["todos"], list):
                return JSONResponse({"ok": False, "error": "todos must be a list"}, status_code=400)
            session.todos = copy.deepcopy(body["todos"])
        store.save(session)
        return JSONResponse({"ok": True, "session": _session_export(session)})

    @app.post("/api/sessions/{session_id}/rename")
    async def api_session_rename(session_id: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        title = str(body.get("title") or body.get("name") or "").strip()
        if not title:
            return JSONResponse({"ok": False, "error": "title is required"}, status_code=400)
        store, session = _load_session(session_id)
        if session is None:
            return JSONResponse({"ok": False, "error": "session not found", "id": session_id}, status_code=404)
        session.title = title
        store.save(session)
        return JSONResponse({"ok": True, "id": session.id, "title": session.title})

    @app.get("/api/sessions/{session_id}/messages")
    async def api_session_messages(session_id: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        _store, session = _load_session(session_id)
        if session is None:
            return JSONResponse({"ok": False, "error": "session not found", "id": session_id}, status_code=404)
        return JSONResponse({
            "ok": True,
            "id": session.id,
            "count": len(session.messages),
            "messages": [_message_payload(message, i) for i, message in enumerate(session.messages)],
        })

    @app.get("/api/sessions/{session_id}/timeline")
    async def api_session_timeline(session_id: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        result = dash._dashboard_session_timeline(session_id, config)
        return JSONResponse(result, status_code=200 if result.get("found") else 404)

    @app.post("/api/sessions/{session_id}/messages")
    async def api_session_message_add(session_id: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        store, session = _load_session(session_id)
        if session is None:
            return JSONResponse({"ok": False, "error": "session not found", "id": session_id}, status_code=404)
        try:
            message = _message_from_payload(body)
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        session.messages.append(message)
        store.save(session)
        index = len(session.messages) - 1
        return JSONResponse({"ok": True, "id": session.id, "message": _message_payload(message, index)})

    @app.get("/api/sessions/{session_id}/messages/{index}")
    async def api_session_message_get(session_id: str, index: int, request: Request) -> JSONResponse:
        _require_request(request, config)
        _store, session = _load_session(session_id)
        if session is None:
            return JSONResponse({"ok": False, "error": "session not found", "id": session_id}, status_code=404)
        if index < 0 or index >= len(session.messages):
            return JSONResponse({"ok": False, "error": "message not found", "index": index}, status_code=404)
        return JSONResponse({"ok": True, "message": _message_payload(session.messages[index], index)})

    @app.patch("/api/sessions/{session_id}/messages/{index}")
    async def api_session_message_patch(session_id: str, index: int, request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        store, session = _load_session(session_id)
        if session is None:
            return JSONResponse({"ok": False, "error": "session not found", "id": session_id}, status_code=404)
        if index < 0 or index >= len(session.messages):
            return JSONResponse({"ok": False, "error": "message not found", "index": index}, status_code=404)
        try:
            session.messages[index] = _patched_message(session.messages[index], body)
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        store.save(session)
        return JSONResponse({"ok": True, "message": _message_payload(session.messages[index], index)})

    @app.delete("/api/sessions/{session_id}/messages/{index}")
    async def api_session_message_delete(session_id: str, index: int, request: Request) -> JSONResponse:
        _require_request(request, config)
        store, session = _load_session(session_id)
        if session is None:
            return JSONResponse({"ok": False, "error": "session not found", "id": session_id}, status_code=404)
        if index < 0 or index >= len(session.messages):
            return JSONResponse({"ok": False, "error": "message not found", "index": index}, status_code=404)
        removed = session.messages.pop(index)
        store.save(session)
        return JSONResponse({"ok": True, "removed": _message_payload(removed, index), "count": len(session.messages)})

    @app.get("/api/sessions/{session_id}/export")
    async def api_session_export(session_id: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        from ..session import SessionStore

        session = SessionStore().load(session_id)
        if session is None:
            return JSONResponse({"ok": False, "error": "session not found", "id": session_id}, status_code=404)
        return JSONResponse(
            _session_export(session),
            headers={"Content-Disposition": f'attachment; filename="{session_id}.json"'},
        )

    @app.delete("/api/sessions/{session_id}")
    async def api_session_delete(session_id: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        from ..session import SessionStore

        ok = SessionStore().delete(session_id)
        return JSONResponse({"ok": ok, "id": session_id}, status_code=200 if ok else 404)

    @app.get("/api/runs/{run_id}/timeline")
    async def api_run_timeline(run_id: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        result = dash._dashboard_run_timeline(run_id, config)
        return JSONResponse(result, status_code=200 if result.get("found") else 404)

    @app.get("/api/background/jobs")
    async def api_background_jobs(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(_background_jobs_payload(config))

    @app.post("/api/background/jobs/{job_id}/cancel")
    async def api_background_job_cancel(job_id: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        return _background_job_cancel_response(job_id)

    @app.post("/api/background/jobs/{job_id}/retry")
    async def api_background_job_retry(job_id: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        return _background_job_retry_response(config, job_id)

