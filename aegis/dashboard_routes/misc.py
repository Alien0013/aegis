"""Misc dashboard routes — extracted from dashboard_fastapi.create_app.

register() wires this group's handlers onto the shared FastAPI ``app`` (closing over
``config`` + ``chat_runner``, exactly as the original nested routes did). Module-level
deps are imported from :mod:`aegis.dashboard_fastapi`; relative imports inside the
handlers are one level deeper than the original (this module lives one package down).
register_all preserves the original cross-module order so the catch-alls register last.
"""

from __future__ import annotations

import mimetypes

from fastapi.responses import FileResponse

from ..dashboard_fastapi import (
    HTTPException,
    JSONResponse,
    Path,
    Request,
    _admin_status_payload,
    _api_get,
    _credential_pools_payload,
    _dashboard_action_catalog,
    _delete_managed_file,
    _hook_test_payload,
    _observability_contract_payload,
    _portal_status_payload,
    _query_dict,
    _require_request,
    _safe_resource_name,
    dash,
    json,
)


def register(app, config, chat_runner):
    @app.get("/api/status")
    async def api_status(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(dash._dashboard_status(config))

    @app.get("/api/system/stats")
    async def api_system_stats(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(dash._system_stats())

    @app.get("/api/logs")
    async def api_logs(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(_api_get("/api/logs", _query_dict(request), config))

    @app.get("/api/media")
    async def api_media(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(_api_get("/api/media", _query_dict(request), config))

    @app.get("/api/files/download")
    async def download_file(request: Request) -> FileResponse:
        _require_request(request, config)
        raw = (request.query_params.get("path") or "").strip()
        if not raw:
            raise HTTPException(status_code=400, detail="missing path")
        try:
            target = Path(raw).expanduser().resolve()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail="bad path") from exc
        if not target.is_file():
            raise HTTPException(status_code=404, detail="not a file")
        if dash._is_sensitive_path(target):
            raise HTTPException(
                status_code=403,
                detail="blocked: refusing to download a credential/key/SSH path through the dashboard.",
            )
        media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        return FileResponse(target, media_type=media_type, filename=target.name)

    @app.delete("/api/files")
    async def api_files_delete(request: Request) -> JSONResponse:
        _require_request(request, config)
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = {}
        return JSONResponse(_delete_managed_file(body if isinstance(body, dict) else {}))

    @app.get("/api/credentials/pools")
    async def api_credentials_pools(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(_credential_pools_payload(config))

    @app.get("/api/credentials/pools/{provider}")
    async def api_credentials_pool_detail(provider: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        name = _safe_resource_name(provider, "provider")
        payload = _credential_pools_payload(config, name)
        return JSONResponse(payload, status_code=200 if payload.get("ok") else 404)

    @app.get("/api/credential-pools/status")
    async def api_credential_pools_status(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(_credential_pools_payload(config))

    @app.get("/api/update/check")
    @app.get("/api/hermes/update/check")
    @app.get("/api/portal/update/check")
    @app.get("/api/check/update")
    async def api_update_check(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(dash._update_check())

    @app.post("/api/update/check")
    @app.post("/api/portal/update/check")
    @app.post("/api/check/update")
    async def api_update_check_post(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(dash._update_check())

    @app.post("/api/hermes/update")
    async def api_hermes_update(request: Request) -> JSONResponse:
        _require_request(request, config)
        update = dash._update_check()
        return JSONResponse({"ok": True, **update})

    @app.get("/api/curator")
    async def api_curator_status(request: Request) -> JSONResponse:
        _require_request(request, config)
        from ..curator import apply_transitions

        return JSONResponse(apply_transitions(dry_run=True))

    @app.post("/api/curator/run")
    async def api_curator_run(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(dash._ops_action("curator_run", {}, config))

    @app.put("/api/curator/paused")
    async def api_curator_paused(request: Request) -> JSONResponse:
        _require_request(request, config)
        raw = await request.body()
        try:
            body = json.loads(raw) if raw else {}
        except ValueError:
            body = {}
        paused = bool(body.get("paused")) if isinstance(body, dict) else False
        enabled = not paused
        config.set("curator.enabled", enabled)
        return JSONResponse({"ok": True, "paused": paused, "enabled": enabled})

    @app.get("/api/portal")
    @app.get("/api/portal/status")
    async def api_portal_status(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(_portal_status_payload(config))

    @app.get("/api/actions/status")
    @app.get("/api/admin/actions/status")
    async def api_actions_status(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(_dashboard_action_catalog())

    @app.get("/api/admin/status")
    async def api_admin_status(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(_admin_status_payload(config))

    @app.post("/api/actions/run")
    @app.post("/api/admin/actions/run")
    async def api_actions_run(request: Request) -> JSONResponse:
        _require_request(request, config)
        raw = await request.body()
        try:
            body = json.loads(raw) if raw else {}
        except ValueError:
            body = {}
        body = body if isinstance(body, dict) else {}
        action = str(body.get("action") or body.get("id") or body.get("name") or "")
        return JSONResponse(dash._ops_action(action, body, config))

    @app.get("/api/hooks")
    @app.get("/api/hooks/contract")
    @app.get("/api/observability")
    @app.get("/api/observability/contract")
    @app.get("/api/observability/events")
    @app.get("/api/observability/hooks")
    async def api_observability_contract(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(_observability_contract_payload(config))

    @app.post("/api/hooks/test")
    @app.post("/api/observability/hooks/test")
    async def api_observability_hook_test(request: Request) -> JSONResponse:
        _require_request(request, config)
        raw = await request.body()
        try:
            body = json.loads(raw) if raw else {}
        except ValueError:
            body = {}
        return JSONResponse(_hook_test_payload(config, body if isinstance(body, dict) else {}))

