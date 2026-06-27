"""Skills Plugins dashboard routes — extracted from dashboard_fastapi.create_app.

register() wires this group's handlers onto the shared FastAPI ``app`` (closing over
``config`` + ``chat_runner``, exactly as the original nested routes did). Module-level
deps are imported from :mod:`aegis.dashboard_fastapi`; relative imports inside the
handlers are one level deeper than the original (this module lives one package down).
register_all preserves the original cross-module order so the catch-alls register last.
"""

from __future__ import annotations

from ..dashboard_fastapi import (
    JSONResponse,
    Path,
    Request,
    Response,
    _coerce_dashboard_bool,
    _dashboard_agent_plugin_update,
    _dashboard_plugin_hub,
    _dashboard_plugin_static,
    _dashboard_plugins_payload,
    _extensions_status_payload,
    _mount_dashboard_plugin_api_routes,
    _plugin_detail,
    _plugins_payload,
    _require_request,
    _safe_plugin_route_name,
    _safe_resource_name,
    _set_dashboard_plugin_providers,
    _set_dashboard_plugin_visibility,
    _skill_detail,
    _skill_path_editable,
    _skills_payload,
    _validate_plugin_source,
    _validate_skill_delete_target,
)


def register(app, config, chat_runner):
    @app.get("/api/skills/manage")
    async def api_skills_manage(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(_skills_payload(config))

    @app.get("/api/skills/bundles")
    async def api_skill_bundles(request: Request) -> JSONResponse:
        _require_request(request, config)
        from ..skill_bundles import list_bundles

        return JSONResponse({"bundles": list_bundles()})

    @app.post("/api/skills/bundles")
    async def api_skill_bundle_save(request: Request) -> JSONResponse:
        _require_request(request, config)
        from ..skill_bundles import list_bundles, save_bundle

        body = await request.json()
        try:
            bundle = save_bundle(
                str(body.get("name") or ""),
                body.get("skills") or body.get("members") or [],
                description=str(body.get("description") or ""),
                instruction=str(body.get("instruction") or ""),
            )
            return JSONResponse({"ok": True, "bundle": bundle, "bundles": list_bundles()})
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.delete("/api/skills/bundles/{name}")
    async def api_skill_bundle_delete(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        from ..skill_bundles import delete_bundle, list_bundles

        ok = delete_bundle(name)
        return JSONResponse({"ok": ok, "name": name, "bundles": list_bundles()}, status_code=200 if ok else 404)

    @app.get("/api/skills/marketplace/search")
    async def api_skills_marketplace_search(request: Request) -> JSONResponse:
        _require_request(request, config)
        from .. import marketplace

        query = str(request.query_params.get("q") or request.query_params.get("query") or "")
        try:
            results = marketplace.search(query)
        except Exception as exc:  # noqa: BLE001
            results = []
            return JSONResponse({"ok": False, "error": str(exc), "results": results}, status_code=502)
        return JSONResponse({"ok": True, "query": query, "results": results})

    @app.post("/api/skills/marketplace/install")
    async def api_skills_marketplace_install(request: Request) -> JSONResponse:
        _require_request(request, config)
        from .. import marketplace

        body = await request.json()
        try:
            if body.get("hub"):
                names = marketplace.install_hub(str(body["hub"]), config, force=bool(body.get("force", False)))
            else:
                source = str(body.get("source") or body.get("name") or "").strip()
                if not source:
                    return JSONResponse({"ok": False, "error": "source is required"}, status_code=400)
                names = marketplace.install(source, force=bool(body.get("force", False)))
            return JSONResponse({**_skills_payload(config), "ok": True, "installed": names})
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.post("/api/skills/marketplace/preview")
    async def api_skills_marketplace_preview(request: Request) -> JSONResponse:
        _require_request(request, config)
        from .. import marketplace

        body = await request.json()
        try:
            if body.get("hub"):
                report = marketplace.preview_hub(str(body["hub"]), config, force=bool(body.get("force", False)))
            else:
                source = str(body.get("source") or body.get("name") or "").strip()
                if not source:
                    return JSONResponse({"ok": False, "error": "source is required"}, status_code=400)
                report = marketplace.preview(source, force=bool(body.get("force", False)))
            return JSONResponse({"ok": bool(report.get("ok", True)), "preview": report, **report})
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.post("/api/skills/marketplace/uninstall")
    async def api_skills_marketplace_uninstall(request: Request) -> JSONResponse:
        _require_request(request, config)
        from .. import marketplace

        body = await request.json()
        name = str(body.get("name") or "").strip()
        if not name:
            return JSONResponse({"ok": False, "error": "name is required"}, status_code=400)
        ok = marketplace.remove(name)
        return JSONResponse({"ok": ok, "name": name, **_skills_payload(config)},
                            status_code=200 if ok else 404)

    @app.get("/api/skills")
    async def api_skills_list(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(_skills_payload(config))

    @app.get("/api/skills/content")
    async def api_skills_content_get(request: Request) -> JSONResponse:
        _require_request(request, config)
        name = str(request.query_params.get("name") or "").strip()
        if not name:
            return JSONResponse({"ok": False, "error": "name is required"}, status_code=400)
        detail = _skill_detail(config, name)
        if not detail.get("ok"):
            return JSONResponse(detail, status_code=404)
        skill = detail["skill"]
        return JSONResponse({
            "ok": True,
            "name": skill["name"],
            "path": skill["path"],
            "content": detail["content"],
            "skill": skill,
        })

    @app.put("/api/skills/content")
    async def api_skills_content_put(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        name = str(body.get("name") or "").strip()
        if not name:
            return JSONResponse({"ok": False, "error": "name is required"}, status_code=400)
        detail = _skill_detail(config, name)
        if not detail.get("ok"):
            return JSONResponse(detail, status_code=404)
        try:
            from ..tools.skill_manage import _split_skill_content
            from ..util import atomic_write

            skill_path = Path(detail["skill"]["path"]).resolve()
            if not _skill_path_editable(skill_path):
                return JSONResponse({"ok": False, "error": "only workspace or personal skills can be edited"}, status_code=403)
            content = str(body.get("content") or "")
            if not content:
                return JSONResponse({"ok": False, "error": "content is required"}, status_code=400)
            fm, _skill_body, err = _split_skill_content(content)
            if err:
                return JSONResponse({"ok": False, "error": err}, status_code=400)
            safe = _safe_resource_name(name, "skill")
            if str(fm.get("name") or "").strip() != safe:
                return JSONResponse({"ok": False, "error": "frontmatter name must match skill name"}, status_code=400)
            atomic_write(skill_path, content.rstrip() + "\n")
            return JSONResponse({"ok": True, **_skill_detail(config, safe)})
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.put("/api/skills/toggle")
    async def api_skills_toggle(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        name = str(body.get("name") or "").strip()
        if not name:
            return JSONResponse({"ok": False, "error": "name is required"}, status_code=400)
        safe = _safe_resource_name(name, "skill")
        disabled = [str(s) for s in (config.get("skills.disabled", []) or []) if str(s).strip()]
        enabled = bool(body.get("enabled"))
        if enabled:
            disabled = [item for item in disabled if item != safe]
        elif safe not in disabled:
            disabled.append(safe)
        config.set("skills.disabled", sorted(set(disabled)))
        return JSONResponse({"ok": True, "name": safe, "enabled": enabled, **_skills_payload(config)})

    def _hub_identifier(data: dict) -> str:
        return str(data.get("identifier") or data.get("source") or data.get("name") or data.get("hub") or "").strip()

    def _is_direct_skill_source(identifier: str) -> bool:
        prefixes = ("git:", "http://", "https://", "skills-sh:", "lobehub:", "clawhub:", "/", "./", "../")
        return identifier.startswith(prefixes)

    @app.get("/api/skills/hub/sources")
    async def api_skills_hub_sources(request: Request) -> JSONResponse:
        _require_request(request, config)
        from .. import marketplace

        try:
            return JSONResponse({
                "ok": True,
                "sources": marketplace.list_registries(config),
                "taps": marketplace.list_taps(config),
                "installed": marketplace.installed(),
            })
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "error": str(exc), "sources": []}, status_code=502)

    @app.get("/api/skills/hub/search")
    async def api_skills_hub_search(request: Request) -> JSONResponse:
        _require_request(request, config)
        from .. import marketplace

        query = str(request.query_params.get("q") or request.query_params.get("query") or "").strip()
        limit = int(str(request.query_params.get("limit") or "20") or "20")
        if not query:
            return JSONResponse({"ok": True, "results": [], "source_counts": {}, "timed_out": [], "installed": marketplace.installed()})
        try:
            results = marketplace.search(query)[:max(1, min(limit, 50))]
            counts: dict[str, int] = {}
            for row in results:
                hub = str(row.get("hub") or row.get("source") or "unknown")
                counts[hub] = counts.get(hub, 0) + 1
            return JSONResponse({
                "ok": True,
                "query": query,
                "results": results,
                "source_counts": counts,
                "timed_out": [],
                "installed": marketplace.installed(),
            })
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "error": str(exc), "results": []}, status_code=502)

    @app.get("/api/skills/hub/preview")
    async def api_skills_hub_preview(request: Request) -> JSONResponse:
        _require_request(request, config)
        from .. import marketplace

        identifier = _hub_identifier(dict(request.query_params))
        if not identifier:
            return JSONResponse({"ok": False, "error": "identifier is required"}, status_code=400)
        try:
            if identifier in marketplace.list_taps(config) and not _is_direct_skill_source(identifier):
                report = marketplace.preview_hub(identifier, config, force=False)
            else:
                report = marketplace.preview(identifier, force=False)
            return JSONResponse({"ok": bool(report.get("ok", True)), "identifier": identifier, "preview": report, **report})
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "identifier": identifier, "error": str(exc)}, status_code=400)

    @app.get("/api/skills/hub/scan")
    async def api_skills_hub_scan(request: Request) -> JSONResponse:
        _require_request(request, config)
        from .. import marketplace

        identifier = _hub_identifier(dict(request.query_params))
        if not identifier:
            return JSONResponse({"ok": False, "error": "identifier is required"}, status_code=400)
        try:
            if identifier in marketplace.list_taps(config) and not _is_direct_skill_source(identifier):
                report = marketplace.preview_hub(identifier, config, force=False)
            else:
                report = marketplace.preview(identifier, force=False)
            return JSONResponse({"ok": bool(report.get("ok", True)), "identifier": identifier, "scan": report, "preview": report})
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "identifier": identifier, "error": str(exc)}, status_code=400)

    @app.post("/api/skills/hub/install")
    async def api_skills_hub_install(request: Request) -> JSONResponse:
        _require_request(request, config)
        from .. import marketplace

        body = await request.json()
        identifier = _hub_identifier(body)
        if not identifier:
            return JSONResponse({"ok": False, "error": "identifier is required"}, status_code=400)
        force = bool(body.get("force", False))
        try:
            if identifier in marketplace.list_taps(config) and not _is_direct_skill_source(identifier):
                names = marketplace.install_hub(identifier, config, force=force)
            else:
                names = marketplace.install(identifier, force=force)
            return JSONResponse({**_skills_payload(config), "ok": True, "identifier": identifier, "installed": names})
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "identifier": identifier, "error": str(exc)}, status_code=400)

    @app.post("/api/skills/hub/uninstall")
    async def api_skills_hub_uninstall(request: Request) -> JSONResponse:
        _require_request(request, config)
        from .. import marketplace

        body = await request.json()
        name = str(body.get("name") or "").strip()
        if not name:
            return JSONResponse({"ok": False, "error": "name is required"}, status_code=400)
        ok = marketplace.remove(name)
        return JSONResponse({"ok": ok, "name": name, **_skills_payload(config)}, status_code=200 if ok else 404)

    @app.post("/api/skills/hub/update")
    async def api_skills_hub_update(request: Request) -> JSONResponse:
        _require_request(request, config)
        from .. import marketplace

        try:
            await request.json()
        except Exception:  # noqa: BLE001
            pass
        updated: list[str] = []
        errors: list[dict[str, str]] = []
        for name, record in marketplace.installed().items():
            source = str((record or {}).get("source") or "").strip()
            if not source:
                continue
            try:
                updated.extend(marketplace.install(source, force=True))
            except Exception as exc:  # noqa: BLE001
                errors.append({"name": name, "error": str(exc)})
        return JSONResponse({"ok": not errors, "updated": updated, "errors": errors, **_skills_payload(config)})

    @app.post("/api/skills")
    async def api_skills_create(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        try:
            from ..skills import SkillsLoader
            from ..tools.skill_manage import _split_skill_content

            loader = SkillsLoader(config)
            content = body.get("content")
            if content is not None:
                fm, skill_body, err = _split_skill_content(str(content))
                if err:
                    return JSONResponse({"ok": False, "error": err}, status_code=400)
                name = str(fm.get("name") or "").strip()
                description = str(fm.get("description") or "").strip()
                extra = {k: v for k, v in fm.items() if k not in {"name", "description"}}
                path = loader.create(name, description, skill_body, extra_frontmatter=extra, origin="user")
            else:
                name = str(body.get("name") or "").strip()
                description = str(body.get("description") or "").strip()
                skill_body = str(body.get("body") or "").strip()
                if not name or not description or not skill_body:
                    return JSONResponse({"ok": False, "error": "name, description, and body are required"}, status_code=400)
                path = loader.create(name, description, skill_body, origin="user")
            return JSONResponse({"ok": True, "path": str(path), **_skills_payload(config)})
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.get("/api/skills/{name}")
    async def api_skill_detail(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        result = _skill_detail(config, name)
        return JSONResponse(result, status_code=200 if result.get("ok") else 404)

    @app.put("/api/skills/{name}/toggle")
    async def api_skill_toggle(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        safe = _safe_resource_name(name, "skill")
        disabled = [str(s) for s in (config.get("skills.disabled", []) or []) if str(s).strip()]
        enabled = bool(body.get("enabled"))
        if enabled:
            disabled = [item for item in disabled if item != safe]
        elif safe not in disabled:
            disabled.append(safe)
        config.set("skills.disabled", sorted(set(disabled)))
        return JSONResponse({"ok": True, "name": safe, "enabled": enabled, **_skills_payload(config)})

    @app.patch("/api/skills/{name}")
    async def api_skill_patch(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        detail = _skill_detail(config, name)
        if not detail.get("ok"):
            return JSONResponse(detail, status_code=404)
        try:
            from ..tools.skill_manage import _split_skill_content
            from ..util import atomic_write

            skill_path = Path(detail["skill"]["path"]).resolve()
            if not _skill_path_editable(skill_path):
                return JSONResponse({"ok": False, "error": "only workspace or personal skills can be edited"}, status_code=403)
            content = str(body.get("content") or "")
            if not content:
                current = skill_path.read_text(encoding="utf-8")
                content = current
            fm, _skill_body, err = _split_skill_content(content)
            if err:
                return JSONResponse({"ok": False, "error": err}, status_code=400)
            if str(fm.get("name") or "").strip() != name:
                return JSONResponse({"ok": False, "error": "frontmatter name must match skill name"}, status_code=400)
            atomic_write(skill_path, content.rstrip() + "\n")
            return JSONResponse({"ok": True, **_skill_detail(config, name)})
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.delete("/api/skills/{name}")
    async def api_skill_delete(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        import shutil

        detail = _skill_detail(config, name)
        if not detail.get("ok"):
            return JSONResponse(detail, status_code=404)
        skill_path = Path(detail["skill"]["path"])
        if not _skill_path_editable(skill_path):
            return JSONResponse({"ok": False, "error": "only workspace or personal skills can be deleted"}, status_code=403)
        target, err = _validate_skill_delete_target(skill_path)
        if err:
            return JSONResponse({"ok": False, "error": err}, status_code=403)
        ok = bool(target and target.exists())
        if ok:
            shutil.rmtree(target)
        return JSONResponse({"ok": ok, "name": detail["skill"]["name"], **_skills_payload(config)}, status_code=200 if ok else 404)

    @app.post("/api/skills/{name}/pin")
    async def api_skill_pin(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        safe = _safe_resource_name(name, "skill")
        from .. import curator

        curator.pin(safe, True)
        return JSONResponse({"ok": True, "name": safe, "pinned": True})

    @app.post("/api/skills/{name}/unpin")
    async def api_skill_unpin(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        safe = _safe_resource_name(name, "skill")
        from .. import curator

        curator.pin(safe, False)
        return JSONResponse({"ok": True, "name": safe, "pinned": False})

    @app.get("/api/plugins")
    async def api_plugins_list(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(_plugins_payload(config))

    @app.get("/api/extensions/status")
    async def api_extensions_status(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(_extensions_status_payload(config))

    @app.get("/api/dashboard/plugins")
    async def api_dashboard_plugins(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(_dashboard_plugins_payload(config))

    @app.get("/api/dashboard/plugins/hub")
    async def api_dashboard_plugins_hub(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(_dashboard_plugin_hub(config))

    @app.get("/api/dashboard/plugins/rescan")
    @app.post("/api/dashboard/plugins/rescan")
    async def api_dashboard_plugins_rescan(request: Request) -> JSONResponse:
        _require_request(request, config)
        from .. import plugins as plugin_runtime

        plugin_runtime.clear_runtime_cache()
        _mount_dashboard_plugin_api_routes(app, config)
        hub = _dashboard_plugin_hub(config)
        return JSONResponse({"ok": True, "count": len(hub.get("plugins", [])), **hub})

    @app.get("/dashboard-plugins/{plugin_name}/{file_path:path}")
    async def dashboard_plugin_asset(plugin_name: str, file_path: str, request: Request) -> Response:
        _require_request(request, config)
        return _dashboard_plugin_static(config, plugin_name, file_path)

    @app.get("/dashboard-plugins/{bad_path:path}")
    async def dashboard_plugin_bad_path(bad_path: str) -> JSONResponse:  # noqa: ARG001
        return JSONResponse({"ok": False, "error": "dashboard plugin asset not found"}, status_code=404)

    _mount_dashboard_plugin_api_routes(app, config)

    @app.post("/api/plugins/reload")
    async def api_plugins_reload(request: Request) -> JSONResponse:
        _require_request(request, config)
        try:
            from .. import plugins as plugin_runtime

            plugin_runtime.clear_runtime_cache()
            _mount_dashboard_plugin_api_routes(app, config)
            return JSONResponse({"ok": True, **_plugins_payload(config)})
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.post("/api/plugins/validate")
    async def api_plugins_validate(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        result = _validate_plugin_source(str((body or {}).get("source") or ""))
        return JSONResponse(result, status_code=200 if result.get("ok") else 400)

    @app.post("/api/plugins/install")
    async def api_plugins_install(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        source = str(body.get("source") or "").strip()
        if not source:
            return JSONResponse({"ok": False, "error": "source is required"}, status_code=400)
        try:
            from .. import plugins as plugin_runtime

            result = plugin_runtime.install_details(
                source,
                config,
                force=_coerce_dashboard_bool(body.get("force"), False),
                enable_now=_coerce_dashboard_bool(body.get("enable"), True),
            )
            _mount_dashboard_plugin_api_routes(app, config)
            return JSONResponse({**result, **_plugins_payload(config)})
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.post("/api/dashboard/agent-plugins/install")
    async def api_dashboard_agent_plugins_install(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        source = str(body.get("identifier") or body.get("source") or "").strip()
        if not source:
            return JSONResponse({"ok": False, "error": "identifier is required"}, status_code=400)
        try:
            from .. import plugins as plugin_runtime

            result = plugin_runtime.install_details(
                source,
                config,
                force=_coerce_dashboard_bool(body.get("force"), False),
                enable_now=_coerce_dashboard_bool(body.get("enable"), True),
            )
            _mount_dashboard_plugin_api_routes(app, config)
            return JSONResponse({**result, **_dashboard_plugin_hub(config)})
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.put("/api/dashboard/plugin-providers")
    async def api_dashboard_plugin_providers(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        if not isinstance(body, dict):
            return JSONResponse({"ok": False, "error": "request body must be an object"}, status_code=400)
        return JSONResponse(_set_dashboard_plugin_providers(config, body))

    @app.post("/api/dashboard/plugins/{name:path}/visibility")
    async def api_dashboard_plugin_visibility(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        if not isinstance(body, dict):
            return JSONResponse({"ok": False, "error": "request body must be an object"}, status_code=400)
        payload = _set_dashboard_plugin_visibility(config, name, bool(body.get("hidden", False)))
        return JSONResponse(payload)

    @app.get("/api/dashboard/agent-plugins/{name:path}")
    async def api_dashboard_agent_plugin_detail(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        safe = _safe_plugin_route_name(name)
        payload = _plugin_detail(config, safe)
        return JSONResponse(payload, status_code=200 if payload.get("ok") else 404)

    @app.get("/api/plugins/{name}")
    async def api_plugin_detail(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        safe = _safe_plugin_route_name(name)
        payload = _plugin_detail(config, safe)
        return JSONResponse(payload, status_code=200 if payload.get("ok") else 404)

    @app.post("/api/plugins/{name:path}/enable")
    async def api_plugin_enable(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        from .. import plugins as plugin_runtime

        safe = _safe_plugin_route_name(name)
        ok = plugin_runtime.enable(safe, config)
        if ok:
            _mount_dashboard_plugin_api_routes(app, config)
        return JSONResponse({"ok": ok, "name": safe, **_plugins_payload(config)}, status_code=200 if ok else 404)

    @app.post("/api/dashboard/agent-plugins/{name:path}/enable")
    async def api_dashboard_agent_plugin_enable(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        from .. import plugins as plugin_runtime

        safe = _safe_plugin_route_name(name)
        ok = plugin_runtime.enable(safe, config)
        if ok:
            _mount_dashboard_plugin_api_routes(app, config)
        return JSONResponse({"ok": ok, "name": safe, **_dashboard_plugin_hub(config)}, status_code=200 if ok else 404)

    @app.post("/api/plugins/{name:path}/disable")
    async def api_plugin_disable(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        from .. import plugins as plugin_runtime

        safe = _safe_plugin_route_name(name)
        ok = plugin_runtime.disable(safe, config)
        if ok:
            _mount_dashboard_plugin_api_routes(app, config)
        return JSONResponse({"ok": ok, "name": safe, **_plugins_payload(config)}, status_code=200 if ok else 404)

    @app.post("/api/dashboard/agent-plugins/{name:path}/disable")
    async def api_dashboard_agent_plugin_disable(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        from .. import plugins as plugin_runtime

        safe = _safe_plugin_route_name(name)
        ok = plugin_runtime.disable(safe, config)
        if ok:
            _mount_dashboard_plugin_api_routes(app, config)
        return JSONResponse({"ok": ok, "name": safe, **_dashboard_plugin_hub(config)}, status_code=200 if ok else 404)

    @app.post("/api/dashboard/agent-plugins/{name:path}/update")
    async def api_dashboard_agent_plugin_update(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        result = _dashboard_agent_plugin_update(config, name)
        if result.get("ok"):
            _mount_dashboard_plugin_api_routes(app, config)
            return JSONResponse({**result, **_dashboard_plugin_hub(config)})
        return JSONResponse(result, status_code=400 if result.get("error") else 404)

    @app.delete("/api/plugins/{name:path}")
    async def api_plugin_delete(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        from .. import plugins as plugin_runtime

        safe = _safe_plugin_route_name(name)
        ok = plugin_runtime.remove(safe, config)
        if ok:
            _mount_dashboard_plugin_api_routes(app, config)
        return JSONResponse({"ok": ok, "name": safe, **_plugins_payload(config)}, status_code=200 if ok else 404)

    @app.delete("/api/dashboard/agent-plugins/{name:path}")
    async def api_dashboard_agent_plugin_delete(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        from .. import plugins as plugin_runtime

        safe = _safe_plugin_route_name(name)
        ok = plugin_runtime.remove(safe, config)
        if ok:
            _mount_dashboard_plugin_api_routes(app, config)
        return JSONResponse({"ok": ok, "name": safe, **_dashboard_plugin_hub(config)}, status_code=200 if ok else 404)

