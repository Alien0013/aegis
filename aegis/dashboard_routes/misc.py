"""Misc dashboard routes — extracted from dashboard_fastapi.create_app.

register() wires this group's handlers onto the shared FastAPI ``app`` (closing over
``config`` + ``chat_runner``, exactly as the original nested routes did). Module-level
deps are imported from :mod:`aegis.dashboard_fastapi`; relative imports inside the
handlers are one level deeper than the original (this module lives one package down).
register_all preserves the original cross-module order so the catch-alls register last.
"""

from __future__ import annotations

import mimetypes
from dataclasses import asdict

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

    @app.post("/api/update")
    async def api_update(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse({"ok": True, **dash._update_check()})

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

    def _ops_action_response(action: str, body: dict | None = None) -> JSONResponse:
        payload = dash._ops_action(action, body or {}, config)
        return JSONResponse(payload, status_code=200 if payload.get("ok", True) else 400)

    async def _request_body(request: Request) -> dict:
        raw = await request.body()
        try:
            body = json.loads(raw) if raw else {}
        except ValueError:
            body = {}
        return body if isinstance(body, dict) else {}

    def _ops_hooks_payload() -> dict:
        from .. import hooks

        return {"ok": True, "events": list(hooks.EVENTS), "hooks": hooks.list_hooks(config)}

    def _ops_hook_event(event: str) -> tuple[str, str]:
        from .. import hooks

        safe = str(event or "").strip()
        if safe not in hooks.EVENTS:
            return "", "unknown hook event"
        return safe, ""

    @app.get("/api/ops/hooks")
    async def api_ops_hooks(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(_ops_hooks_payload())

    @app.post("/api/ops/hooks")
    async def api_ops_hooks_add(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await _request_body(request)
        event, error = _ops_hook_event(str(body.get("event") or ""))
        command = str(body.get("command") or "").strip()
        if error or not command:
            return JSONResponse({"ok": False, "error": error or "command is required"}, status_code=400)
        commands = config.get(f"hooks.{event}", []) or []
        if isinstance(commands, str):
            commands = [commands]
        rows = [str(row) for row in commands]
        if command not in rows:
            rows.append(command)
        config.set(f"hooks.{event}", rows)
        return JSONResponse({"ok": True, "event": event, "command": command, **_ops_hooks_payload()})

    @app.delete("/api/ops/hooks")
    async def api_ops_hooks_delete(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await _request_body(request)
        event, error = _ops_hook_event(str(body.get("event") or ""))
        if error:
            return JSONResponse({"ok": False, "error": error}, status_code=400)
        command = str(body.get("command") or "").strip()
        commands = config.get(f"hooks.{event}", []) or []
        if isinstance(commands, str):
            commands = [commands]
        rows = [str(row) for row in commands]
        if command:
            kept = [row for row in rows if row != command]
        else:
            kept = []
        removed = len(rows) - len(kept)
        config.set(f"hooks.{event}", kept)
        return JSONResponse({"ok": True, "event": event, "removed": removed, **_ops_hooks_payload()})

    @app.get("/api/ops/checkpoints")
    async def api_ops_checkpoints(request: Request) -> JSONResponse:
        _require_request(request, config)
        from ..checkpoints import CheckpointStore

        rows = [asdict(row) for row in CheckpointStore().list()]
        return JSONResponse({"ok": True, "checkpoints": rows, "sessions": rows, "count": len(rows)})

    @app.post("/api/ops/checkpoints/prune")
    async def api_ops_checkpoints_prune(request: Request) -> JSONResponse:
        _require_request(request, config)
        from ..checkpoints import CheckpointStore

        removed = CheckpointStore().clear()
        return JSONResponse({"ok": True, "removed": removed})

    @app.post("/api/ops/backup")
    async def api_ops_backup(request: Request) -> JSONResponse:
        _require_request(request, config)
        return _ops_action_response("backup", await _request_body(request))

    @app.post("/api/ops/doctor")
    async def api_ops_doctor(request: Request) -> JSONResponse:
        _require_request(request, config)
        return _ops_action_response("doctor", await _request_body(request))

    @app.post("/api/ops/security-audit")
    async def api_ops_security_audit(request: Request) -> JSONResponse:
        _require_request(request, config)
        return _ops_action_response("security_audit", await _request_body(request))

    @app.post("/api/ops/config-migrate")
    async def api_ops_config_migrate(request: Request) -> JSONResponse:
        _require_request(request, config)
        return _ops_action_response("config_migrate", await _request_body(request))

    @app.post("/api/ops/debug-share")
    async def api_ops_debug_share(request: Request) -> JSONResponse:
        _require_request(request, config)
        return _ops_action_response("debug_share", await _request_body(request))

    @app.post("/api/ops/dump")
    async def api_ops_dump(request: Request) -> JSONResponse:
        _require_request(request, config)
        return _ops_action_response("dump", await _request_body(request))

    @app.post("/api/ops/import")
    async def api_ops_import(request: Request) -> JSONResponse:
        _require_request(request, config)
        return _ops_action_response("import", await _request_body(request))

    @app.post("/api/ops/prompt-size")
    async def api_ops_prompt_size(request: Request) -> JSONResponse:
        _require_request(request, config)
        return _ops_action_response("prompt_size", await _request_body(request))

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

