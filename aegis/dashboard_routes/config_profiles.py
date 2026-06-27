"""Config Profiles dashboard routes — extracted from dashboard_fastapi.create_app.

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
    _SESSION_COOKIE,
    _api_post,
    _auth_configured,
    _auth_providers_payload,
    _basic_auth_configured,
    _basic_auth_credentials,
    _coerce_dashboard_bool,
    _config_fields_patch,
    _config_schema,
    _dashboard_preferences,
    _dashboard_registration_enabled,
    _delete_env_key,
    _env_delete_payload,
    _env_list,
    _env_reveal_payload,
    _env_set_payload,
    _is_loopback_host,
    _issue_ws_ticket,
    _make_session_cookie,
    _profile_detail,
    _profile_path,
    _profiles_payload,
    _provider_auth_payload,
    _provider_probe,
    _remote_bind_requires_auth,
    _replace_config_mapping,
    _request_authorized,
    _request_peer_allowed,
    _require_request,
    _runtime_profiles_payload,
    _safe_resource_name,
    _set_dashboard_preferences,
    _write_profile,
    copy,
    dash,
    hmac,
    json,
    os,
    secrets,
)


def register(app, config, chat_runner):
    @app.get("/api/auth/providers")
    async def api_auth_providers(request: Request) -> JSONResponse:
        if not _request_peer_allowed(request, config):
            return JSONResponse({"ok": False, "error": "request rejected by dashboard host guard"}, status_code=403)
        return JSONResponse(_auth_providers_payload(config))

    @app.post("/api/auth/register")
    async def api_auth_register(request: Request) -> JSONResponse:
        client_host = getattr(getattr(request, "client", None), "host", "") or ""
        if not _is_loopback_host(client_host):
            return JSONResponse({"ok": False, "error": "dashboard registration is loopback-only"}, status_code=403)
        if not _dashboard_registration_enabled(config):
            return JSONResponse({"ok": False, "error": "dashboard registration is disabled"}, status_code=403)
        raw = await request.body()
        try:
            body = json.loads(raw) if raw else {}
        except ValueError:
            body = {}
        if not isinstance(body, dict):
            body = {}
        rotate = _coerce_dashboard_bool(body.get("rotate"), False)
        env_token = os.environ.get("AEGIS_DASHBOARD_TOKEN", "")
        configured = dash._dashboard_token(config)
        if configured and not _request_authorized(request, config):
            return JSONResponse({"ok": False, "error": "dashboard registration requires current auth"}, status_code=401)
        if env_token:
            return JSONResponse({
                "ok": True,
                "created": False,
                "token_configured": True,
                "token_source": "env",
                "warning": "AEGIS_DASHBOARD_TOKEN controls this running dashboard; persisted registration skipped.",
            })
        if configured and not rotate:
            return JSONResponse({
                "ok": True,
                "created": False,
                "token_configured": True,
                "token_source": "config",
            })
        token = "aegis_tok_" + secrets.token_urlsafe(24)
        config.data.setdefault("server", {})["dashboard_token"] = token
        config.save()
        response = JSONResponse({
            "ok": True,
            "created": True,
            "token": token,
            "token_configured": True,
            "token_source": "config",
            "auth": _auth_providers_payload(config),
        })
        response.set_cookie("aegis_dashboard_token", token, httponly=True, samesite="lax")
        return response

    @app.get("/api/auth/me")
    async def api_auth_me(request: Request) -> JSONResponse:
        _require_request(request, config)
        providers = []
        if dash._dashboard_token(config):
            providers.append("token")
        if _basic_auth_configured():
            providers.append("basic")
        if not providers:
            providers.append("loopback")
        return JSONResponse({
            "authenticated": True,
            "auth_required": _auth_configured(config) or _remote_bind_requires_auth(config),
            "providers": providers,
            "user": "local",
        })

    @app.post("/api/auth/login")
    async def api_auth_login(request: Request) -> JSONResponse:
        if not _request_peer_allowed(request, config):
            return JSONResponse({"ok": False, "error": "request rejected by dashboard host guard"}, status_code=403)
        body = await request.json()
        username = str(body.get("username") or "")
        password = str(body.get("password") or "")
        if not _basic_auth_configured():
            return JSONResponse({"ok": False, "error": "username/password login is not configured"}, status_code=400)
        expected_user, expected_password = _basic_auth_credentials()
        if not (hmac.compare_digest(username, expected_user)
                and hmac.compare_digest(password, expected_password)):
            return JSONResponse({"ok": False, "error": "invalid username or password"}, status_code=401)
        response = JSONResponse({"ok": True, "user": username})
        response.set_cookie(
            _SESSION_COOKIE,
            _make_session_cookie(username, config),
            httponly=True,
            samesite="lax",
        )
        return response

    @app.post("/api/auth/logout")
    async def api_auth_logout(request: Request) -> JSONResponse:  # noqa: ARG001
        response = JSONResponse({"ok": True})
        response.delete_cookie(_SESSION_COOKIE)
        response.delete_cookie("aegis_dashboard_token")
        return response

    @app.post("/api/auth/ws-ticket")
    async def api_auth_ws_ticket(request: Request) -> JSONResponse:
        _require_request(request, config)
        ticket = _issue_ws_ticket()
        return JSONResponse({"ok": True, **ticket})

    @app.get("/api/config")
    async def api_config_get(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(dash._redacted_config(config))

    @app.post("/api/config")
    async def api_config_set(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        return JSONResponse(_api_post("/api/config", body, config, chat_runner))

    @app.patch("/api/config/fields")
    async def api_config_fields_patch(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        result = _config_fields_patch(config, body if isinstance(body, dict) else {})
        return JSONResponse(result, status_code=200 if result.get("ok") else 400)

    @app.get("/api/config/defaults")
    async def api_config_defaults(request: Request) -> JSONResponse:
        _require_request(request, config)
        from ..config import DEFAULT_CONFIG

        return JSONResponse(copy.deepcopy(DEFAULT_CONFIG))

    @app.get("/api/config/schema")
    async def api_config_schema(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(_config_schema())

    @app.get("/api/config/raw")
    async def api_config_raw(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse({"config": copy.deepcopy(config.data)})

    @app.get("/api/config/yaml")
    async def api_config_yaml(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(dash._config_raw(config))

    @app.post("/api/config/yaml")
    async def api_config_yaml_put(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        result = dash._config_write_raw(str(body.get("raw") or "") if isinstance(body, dict) else "", config)
        return JSONResponse(result, status_code=200 if result.get("ok") else 400)

    @app.get("/api/config/export")
    async def api_config_export(request: Request) -> JSONResponse:
        _require_request(request, config)
        from .. import config as cfg

        payload = {
            "ok": True,
            "config": copy.deepcopy(config.data),
            "redacted_config": dash._redacted_config(config),
            "env": _env_list(),
            "paths": {
                "home": str(cfg.get_home()),
                "config": str(cfg.config_path()),
                "env": str(cfg.env_path()),
            },
        }
        return JSONResponse(
            payload,
            headers={"Content-Disposition": 'attachment; filename="aegis-config-export.json"'},
        )

    @app.put("/api/config/raw")
    async def api_config_raw_put(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        raw = body.get("config", body) if isinstance(body, dict) else None
        if not isinstance(raw, dict):
            return JSONResponse({"ok": False, "error": "config object required"}, status_code=400)
        payload, status = _replace_config_mapping(config, raw)
        return JSONResponse(payload, status_code=status)

    @app.post("/api/config/import")
    async def api_config_import(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        raw = body.get("config", body) if isinstance(body, dict) else None
        if not isinstance(raw, dict):
            return JSONResponse({"ok": False, "error": "config object required"}, status_code=400)
        payload, status = _replace_config_mapping(config, raw)
        if status == 200:
            payload = {"ok": True, "config": dash._redacted_config(config)}
        return JSONResponse(payload, status_code=status)

    @app.get("/api/env")
    async def api_env_list(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(_env_list())

    @app.post("/api/env")
    async def api_env_set(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        payload, status = _env_set_payload(body if isinstance(body, dict) else {})
        return JSONResponse(payload, status_code=status)

    @app.put("/api/env")
    async def api_env_put(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        payload, status = _env_set_payload(body if isinstance(body, dict) else {})
        return JSONResponse(payload, status_code=status)

    @app.get("/api/env/{key}/reveal")
    async def api_env_reveal(key: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        payload, status = _env_reveal_payload(key)
        return JSONResponse(payload, status_code=status)

    @app.post("/api/env/reveal")
    async def api_env_reveal_post(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        key = str((body if isinstance(body, dict) else {}).get("key") or "").strip()
        if not key:
            return JSONResponse({"ok": False, "error": "missing key"}, status_code=400)
        payload, status = _env_reveal_payload(key)
        return JSONResponse(payload, status_code=status)

    @app.delete("/api/env/{key}")
    async def api_env_delete(key: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        payload, status = _env_delete_payload(key)
        return JSONResponse(payload, status_code=status)

    @app.delete("/api/env")
    async def api_env_delete_body(request: Request) -> JSONResponse:
        _require_request(request, config)
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = {}
        key = str((body if isinstance(body, dict) else {}).get("key") or "").strip()
        if not key:
            return JSONResponse({"ok": False, "error": "missing key"}, status_code=400)
        payload, status = _env_delete_payload(key)
        return JSONResponse(payload, status_code=status)

    @app.get("/api/providers")
    async def api_providers_get(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(dash._dashboard_models(config))

    @app.get("/api/providers/matrix")
    async def api_providers_matrix(request: Request) -> JSONResponse:
        _require_request(request, config)
        from ..providers import registry

        return JSONResponse(registry.provider_capability_matrix(config))

    @app.post("/api/providers/probe")
    async def api_providers_probe(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        return JSONResponse(_provider_probe(config, body if isinstance(body, dict) else {}))

    @app.get("/api/provider-auth")
    async def api_provider_auth_get(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(_provider_auth_payload(config))

    @app.get("/api/provider-auth/{provider}")
    async def api_provider_auth_detail(provider: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        name = _safe_resource_name(provider, "provider")
        payload = _provider_auth_payload(config, name)
        return JSONResponse(payload, status_code=200 if payload.get("ok") else 404)

    @app.delete("/api/provider-auth/{provider}")
    async def api_provider_auth_delete(provider: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        name = _safe_resource_name(provider, "provider")
        payload = _provider_auth_payload(config, name)
        row = payload.get("auth") or {}
        removed = []
        for key in row.get("env_vars", []) or []:
            if _delete_env_key(str(key)):
                removed.append(str(key))
        try:
            from ..providers.auth import AuthStore

            AuthStore().delete(name)
        except Exception:  # noqa: BLE001
            pass
        return JSONResponse({"ok": True, "provider": name, "removed_env": removed})

    @app.post("/api/provider-auth/anthropic/import-claude")
    async def api_provider_auth_import_claude(request: Request) -> JSONResponse:
        _require_request(request, config)
        from ..providers.auth import AuthStore, import_claude_cli_login

        ok, detail = import_claude_cli_login(AuthStore())
        return JSONResponse({"ok": bool(ok), "detail": detail}, status_code=200 if ok else 400)

    @app.get("/api/dashboard/preferences")
    async def api_dashboard_preferences(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(_dashboard_preferences(config))

    @app.put("/api/dashboard/preferences")
    async def api_dashboard_preferences_put(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        if not isinstance(body, dict):
            return JSONResponse({"ok": False, "error": "preferences object required"}, status_code=400)
        return JSONResponse({"ok": True, "preferences": _set_dashboard_preferences(config, body)})

    @app.get("/api/profiles")
    async def api_profiles_get(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(_profiles_payload(config))

    @app.post("/api/profiles")
    async def api_profiles_create(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        try:
            raw_name = str((body or {}).get("name") or "").strip()
            if not raw_name:
                config.set("agent.personality", "")
                return JSONResponse({"ok": True, "active": "", "profiles": _profiles_payload(config)})
            name = _safe_resource_name(raw_name, "profile")
            content = str((body or {}).get("content") or "")
            if not content.strip() and _profile_path(name).exists():
                config.set("agent.personality", name)
                return JSONResponse({"ok": True, "active": name, "profiles": _profiles_payload(config)})
            if not content.strip():
                content = f"# {name}\n\n"
            result = _write_profile(config, name, content)
            if bool((body or {}).get("activate", False)):
                config.set("agent.personality", name)
            return JSONResponse({**result, "active": config.get("agent.personality") or "", "profiles": _profiles_payload(config)})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.get("/api/profiles/active")
    async def api_profiles_active_get(request: Request) -> JSONResponse:
        _require_request(request, config)
        active = str(config.get("agent.personality") or "")
        return JSONResponse({"active": active or "default", "current": active or "default"})

    @app.post("/api/profiles/active")
    async def api_profiles_active_post(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        raw_name = str((body or {}).get("name") or (body or {}).get("profile") or "").strip()
        if raw_name in {"", "default", "none", "_default"}:
            config.set("agent.personality", "")
            return JSONResponse({"ok": True, "active": "default", "profiles": _profiles_payload(config)})
        try:
            name = _safe_resource_name(raw_name, "profile")
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        if not _profile_path(name).exists():
            return JSONResponse({"ok": False, "error": "profile not found", "active": name}, status_code=404)
        config.set("agent.personality", name)
        return JSONResponse({"ok": True, "active": name, "profiles": _profiles_payload(config)})

    @app.get("/api/profiles/sessions")
    async def api_profiles_sessions(request: Request) -> JSONResponse:
        _require_request(request, config)
        try:
            limit = max(1, min(200, int(str(request.query_params.get("limit") or "20"))))
        except ValueError:
            limit = 20
        try:
            from ..session import SessionStore

            sessions = SessionStore().list(limit)
        except Exception:  # noqa: BLE001
            sessions = []
        return JSONResponse({
            "ok": True,
            "active": config.get("agent.personality") or "",
            "sessions": sessions,
            "profiles": _profiles_payload(config),
        })

    @app.get("/api/profiles/{name}")
    async def api_profile_get(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        result = _profile_detail(config, name)
        return JSONResponse(result, status_code=200 if result.get("ok") else 404)

    @app.patch("/api/profiles/{name}")
    async def api_profile_patch(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        try:
            existing = _profile_detail(config, name)
            if not existing.get("ok"):
                return JSONResponse(existing, status_code=404)
            content = str((body or {}).get("content", existing.get("content", "")))
            result = _write_profile(config, name, content)
            return JSONResponse({**result, "profiles": _profiles_payload(config)})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.delete("/api/profiles/{name}")
    async def api_profile_delete(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        try:
            path = _profile_path(name)
            if not path.exists():
                return JSONResponse({"ok": False, "error": "profile not found", "name": name}, status_code=404)
            path.unlink()
            if config.get("agent.personality") == path.stem:
                config.set("agent.personality", "")
            return JSONResponse({"ok": True, "name": path.stem, "profiles": _profiles_payload(config)})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.post("/api/profiles/{name}/activate")
    async def api_profile_activate(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        if name in {"default", "none", "_default"}:
            config.set("agent.personality", "")
            return JSONResponse({"ok": True, "active": "", "profiles": _profiles_payload(config)})
        result = _profile_detail(config, name)
        if not result.get("ok"):
            return JSONResponse(result, status_code=404)
        config.set("agent.personality", str(result["name"]))
        return JSONResponse({"ok": True, "active": result["name"], "profiles": _profiles_payload(config)})

    @app.get("/api/profiles/{name}/setup-command")
    async def api_profile_setup_command(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        try:
            safe_name = _safe_resource_name(name, "profile")
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        return JSONResponse({"command": f"aegis profile use {safe_name} && aegis"})

    @app.post("/api/profiles/{name}/open-terminal")
    async def api_profile_open_terminal(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        try:
            safe_name = _safe_resource_name(name, "profile")
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        command = f"aegis profile use {safe_name} && aegis"
        return JSONResponse({"ok": True, "command": command})

    @app.get("/api/profiles/{name}/soul")
    async def api_profile_soul_get(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        detail = _profile_detail(config, name)
        if not detail.get("ok"):
            return JSONResponse({"content": "", "exists": False, "error": detail.get("error")}, status_code=404)
        return JSONResponse({"content": detail.get("content", ""), "exists": True})

    @app.put("/api/profiles/{name}/soul")
    async def api_profile_soul_put(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        try:
            result = _write_profile(config, name, str((body or {}).get("content") or ""))
            return JSONResponse({"ok": True, **result})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.put("/api/profiles/{name}/description")
    async def api_profile_description_put(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        try:
            safe_name = _safe_resource_name(name, "profile")
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        text = str((body or {}).get("description") or "").strip()
        config.data.setdefault("profile_descriptions", {})[safe_name] = text
        config.save()
        return JSONResponse({"ok": True, "description": text, "description_auto": False})

    @app.put("/api/profiles/{name}/model")
    async def api_profile_model_put(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        try:
            safe_name = _safe_resource_name(name, "profile")
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        provider = str((body or {}).get("provider") or "").strip()
        model = str((body or {}).get("model") or "").strip()
        if not provider or not model:
            return JSONResponse({"ok": False, "error": "provider and model are required"}, status_code=400)
        config.data.setdefault("profile_models", {})[safe_name] = {"provider": provider, "model": model}
        config.save()
        return JSONResponse({"ok": True, "provider": provider, "model": model})

    @app.post("/api/profiles/{name}/describe-auto")
    async def api_profile_describe_auto(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        detail = _profile_detail(config, name)
        if not detail.get("ok"):
            return JSONResponse(detail, status_code=404)
        lines = str(detail.get("content") or "").strip().splitlines()
        description = lines[0].lstrip("# ").strip() if lines else str(detail.get("name") or name)
        return JSONResponse({
            "ok": True,
            "reason": "derived from AEGIS profile content",
            "description": description,
            "description_auto": True,
        })

    @app.get("/api/runtime-profiles")
    async def api_runtime_profiles_get(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(_runtime_profiles_payload())

    @app.post("/api/runtime-profiles")
    async def api_runtime_profiles_create(request: Request) -> JSONResponse:
        _require_request(request, config)
        from .. import profiles

        body = await request.json()
        try:
            name = str((body or {}).get("name") or "").strip()
            source = str((body or {}).get("clone_from") or "").strip() or None
            path = profiles.create_profile(
                name,
                clone_from=source,
                clone_config=bool((body or {}).get("clone", False) or source),
                clone_all=bool((body or {}).get("clone_all", False)),
            )
            if bool((body or {}).get("activate", False)):
                profiles.use_profile(name)
            return JSONResponse({"ok": True, "path": str(path), **_runtime_profiles_payload()})
        except (ValueError, FileExistsError, FileNotFoundError, OSError) as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.post("/api/runtime-profiles/{name}/activate")
    async def api_runtime_profile_activate(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        from .. import profiles

        try:
            profiles.use_profile(name)
            return JSONResponse({"ok": True, **_runtime_profiles_payload()})
        except (ValueError, FileNotFoundError, OSError) as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.delete("/api/runtime-profiles/{name}")
    async def api_runtime_profile_delete(name: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        from .. import profiles

        try:
            ok = profiles.delete_profile(name)
            return JSONResponse({"ok": ok, **_runtime_profiles_payload()}, status_code=200 if ok else 404)
        except (ValueError, OSError) as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

