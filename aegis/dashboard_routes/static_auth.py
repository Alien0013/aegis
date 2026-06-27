"""Static Auth dashboard routes — extracted from dashboard_fastapi.create_app.

register() wires this group's handlers onto the shared FastAPI ``app`` (closing over
``config`` + ``chat_runner``, exactly as the original nested routes did). Module-level
deps are imported from :mod:`aegis.dashboard_fastapi`; relative imports inside the
handlers are one level deeper than the original (this module lives one package down).
register_all preserves the original cross-module order so the catch-alls register last.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Form
from fastapi.responses import RedirectResponse

from ..dashboard_fastapi import (
    Any,
    HTMLResponse,
    HTTPException,
    JSONResponse,
    Request,
    Response,
    StreamingResponse,
    WebSocket,
    _SESSION_COOKIE,
    __version__,
    _basic_auth_configured,
    _basic_auth_credentials,
    _dashboard_events_response,
    _dashboard_ws_rpc_response,
    _html_response,
    _login_page,
    _make_session_cookie,
    _publish_dashboard_event,
    _require_request,
    _websocket_authorized,
    asyncio,
    dash,
    hmac,
    json,
    queue,
)


def register(app, config, chat_runner):
    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        return _html_response(config, request)

    @app.get("/login", response_class=HTMLResponse)
    async def login_page() -> HTMLResponse:
        return _login_page()

    @app.get("/auth/login", response_class=HTMLResponse)
    async def auth_login_page() -> HTMLResponse:
        return _login_page()

    @app.get("/auth/callback")
    async def auth_callback(request: Request) -> JSONResponse:
        return JSONResponse({"ok": True, "provider": str(request.query_params.get("provider") or ""), "callback": True})

    @app.post("/auth/password-login")
    async def auth_password_login(request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = {}
        username = str((body or {}).get("username") or "")
        password = str((body or {}).get("password") or "")
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

    @app.post("/auth/login")
    async def login_form(username: Annotated[str, Form()] = "",
                         password: Annotated[str, Form()] = "") -> Response:
        if not _basic_auth_configured():
            return _login_page("Username/password login is not configured.")
        expected_user, expected_password = _basic_auth_credentials()
        if not (hmac.compare_digest(username, expected_user)
                and hmac.compare_digest(password, expected_password)):
            return _login_page("Invalid username or password.")
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(
            _SESSION_COOKIE,
            _make_session_cookie(username, config),
            httponly=True,
            samesite="lax",
        )
        return response

    @app.post("/auth/logout")
    async def logout_form() -> RedirectResponse:
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(_SESSION_COOKIE)
        response.delete_cookie("aegis_dashboard_token")
        return response

    @app.get("/assets/{name:path}")
    async def asset(name: str) -> Response:
        found = dash._asset(f"/assets/{name}")
        if found is None:
            raise HTTPException(status_code=404, detail="asset not found")
        data, ctype = found
        return Response(data, media_type=ctype, headers={"Cache-Control": "public, max-age=31536000, immutable"})

    @app.get("/favicon.ico")
    @app.get("/fonts/{name:path}")
    @app.get("/fonts-terminal/{name:path}")
    async def dist_file(request: Request, name: str = "") -> Response:  # noqa: ARG001
        found = dash._dist_file(request.url.path)
        if found is None:
            raise HTTPException(status_code=404, detail="asset not found")
        data, ctype = found
        return Response(data, media_type=ctype, headers={"Cache-Control": "public, max-age=31536000, immutable"})

    @app.get("/events")
    async def events(request: Request) -> StreamingResponse:
        return _dashboard_events_response(config, request)

    @app.get("/api/events")
    async def api_events(request: Request) -> StreamingResponse:
        return _dashboard_events_response(config, request)

    @app.post("/api/pub")
    async def api_pub(request: Request) -> JSONResponse:
        _require_request(request, config)
        raw = await request.body()
        try:
            body = json.loads(raw) if raw else {}
        except ValueError:
            return JSONResponse({"ok": False, "error": "request body must be JSON"}, status_code=400)
        return JSONResponse(_publish_dashboard_event(body))

    @app.websocket("/api/ws")
    async def event_socket(ws: WebSocket) -> None:
        if not _websocket_authorized(ws, config):
            await ws.close(code=4401, reason="unauthorized")
            return
        from ..eventbus import BUS

        sub = BUS.subscribe()
        await ws.accept()
        loop = asyncio.get_running_loop()
        send_lock = asyncio.Lock()

        async def send_json(payload: dict[str, Any]) -> None:
            async with send_lock:
                await ws.send_json(payload)

        async def pump_events() -> None:
            idle_ticks = 0
            while True:
                try:
                    event = await loop.run_in_executor(None, lambda: sub.get(timeout=0.2))
                    idle_ticks = 0
                    await send_json(event)
                except queue.Empty:
                    idle_ticks += 1
                    if idle_ticks >= 75:
                        idle_ticks = 0
                        await send_json({"type": "keepalive"})
                except Exception:
                    return

        writer = asyncio.create_task(pump_events())
        try:
            while True:
                msg = await ws.receive()
                if msg.get("type") == "websocket.disconnect":
                    break
                reply = _dashboard_ws_rpc_response(msg.get("text"), config)
                if reply is not None:
                    await send_json(reply)
        finally:
            writer.cancel()
            try:
                await writer
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001
                pass
            BUS.unsubscribe(sub)

    @app.get("/api/health")
    async def api_health(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse({"ok": True, "version": __version__})

    @app.get("/api/browser/manage")
    async def api_browser_manage_get(request: Request) -> JSONResponse:
        _require_request(request, config)
        from ..browser_connect import manage_browser

        return JSONResponse(manage_browser("status", config=config))

    @app.post("/api/browser/manage")
    async def api_browser_manage(request: Request) -> JSONResponse:
        _require_request(request, config)
        raw = await request.body()
        try:
            body = json.loads(raw) if raw else {}
        except ValueError:
            body = {}
        if not isinstance(body, dict):
            body = {}
        from ..browser_connect import manage_browser

        try:
            result = manage_browser(
                str(body.get("action") or "status"),
                url=body.get("url"),
                config=config,
            )
        except ValueError as exc:
            return JSONResponse({"connected": False, "url": "", "error": str(exc)}, status_code=400)
        return JSONResponse(result)

