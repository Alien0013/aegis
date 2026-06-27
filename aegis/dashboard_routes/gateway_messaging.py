"""Gateway Messaging dashboard routes — extracted from dashboard_fastapi.create_app.

register() wires this group's handlers onto the shared FastAPI ``app`` (closing over
``config`` + ``chat_runner``, exactly as the original nested routes did). Module-level
deps are imported from :mod:`aegis.dashboard_fastapi`; relative imports inside the
handlers are one level deeper than the original (this module lives one package down).
register_all preserves the original cross-module order so the catch-alls register last.
"""

from __future__ import annotations

from datetime import UTC, datetime

from ..dashboard_fastapi import (
    JSONResponse,
    Request,
    _channel_catalog_map,
    _gateway_channel_payload,
    _gateway_dead_letter_payload,
    _gateway_outbox_action,
    _gateway_outbox_payload,
    _gateway_probe,
    _gateway_service_control,
    _gateway_status,
    _messaging_platform_test,
    _messaging_platform_update,
    _messaging_platforms_payload,
    _normalize_platform_id,
    _platform_registry_payload,
    _require_request,
    _service_result,
    _set_gateway_channel,
)


_GATEWAY_DRAIN_STATE: dict[str, str] = {}


def register(app, config, chat_runner):
    @app.get("/api/gateway/status")
    async def api_gateway_status(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(_gateway_status(config))

    @app.post("/api/gateway/drain")
    async def api_gateway_drain(request: Request) -> JSONResponse:
        _require_request(request, config)
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = {}
        action = str((body if isinstance(body, dict) else {}).get("action") or "drain").strip().lower()
        if action == "cancel":
            was_draining = bool(_GATEWAY_DRAIN_STATE)
            _GATEWAY_DRAIN_STATE.clear()
            return JSONResponse({"ok": True, "action": "cancel", "was_draining": was_draining})
        if action != "drain":
            return JSONResponse({
                "ok": False,
                "error": "unknown drain action; expected 'drain' or 'cancel'",
                "action": action,
            }, status_code=400)
        requested_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        _GATEWAY_DRAIN_STATE.update({"requested_at": requested_at, "principal": "dashboard"})
        return JSONResponse({"ok": True, "action": "drain", "requested_at": requested_at, "draining": True})

    @app.get("/api/gateway/outbox")
    async def api_gateway_outbox(request: Request) -> JSONResponse:
        _require_request(request, config)
        status = str(request.query_params.get("status") or "").strip().lower()
        try:
            limit = max(1, min(200, int(request.query_params.get("limit") or 50)))
        except (TypeError, ValueError):
            limit = 50
        return JSONResponse(_gateway_outbox_payload(status=status, limit=limit))

    @app.get("/api/gateway/dead-letter")
    async def api_gateway_dead_letter(request: Request) -> JSONResponse:
        _require_request(request, config)
        try:
            limit = max(1, min(200, int(request.query_params.get("limit") or 50)))
        except (TypeError, ValueError):
            limit = 50
        return JSONResponse(_gateway_dead_letter_payload(limit=limit))

    @app.post("/api/gateway/outbox/{message_id}/retry")
    async def api_gateway_outbox_retry(message_id: int, request: Request) -> JSONResponse:
        _require_request(request, config)
        payload, status = _gateway_outbox_action(message_id, "retry")
        return JSONResponse(payload, status_code=status)

    @app.post("/api/gateway/outbox/{message_id}/discard")
    async def api_gateway_outbox_discard(message_id: int, request: Request) -> JSONResponse:
        _require_request(request, config)
        payload, status = _gateway_outbox_action(message_id, "discard")
        return JSONResponse(payload, status_code=status)

    @app.post("/api/gateway/dead-letter/{message_id}/retry")
    async def api_gateway_dead_letter_retry(message_id: int, request: Request) -> JSONResponse:
        _require_request(request, config)
        payload, status = _gateway_outbox_action(message_id, "retry")
        return JSONResponse(payload, status_code=status)

    @app.post("/api/gateway/dead-letter/{message_id}/discard")
    async def api_gateway_dead_letter_discard(message_id: int, request: Request) -> JSONResponse:
        _require_request(request, config)
        payload, status = _gateway_outbox_action(message_id, "discard")
        return JSONResponse(payload, status_code=status)

    @app.get("/api/messaging/platforms")
    async def api_messaging_platforms(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(_messaging_platforms_payload(config))

    @app.get("/api/platforms")
    @app.get("/api/platforms/registry")
    @app.get("/api/messaging/platforms/registry")
    async def api_platforms_registry(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(_platform_registry_payload(config))

    @app.get("/api/platforms/{platform_id}")
    @app.get("/api/messaging/platforms/{platform_id}")
    async def api_platform_registry_detail(platform_id: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        safe = _normalize_platform_id(platform_id)
        payload = _platform_registry_payload(config, safe)
        return JSONResponse(payload, status_code=200 if payload.get("ok") else 404)

    @app.put("/api/messaging/platforms/{platform_id}")
    async def api_messaging_platform_update(platform_id: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        payload = _messaging_platform_update(config, platform_id, body if isinstance(body, dict) else {})
        status = 200 if payload.get("ok") else (404 if "unknown" in str(payload.get("error", "")) else 400)
        return JSONResponse(payload, status_code=status)

    @app.post("/api/messaging/platforms/{platform_id}/test")
    async def api_messaging_platform_test(platform_id: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        payload = _messaging_platform_test(config, platform_id)
        status = 200 if payload.get("ok") or payload.get("state") in {"disabled", "not_configured", "error"} else 404
        return JSONResponse(payload, status_code=status)

    @app.get("/api/gateway/channels/catalog")
    async def api_gateway_channels_catalog(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(_gateway_channel_payload(config))

    @app.get("/api/gateway/channels/{channel}")
    async def api_gateway_channel_get(channel: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        payload = _gateway_channel_payload(config, channel)
        return JSONResponse(payload, status_code=200 if payload.get("ok") else 404)

    @app.patch("/api/gateway/channels/{channel}")
    async def api_gateway_channel_patch(channel: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        payload = _set_gateway_channel(config, channel, body if isinstance(body, dict) else {})
        return JSONResponse(payload, status_code=200 if payload.get("ok") else 400)

    @app.post("/api/gateway/channels/{channel}/probe")
    async def api_gateway_channel_probe(channel: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        safe = _normalize_platform_id(channel)
        if safe not in _channel_catalog_map(config):
            return JSONResponse({"ok": False, "error": "unknown channel", "channel": safe}, status_code=404)
        return JSONResponse(_gateway_probe({"channel": safe}))

    @app.post("/api/gateway/channels")
    async def api_gateway_channels(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        channels = body.get("channels", [])
        if isinstance(channels, str):
            channels = [c.strip() for c in channels.split(",") if c.strip()]
        if not isinstance(channels, list):
            return JSONResponse({"ok": False, "error": "channels must be a list or comma string"}, status_code=400)
        catalog = _channel_catalog_map(config)
        canonical: list[str] = []
        unknown: list[str] = []
        for item in channels:
            raw = str(item).strip()
            if not raw:
                continue
            safe = _normalize_platform_id(raw)
            if safe not in catalog:
                unknown.append(raw)
                continue
            if safe not in canonical:
                canonical.append(safe)
        if unknown:
            return JSONResponse({"ok": False, "error": "unknown channel", "channels": unknown}, status_code=400)
        config.data.setdefault("gateway", {})["channels"] = canonical
        config.save()
        return JSONResponse({"ok": True, "gateway": _gateway_status(config)})

    @app.post("/api/gateway/start")
    async def api_gateway_start(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(_gateway_service_control("start"))

    @app.post("/api/gateway/stop")
    async def api_gateway_stop(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(_gateway_service_control("stop"))

    @app.post("/api/gateway/restart")
    async def api_gateway_restart(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(_gateway_service_control("restart"))

    @app.post("/api/gateway/service")
    async def api_gateway_service(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        action = str(body.get("action") or "status")
        from ..daemon import (
            gateway_service_status,
            install_gateway_service,
            remove_gateway_service,
        )

        if action == "status":
            return JSONResponse({"ok": True, "service": "aegis-gateway.service", "status": gateway_service_status()})
        if action == "install":
            channels = body.get("channels") or config.get("gateway.channels", []) or []
            if isinstance(channels, str):
                channels = [c.strip() for c in channels.split(",") if c.strip()]
            return JSONResponse(_service_result(install_gateway_service(
                config,
                [str(c).strip() for c in channels if str(c).strip()],
                enable_now=not bool(body.get("no_start", False)),
            )))
        if action == "remove":
            return JSONResponse(_service_result(remove_gateway_service()))
        if action in {"start", "stop", "restart"}:
            return JSONResponse(_gateway_service_control(action))
        return JSONResponse({"ok": False, "error": f"unknown gateway service action: {action}"}, status_code=400)

    @app.api_route(
        "/api/plugins/{plugin_name}/{plugin_path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    )
    async def api_plugin_api_missing(plugin_name: str, plugin_path: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse({
            "ok": False,
            "plugin": plugin_name,
            "path": plugin_path,
            "error": "dashboard plugin API not mounted",
        }, status_code=404)

