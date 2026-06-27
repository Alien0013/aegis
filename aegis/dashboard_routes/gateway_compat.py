"""Dashboard gateway-adjacent compatibility routes."""

from __future__ import annotations

from typing import Any

from ..dashboard_fastapi import (
    HTTPException,
    JSONResponse,
    Request,
    _credential_pools_payload,
    _dashboard_action_catalog,
    _require_request,
    _safe_resource_name,
)


def _safe_name(value: str, kind: str) -> str:
    try:
        return _safe_resource_name(value, kind)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


async def _json_body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return {}
    return body if isinstance(body, dict) else {}


def _reset_credential_pool_cache() -> None:
    try:
        from ..credentials import reset

        reset()
    except Exception:  # noqa: BLE001
        pass


def _webhook_summary(hook) -> dict[str, str]:
    return {"name": hook.name, "prompt": hook.prompt}


def register(app, config, chat_runner):  # noqa: ARG001
    @app.get("/api/credentials/pool")
    async def api_credentials_pool_alias(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(_credential_pools_payload(config))

    @app.post("/api/credentials/pool")
    async def api_credentials_pool_add(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await _json_body(request)
        provider = _safe_name(str(body.get("provider") or body.get("name") or ""), "provider")
        key = str(body.get("key") or body.get("api_key") or body.get("token") or "").strip()
        if not key:
            return JSONResponse({"ok": False, "error": "key is required", "provider": provider}, status_code=400)
        pools = config.data.setdefault("credential_pools", {})
        node = pools.setdefault(provider, {})
        keys = list(node.get("keys") or [])
        keys.append(key)
        node["keys"] = keys
        if body.get("strategy"):
            node["strategy"] = str(body.get("strategy"))
        config.save()
        _reset_credential_pool_cache()
        payload = _credential_pools_payload(config)
        return JSONResponse({"ok": True, "provider": provider, "count": len(keys), **payload})

    @app.delete("/api/credentials/pool/{provider}/{index}")
    async def api_credentials_pool_delete(provider: str, index: int, request: Request) -> JSONResponse:
        _require_request(request, config)
        safe_provider = _safe_name(provider, "provider")
        pools = config.data.setdefault("credential_pools", {})
        node = pools.get(safe_provider)
        keys = list((node or {}).get("keys") or []) if isinstance(node, dict) else []
        pos = int(index) - 1
        if pos < 0 or pos >= len(keys):
            return JSONResponse(
                {"ok": False, "error": "credential pool entry not found", "provider": safe_provider},
                status_code=404,
            )
        keys.pop(pos)
        node["keys"] = keys
        config.save()
        _reset_credential_pool_cache()
        payload = _credential_pools_payload(config)
        return JSONResponse({"ok": True, "provider": safe_provider, "count": len(keys), **payload})

    @app.get("/api/actions/{name}/status")
    async def api_action_status(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        safe_name = _safe_name(name, "action")
        catalog = _dashboard_action_catalog()
        action = next((row for row in catalog.get("actions", []) if row.get("id") == safe_name), None)
        if action is None:
            return JSONResponse({"ok": False, "name": safe_name, "action": None}, status_code=404)
        return JSONResponse({"ok": True, "name": safe_name, "action": action})

    @app.get("/api/pairing")
    async def api_pairing_list(request: Request) -> JSONResponse:
        _require_request(request, config)
        from ..gateway.pairing import PairingStore

        return JSONResponse(PairingStore().list())

    @app.post("/api/pairing/approve")
    async def api_pairing_approve(request: Request) -> JSONResponse:
        _require_request(request, config)
        from ..gateway.pairing import PairingStore

        body = await _json_body(request)
        platform = str(body.get("platform") or "").strip().lower()
        code = str(body.get("code") or body.get("user_id") or body.get("user") or "").strip()
        if not platform or not code:
            return JSONResponse({"ok": False, "error": "platform and code are required"}, status_code=400)
        return JSONResponse({"ok": PairingStore().approve(platform, code), "platform": platform})

    @app.post("/api/pairing/revoke")
    async def api_pairing_revoke(request: Request) -> JSONResponse:
        _require_request(request, config)
        from ..gateway.pairing import PairingStore

        body = await _json_body(request)
        platform = str(body.get("platform") or "").strip().lower()
        user_id = str(body.get("user_id") or body.get("user") or body.get("code") or "").strip()
        if not platform or not user_id:
            return JSONResponse({"ok": False, "error": "platform and user_id are required"}, status_code=400)
        return JSONResponse({"ok": PairingStore().revoke(platform, user_id), "platform": platform, "user_id": user_id})

    @app.post("/api/pairing/clear-pending")
    async def api_pairing_clear_pending(request: Request) -> JSONResponse:
        _require_request(request, config)
        from ..gateway.pairing import PairingStore

        return JSONResponse({"ok": True, "cleared": PairingStore().clear_pending()})

    @app.get("/api/webhooks")
    async def api_webhooks_list(request: Request) -> JSONResponse:
        _require_request(request, config)
        from ..webhook import WebhookStore

        return JSONResponse([_webhook_summary(hook) for hook in WebhookStore().list()])

    @app.post("/api/webhooks/enable")
    async def api_webhooks_enable(request: Request) -> JSONResponse:
        _require_request(request, config)
        config.set("webhook.enabled", True)
        return JSONResponse({"ok": True, "enabled": True})

    @app.post("/api/webhooks")
    async def api_webhooks_create(request: Request) -> JSONResponse:
        _require_request(request, config)
        from ..webhook import WebhookStore

        body = await _json_body(request)
        name = _safe_name(str(body.get("name") or ""), "webhook")
        action = str(body.get("action") or "").strip().lower()
        if action == "remove":
            ok = WebhookStore().remove(name)
            return JSONResponse({"ok": ok, "name": name}, status_code=200 if ok else 404)
        prompt = str(body.get("prompt") or body.get("template") or "").strip()
        if not prompt:
            return JSONResponse({"ok": False, "error": "prompt is required", "name": name}, status_code=400)
        hook = WebhookStore().add(
            name,
            prompt,
            secret=str(body.get("secret") or ""),
            deliver=str(body.get("deliver") or ""),
            events=body.get("events") if isinstance(body.get("events"), list) else None,
            skills=body.get("skills") if isinstance(body.get("skills"), list) else None,
            deliver_only=bool(body.get("deliver_only", False)),
        )
        return JSONResponse({"ok": True, "webhook": _webhook_summary(hook)})

    @app.delete("/api/webhooks/{name}")
    async def api_webhooks_delete(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        from ..webhook import WebhookStore

        safe_name = _safe_name(name, "webhook")
        ok = WebhookStore().remove(safe_name)
        return JSONResponse({"ok": ok, "name": safe_name}, status_code=200 if ok else 404)

    @app.put("/api/webhooks/{name}/enabled")
    async def api_webhooks_enabled(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await _json_body(request)
        enabled = bool(body.get("enabled"))
        return JSONResponse({"ok": True, "name": _safe_name(name, "webhook"), "enabled": enabled})
