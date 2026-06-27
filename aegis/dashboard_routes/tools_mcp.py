"""Tools Mcp dashboard routes — extracted from dashboard_fastapi.create_app.

register() wires this group's handlers onto the shared FastAPI ``app`` (closing over
``config`` + ``chat_runner``, exactly as the original nested routes did). Module-level
deps are imported from :mod:`aegis.dashboard_fastapi`; relative imports inside the
handlers are one level deeper than the original (this module lives one package down).
register_all preserves the original cross-module order so the catch-alls register last.
"""

from __future__ import annotations

import tempfile
from typing import Annotated, Any

from fastapi import File, Form, UploadFile

from ..dashboard_fastapi import (
    JSONResponse,
    Path,
    Request,
    _mcp_catalog_install_response,
    _mcp_servers,
    _mcp_spec_from_body,
    _require_request,
    _safe_resource_name,
    _save_mcp_servers,
    _voice_tool_context,
    dash,
    json,
)


def _memory_builtin_file_sizes() -> dict[str, int]:
    from ..memory import MemoryStore

    store = MemoryStore()
    out: dict[str, int] = {}
    for target in ("memory", "user"):
        try:
            out[target] = store._path(target).stat().st_size
        except OSError:
            out[target] = 0
    return out


def _memory_payload(config) -> dict[str, Any]:
    from ..memory_providers import memory_provider_report

    base = dash._dashboard_memory_payload()
    report = memory_provider_report(config)
    catalog = report.get("provider_catalog", [])
    return {
        **base,
        "active": report.get("provider", ""),
        "provider": report.get("provider", ""),
        "providers": catalog,
        "provider_catalog": catalog,
        "builtin_files": _memory_builtin_file_sizes(),
        "config_schema": report.get("config_schema", {}),
    }


def _memory_provider_config_payload(name: str, config) -> tuple[dict[str, Any], int]:
    from ..memory_providers import memory_provider_config_schema, memory_provider_metadata

    schema = memory_provider_config_schema(name)
    if not schema.get("known"):
        return {"name": schema.get("name", name), "label": name, "fields": [], "properties": {}}, 404
    meta = memory_provider_metadata(name)
    properties = dict(schema.get("properties", {}) or {})
    values: dict[str, Any] = {}
    fields: list[dict[str, Any]] = []
    for key, spec in properties.items():
        if key == "memory.provider":
            value = name
        else:
            value = config.get(key, spec.get("default"))
        if spec.get("secret") or spec.get("secret_values"):
            redacted = {"configured": value not in (None, "", {}, [])}
            values[key] = redacted
            field_value = redacted
        else:
            values[key] = value
            field_value = value
        fields.append({"key": key, "value": field_value, **spec})
    return {
        "name": schema.get("name", name),
        "label": meta.get("display_name") or schema.get("name", name),
        "fields": fields,
        "properties": properties,
        "values": values,
        "schema": schema,
    }, 200


def _memory_provider_values(body: dict[str, Any]) -> dict[str, Any]:
    values = body.get("values", body)
    return values if isinstance(values, dict) else {}


