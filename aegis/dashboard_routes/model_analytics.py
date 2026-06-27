"""Dashboard model and analytics compatibility routes."""

from __future__ import annotations

from typing import Any

from ..dashboard_fastapi import (
    JSONResponse,
    Request,
    _api_get,
    _auxiliary_model_payload,
    _model_info_payload,
    _model_options_payload,
    _model_set_payload,
    _query_dict,
    _recommended_default_payload,
    _require_request,
)


def _moa_model_specs(config) -> list[str]:
    raw = config.get("moa.models", []) or []
    if isinstance(raw, str):
        raw = [part.strip() for part in raw.split(",") if part.strip()]
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()][:5]


def _moa_payload(config) -> dict[str, Any]:
    models = _moa_model_specs(config)
    main = {
        "provider": str(config.get("model.provider") or ""),
        "model": str(config.get("model.default") or ""),
    }
    return {
        "ok": True,
        "enabled": len(models) >= 2,
        "models": models,
        "reference_models": [
            {"provider": spec.split("/", 1)[0] if "/" in spec else "", "model": spec}
            for spec in models
        ],
        "aggregator": main,
        "max_tokens": int(config.get("moa.max_tokens", 0) or 0),
    }


def _moa_specs_from_body(body: dict[str, Any]) -> list[str]:
    raw = body.get("models")
    if raw is None:
        raw = body.get("reference_models")
    if isinstance(raw, str):
        return [part.strip() for part in raw.split(",") if part.strip()][:5]
    if not isinstance(raw, list):
        return []
    specs: list[str] = []
    for item in raw:
        if isinstance(item, dict):
            provider = str(item.get("provider") or "").strip()
            model = str(item.get("model") or "").strip()
            spec = f"{provider}/{model}" if provider and model and "/" not in model else model
        else:
            spec = str(item).strip()
        if spec:
            specs.append(spec)
    return specs[:5]


def register(app, config, chat_runner):  # noqa: ARG001
    @app.get("/api/model/info")
    async def api_model_info(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(_model_info_payload(config))

    @app.get("/api/model/options")
    async def api_model_options(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(_model_options_payload(config))

    @app.get("/api/model/recommended-default")
    async def api_model_recommended_default(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(_recommended_default_payload(config, _query_dict(request)))

    @app.get("/api/model/auxiliary")
    async def api_model_auxiliary(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(_auxiliary_model_payload(config))

    @app.post("/api/model/set")
    async def api_model_set(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        payload = _model_set_payload(config, body if isinstance(body, dict) else {})
        return JSONResponse(payload, status_code=200 if payload.get("ok") else 400)

    @app.get("/api/model/moa")
    async def api_model_moa(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(_moa_payload(config))

    @app.put("/api/model/moa")
    async def api_model_moa_update(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        models = _moa_specs_from_body(body if isinstance(body, dict) else {})
        config.data.setdefault("moa", {})["models"] = models
        config.save()
        return JSONResponse({"ok": True, **_moa_payload(config)})

    @app.get("/api/analytics/usage")
    async def api_analytics_usage(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(_api_get("/api/analytics/usage", _query_dict(request), config))

    @app.get("/api/analytics/models")
    async def api_analytics_models(request: Request) -> JSONResponse:
        _require_request(request, config)
        usage = _api_get("/api/analytics/usage", _query_dict(request), config)
        models = usage.get("models") or usage.get("by_model") or []
        return JSONResponse({"ok": True, "models": models, "usage": usage})
