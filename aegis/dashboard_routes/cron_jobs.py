"""Cron Jobs dashboard routes — extracted from dashboard_fastapi.create_app.

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
    _cron_blueprint_instantiate_response,
    _cron_blueprints_response,
    _cron_delivery_targets,
    _cron_fire_response,
    _cron_job_create_response,
    _cron_job_delete_response,
    _cron_job_detail,
    _cron_job_enabled_response,
    _cron_job_invalid_id_response,
    _cron_job_patch_response,
    _cron_job_preview_response,
    _cron_job_put_response,
    _cron_job_run_response,
    _cron_job_runs_response,
    _cron_jobs_response,
    _cron_request_profile,
    _query_dict,
    _require_request,
    _service_result,
)


def register(app, config, chat_runner):
    @app.get("/api/cron/jobs")
    async def api_cron_jobs(request: Request) -> JSONResponse:
        _require_request(request, config)
        return _cron_jobs_response(request.query_params.get("profile"))

    @app.get("/api/cron/blueprints")
    async def api_cron_blueprints(request: Request) -> JSONResponse:
        _require_request(request, config)
        return _cron_blueprints_response()

    @app.post("/api/cron/blueprints/instantiate")
    async def api_cron_blueprint_instantiate(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        if not isinstance(body, dict):
            return JSONResponse({"ok": False, "error": "body must be an object"}, status_code=400)
        if "profile" not in body and request.query_params.get("profile"):
            body["profile"] = request.query_params.get("profile")
        return _cron_blueprint_instantiate_response(config, body)

    @app.post("/api/cron/jobs")
    async def api_cron_job_create(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        return _cron_job_create_response(config, body, profile=_cron_request_profile(request, body))

    @app.post("/api/cron/fire")
    async def api_cron_fire(request: Request) -> JSONResponse:
        _require_request(request, config)
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = {}
        payload = body if isinstance(body, dict) else {}
        return _cron_fire_response(config, payload, profile=_cron_request_profile(request, payload))

    @app.get("/api/cron/delivery-targets")
    async def api_cron_delivery_targets(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(_cron_delivery_targets(config))

    @app.get("/api/cron/jobs/{job_id}")
    async def api_cron_job_detail(job_id: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        invalid = _cron_job_invalid_id_response(job_id, request)
        if invalid is not None:
            return invalid
        detail = _cron_job_detail(job_id, profile=_cron_request_profile(request))
        return JSONResponse(detail, status_code=200 if detail.get("found") else 404)

    @app.patch("/api/cron/jobs/{job_id}")
    async def api_cron_job_patch(job_id: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        return _cron_job_patch_response(job_id, body, request, profile=_cron_request_profile(request, body))

    @app.put("/api/cron/jobs/{job_id}")
    async def api_cron_job_put(job_id: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        return _cron_job_put_response(
            job_id,
            body if isinstance(body, dict) else {},
            request,
            profile=_cron_request_profile(request, body if isinstance(body, dict) else {}),
        )

    @app.delete("/api/cron/jobs/{job_id}")
    async def api_cron_job_delete(job_id: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        return _cron_job_delete_response(job_id, request, profile=_cron_request_profile(request))

    @app.get("/api/cron/jobs/{job_id}/runs")
    async def api_cron_job_runs(job_id: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        return _cron_job_runs_response(job_id, _query_dict(request), request, profile=_cron_request_profile(request))

    @app.get("/api/cron/jobs/{job_id}/preview")
    async def api_cron_job_preview(job_id: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        return _cron_job_preview_response(config, job_id, request, profile=_cron_request_profile(request))

    @app.post("/api/cron/jobs/{job_id}/dry-run")
    async def api_cron_job_dry_run(job_id: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        return _cron_job_preview_response(config, job_id, request, profile=_cron_request_profile(request))

    @app.post("/api/cron/jobs/{job_id}/run")
    async def api_cron_job_run(job_id: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        return _cron_job_run_response(config, job_id, request, profile=_cron_request_profile(request))

    @app.post("/api/cron/jobs/{job_id}/trigger")
    async def api_cron_job_trigger(job_id: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        return _cron_job_run_response(config, job_id, request, profile=_cron_request_profile(request))

    @app.post("/api/cron/jobs/{job_id}/pause")
    async def api_cron_job_pause(job_id: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        return _cron_job_enabled_response(job_id, False, request, profile=_cron_request_profile(request))

    @app.post("/api/cron/jobs/{job_id}/resume")
    async def api_cron_job_resume(job_id: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        return _cron_job_enabled_response(job_id, True, request, profile=_cron_request_profile(request))

    @app.get("/api/jobs")
    async def api_jobs(request: Request) -> JSONResponse:
        _require_request(request, config)
        return _cron_jobs_response(request.query_params.get("profile"))

    @app.get("/api/jobs/blueprints")
    async def api_jobs_blueprints(request: Request) -> JSONResponse:
        _require_request(request, config)
        return _cron_blueprints_response()

    @app.post("/api/jobs/blueprints/instantiate")
    async def api_jobs_blueprint_instantiate(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        if not isinstance(body, dict):
            return JSONResponse({"ok": False, "error": "body must be an object"}, status_code=400)
        if "profile" not in body and request.query_params.get("profile"):
            body["profile"] = request.query_params.get("profile")
        return _cron_blueprint_instantiate_response(config, body)

    @app.post("/api/jobs")
    async def api_job_create(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        return _cron_job_create_response(config, body, profile=_cron_request_profile(request, body))

    @app.post("/api/jobs/fire")
    async def api_jobs_fire(request: Request) -> JSONResponse:
        _require_request(request, config)
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = {}
        payload = body if isinstance(body, dict) else {}
        return _cron_fire_response(config, payload, profile=_cron_request_profile(request, payload))

    @app.get("/api/jobs/{job_id}")
    async def api_job_detail(job_id: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        invalid = _cron_job_invalid_id_response(job_id, request)
        if invalid is not None:
            return invalid
        detail = _cron_job_detail(job_id, profile=_cron_request_profile(request))
        return JSONResponse(detail, status_code=200 if detail.get("found") else 404)

    @app.patch("/api/jobs/{job_id}")
    async def api_job_patch(job_id: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        return _cron_job_patch_response(job_id, body, request, profile=_cron_request_profile(request, body))

    @app.delete("/api/jobs/{job_id}")
    async def api_job_delete(job_id: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        return _cron_job_delete_response(job_id, request, profile=_cron_request_profile(request))

    @app.post("/api/jobs/{job_id}/pause")
    async def api_job_pause(job_id: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        return _cron_job_enabled_response(job_id, False, request, profile=_cron_request_profile(request))

    @app.post("/api/jobs/{job_id}/resume")
    async def api_job_resume(job_id: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        return _cron_job_enabled_response(job_id, True, request, profile=_cron_request_profile(request))

    @app.get("/api/jobs/{job_id}/preview")
    async def api_job_preview(job_id: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        return _cron_job_preview_response(config, job_id, request, profile=_cron_request_profile(request))

    @app.post("/api/jobs/{job_id}/dry-run")
    async def api_job_dry_run(job_id: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        return _cron_job_preview_response(config, job_id, request, profile=_cron_request_profile(request))

    @app.get("/api/jobs/{job_id}/runs")
    async def api_job_runs(job_id: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        return _cron_job_runs_response(job_id, _query_dict(request), request, profile=_cron_request_profile(request))

    @app.post("/api/jobs/{job_id}/run")
    async def api_job_run(job_id: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        return _cron_job_run_response(config, job_id, request, profile=_cron_request_profile(request))

    @app.post("/api/jobs/{job_id}/trigger")
    async def api_job_trigger(job_id: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        return _cron_job_run_response(config, job_id, request, profile=_cron_request_profile(request))

    @app.get("/api/cron/service")
    async def api_cron_service_get(request: Request) -> JSONResponse:
        _require_request(request, config)
        from ..daemon import cron_service_status

        return JSONResponse({"service": "aegis-cron.service", "status": cron_service_status()})

    @app.post("/api/cron/service")
    async def api_cron_service_post(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        action = str(body.get("action") or "status")
        from ..daemon import control_cron_service, cron_service_status, install_cron_service, remove_cron_service

        if action == "status":
            return JSONResponse({"ok": True, "service": "aegis-cron.service", "status": cron_service_status()})
        if action == "install":
            return JSONResponse(_service_result(install_cron_service(
                config,
                enable_now=not bool(body.get("no_start", False)),
            )))
        if action == "remove":
            return JSONResponse(_service_result(remove_cron_service()))
        if action in {"start", "stop", "restart"}:
            return JSONResponse(_service_result(control_cron_service(action)))
        return JSONResponse({"ok": False, "error": f"unknown cron service action: {action}"}, status_code=400)