def register(app, config, chat_runner):
    @app.get("/api/tools/toolsets")
    async def api_toolsets(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(dash._dashboard_toolsets(config))

    @app.get("/api/tools/inventory")
    async def api_tools_inventory(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(dash._dashboard_tool_inventory(config))

    @app.get("/api/tools/validation")
    async def api_tools_validation(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(dash._dashboard_tool_schema_validation(config))

    @app.post("/api/tools/permission-dry-run")
    async def api_tools_permission_dry_run(request: Request) -> JSONResponse:
        _require_request(request, config)
        raw = await request.body()
        try:
            body = json.loads(raw) if raw else {}
        except ValueError:
            body = {}
        if not isinstance(body, dict):
            body = {}
        result = dash._dashboard_tool_permission_dry_run(body, config)
        return JSONResponse(result, status_code=200 if result.get("ok") else 400)

    @app.post("/api/security/policy-simulate")
    async def api_security_policy_simulate(request: Request) -> JSONResponse:
        _require_request(request, config)
        raw = await request.body()
        try:
            body = json.loads(raw) if raw else {}
        except ValueError:
            body = {}
        if not isinstance(body, dict):
            body = {}
        result = dash._dashboard_security_policy_simulator(body, config)
        return JSONResponse(result, status_code=200 if result.get("ok") else 400)

    @app.put("/api/tools/toolsets/{name}")
    async def api_toolset_toggle(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        result = dash._dashboard_toolset_toggle(
            {"toolset": name, "enabled": bool(body.get("enabled"))},
            config,
        )
        return JSONResponse(
            {**result, "toolsets_detail": dash._dashboard_toolsets(config)},
            status_code=200 if result.get("ok") else 400,
        )

    @app.get("/api/mcp/servers")
    async def api_mcp_servers(request: Request) -> JSONResponse:
        _require_request(request, config)
        live = str(request.query_params.get("live") or "").lower() in {"1", "true", "yes"}
        return JSONResponse(dash._dashboard_mcp_catalog(config, live=live))

    @app.get("/api/mcp/catalog")
    async def api_mcp_catalog(request: Request) -> JSONResponse:
        _require_request(request, config)
        live = str(request.query_params.get("live") or "").lower() in {"1", "true", "yes"}
        return JSONResponse(dash._dashboard_mcp_catalog(config, live=live))

    @app.post("/api/mcp/servers")
    async def api_mcp_server_create(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        try:
            name = _safe_resource_name(str((body or {}).get("name") or ""), "mcp server")
            servers = _mcp_servers(config)
            if name in servers and not bool((body or {}).get("force", False)):
                return JSONResponse({"ok": False, "error": "server already exists"}, status_code=409)
            servers[name] = _mcp_spec_from_body(body if isinstance(body, dict) else {})
            _save_mcp_servers(config, servers)
            return JSONResponse({"ok": True, "name": name, **dash._dashboard_mcp_catalog(config)})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.post("/api/mcp/catalog/{name}/install")
    async def api_mcp_catalog_install(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        return _mcp_catalog_install_response(config, name)

    @app.post("/api/mcp/catalog/install")
    async def api_mcp_catalog_install_body(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        name = str((body if isinstance(body, dict) else {}).get("name") or "").strip()
        if not name:
            return JSONResponse({"ok": False, "error": "missing name"}, status_code=400)
        return _mcp_catalog_install_response(config, name)

    @app.get("/api/mcp/servers/{name}")
    async def api_mcp_server_detail(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        safe = _safe_resource_name(name, "mcp server")
        live = str(request.query_params.get("live") or "").lower() in {"1", "true", "yes"}
        payload = dash._dashboard_mcp_catalog(config, live=live)
        match = next((row for row in payload.get("servers", []) if row.get("name") == safe), None)
        return JSONResponse({"ok": bool(match), "server": match}, status_code=200 if match else 404)

    @app.patch("/api/mcp/servers/{name}")
    async def api_mcp_server_patch(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        try:
            safe = _safe_resource_name(name, "mcp server")
            servers = _mcp_servers(config)
            if safe not in servers:
                return JSONResponse({"ok": False, "error": "server not found"}, status_code=404)
            servers[safe] = _mcp_spec_from_body(body if isinstance(body, dict) else {}, servers[safe])
            _save_mcp_servers(config, servers)
            return JSONResponse({"ok": True, "name": safe, **dash._dashboard_mcp_catalog(config)})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.put("/api/mcp/servers/{name}/enabled")
    async def api_mcp_server_enabled(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        try:
            safe = _safe_resource_name(name, "mcp server")
            servers = _mcp_servers(config)
            if safe not in servers:
                return JSONResponse({"ok": False, "error": "server not found"}, status_code=404)
            enabled = bool((body if isinstance(body, dict) else {}).get("enabled"))
            servers[safe] = _mcp_spec_from_body({"enabled": enabled}, servers[safe])
            _save_mcp_servers(config, servers)
            return JSONResponse({"ok": True, "name": safe, "enabled": enabled, **dash._dashboard_mcp_catalog(config)})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.delete("/api/mcp/servers/{name}")
    async def api_mcp_server_delete(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        safe = _safe_resource_name(name, "mcp server")
        servers = _mcp_servers(config)
        ok = safe in servers
        if ok:
            servers.pop(safe, None)
            _save_mcp_servers(config, servers)
        return JSONResponse({"ok": ok, "name": safe, **dash._dashboard_mcp_catalog(config)}, status_code=200 if ok else 404)

    @app.post("/api/mcp/servers/{name}/probe")
    async def api_mcp_server_probe(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        try:
            from ..mcp.client import probe_server

            safe = _safe_resource_name(name, "mcp server")
            result = probe_server(config, safe)
            return JSONResponse(result, status_code=200 if result.get("ok") else 502)
        except KeyError:
            return JSONResponse({"ok": False, "error": "server not found", "name": name}, status_code=404)

    @app.post("/api/mcp/servers/{name}/test")
    async def api_mcp_server_test(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        try:
            from ..mcp.client import probe_server

            safe = _safe_resource_name(name, "mcp server")
            result = probe_server(config, safe)
            return JSONResponse(result, status_code=200 if result.get("ok") else 502)
        except KeyError:
            return JSONResponse({"ok": False, "error": "server not found", "name": name}, status_code=404)

    @app.get("/api/mcp/servers/{name}/tools")
    async def api_mcp_server_tools(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        try:
            from ..mcp.client import tool_checklist

            safe = _safe_resource_name(name, "mcp server")
            result = tool_checklist(config, safe)
            return JSONResponse(result, status_code=200 if result.get("ok") else 502)
        except KeyError:
            return JSONResponse({"ok": False, "error": "server not found", "name": name}, status_code=404)

    @app.post("/api/mcp/servers/{name}/tools")
    async def api_mcp_server_tools_post(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        try:
            from ..mcp.client import save_tool_checklist, tool_checklist

            safe = _safe_resource_name(name, "mcp server")
            include = body.get("include", []) if isinstance(body, dict) else []
            if not isinstance(include, list):
                return JSONResponse({"ok": False, "error": "include must be a list"}, status_code=400)
            save_tool_checklist(config, safe, [str(x) for x in include])
            return JSONResponse({"ok": True, **tool_checklist(config, safe)})
        except KeyError:
            return JSONResponse({"ok": False, "error": "server not found", "name": name}, status_code=404)

    @app.get("/api/memory")
    async def api_memory_status(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(_memory_payload(config))

    @app.put("/api/memory/provider")
    async def api_memory_provider_select(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        provider = str((body if isinstance(body, dict) else {}).get("provider") or "").strip()
        if provider.lower() in {"built-in", "builtin", "none"}:
            provider = ""
        if provider:
            from ..memory_providers import memory_provider_status

            status = memory_provider_status(provider, config)
            if not status.get("known"):
                return JSONResponse({"ok": False, "error": f"unknown memory provider: {provider}"}, status_code=400)
        config.set("memory.provider", provider)
        return JSONResponse({"ok": True, "active": provider})

    @app.post("/api/memory/reset")
    async def api_memory_reset(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        target = str((body if isinstance(body, dict) else {}).get("target") or "all").strip().lower()
        if target not in {"all", "memory", "user"}:
            return JSONResponse({"ok": False, "error": "target must be all, memory, or user"}, status_code=400)
        targets = ["memory", "user"] if target == "all" else [target]
        reset = [dash._reset_memory_file(item) for item in targets]
        failed = [item for item in reset if not item.get("ok")]
        deleted = [{"memory": "MEMORY.md", "user": "USER.md"}[item] for item in targets]
        return JSONResponse({"ok": not failed, "deleted": deleted, "reset": reset}, status_code=200 if not failed else 500)

    @app.get("/api/memory/providers")
    async def api_memory_providers(request: Request) -> JSONResponse:
        _require_request(request, config)
        from ..memory_providers import memory_provider_report

        return JSONResponse(memory_provider_report(config))

    @app.get("/api/memory/provider")
    async def api_memory_provider_active(request: Request) -> JSONResponse:
        _require_request(request, config)
        from ..memory_providers import memory_provider_report

        return JSONResponse(memory_provider_report(config)["active"])

    @app.get("/api/memory/providers/{name}")
    async def api_memory_provider_status(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        from ..memory_providers import memory_provider_status

        status = memory_provider_status(name, config)
        return JSONResponse(status, status_code=200 if status.get("known") else 404)

    @app.get("/api/memory/providers/{name}/config")
    async def api_memory_provider_config(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        payload, status = _memory_provider_config_payload(name, config)
        return JSONResponse(payload, status_code=status)

    @app.put("/api/memory/providers/{name}/config")
    async def api_memory_provider_config_update(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        from ..memory_providers import memory_provider_config_schema

        body = await request.json()
        schema = memory_provider_config_schema(name)
        if not schema.get("known"):
            return JSONResponse({"ok": False, "error": f"unknown memory provider: {name}"}, status_code=404)
        properties = dict(schema.get("properties", {}) or {})
        for key, value in _memory_provider_values(body if isinstance(body, dict) else {}).items():
            if key in properties and key != "memory.provider":
                config.set(key, value)
        active = str(schema.get("name") or name)
        config.set("memory.provider", active)
        payload, _status = _memory_provider_config_payload(name, config)
        return JSONResponse({"ok": True, "active": active, "config": payload})

    @app.get("/api/memory/providers/{name}/setup")
    async def api_memory_provider_setup(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        from ..memory_providers import memory_provider_setup

        setup = memory_provider_setup(name)
        return JSONResponse(setup, status_code=200 if setup.get("known") else 404)

    @app.get("/api/memory/providers/{name}/schema")
    async def api_memory_provider_schema(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        from ..memory_providers import memory_provider_config_schema

        schema = memory_provider_config_schema(name)
        return JSONResponse(schema, status_code=200 if schema.get("known") else 404)

    @app.get("/api/audio/voices")
    async def api_audio_voices(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse({
            "voices": ["alloy", "ash", "ballad", "coral", "echo", "fable", "nova", "onyx", "sage", "shimmer"],
            "transcription_models": ["whisper-1", "gpt-4o-mini-transcribe", "gpt-4o-transcribe"],
            "tts_models": ["tts-1", "tts-1-hd", "gpt-4o-mini-tts"],
            "provider": config.get("model.provider"),
        })

    @app.get("/api/audio/elevenlabs/voices")
    async def api_audio_elevenlabs_voices(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse({
            "available": False,
            "provider": "elevenlabs",
            "voices": [
                {"id": "alloy", "name": "Alloy", "label": "Alloy", "provider": "aegis-default"},
                {"id": "onyx", "name": "Onyx", "label": "Onyx", "provider": "aegis-default"},
            ],
        })

    @app.post("/api/audio/tts")
    async def api_audio_tts(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        if not str(body.get("text") or "").strip():
            return JSONResponse({"ok": False, "error": "text is required"}, status_code=400)
        from ..tools.voice import SpeakTool

        result = SpeakTool().run(body, _voice_tool_context(config))
        return JSONResponse(
            {"ok": not result.is_error, "content": result.content, "display": result.display, "data": result.data},
            status_code=502 if result.is_error else 200,
        )

    @app.post("/api/audio/speak")
    async def api_audio_speak(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        if not str(body.get("text") or "").strip():
            return JSONResponse({"ok": False, "error": "text is required"}, status_code=400)
        from ..tools.voice import SpeakTool

        result = SpeakTool().run(body, _voice_tool_context(config))
        payload = {"ok": not result.is_error, "content": result.content, "display": result.display, "data": result.data}
        if isinstance(result.data, dict):
            payload.update({
                key: value
                for key, value in result.data.items()
                if key in {"data_url", "mime_type", "provider"}
            })
        return JSONResponse(payload, status_code=502 if result.is_error else 200)

    @app.post("/api/audio/transcribe")
    async def api_audio_transcribe(request: Request,
                                   file: Annotated[UploadFile, File()],
                                   model: Annotated[str, Form()] = "whisper-1") -> JSONResponse:
        _require_request(request, config)
        suffix = Path(file.filename or "audio").suffix or ".audio"
        temp_path = ""
        try:
            with tempfile.NamedTemporaryFile(prefix="aegis-audio-", suffix=suffix, delete=False) as tmp:
                temp_path = tmp.name
                tmp.write(await file.read())
            from ..tools.voice import TranscribeTool

            result = TranscribeTool().run({"path": temp_path, "model": model}, _voice_tool_context(config))
            return JSONResponse(
                {"ok": not result.is_error, "text": result.content, "display": result.display},
                status_code=502 if result.is_error else 200,
            )
        finally:
            if temp_path:
                try:
                    Path(temp_path).unlink()
                except OSError:
                    pass

