"""Fallback dashboard routes — extracted from dashboard_fastapi.create_app.

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
    StreamingResponse,
    WebSocket,
    _CHAT_FALLBACK,
    _RESIZE_RE,
    _api_get,
    _api_post,
    _dashboard_chat_attach_response,
    _dashboard_chat_control_response,
    _dashboard_chat_json_response,
    _dashboard_chat_streaming_response,
    _html_response,
    _query_dict,
    _require_request,
    _websocket_authorized,
    asyncio,
    base64,
    dash,
    json,
    os,
)


def register(app, config, chat_runner):
    @app.get("/api/{path:path}")
    async def api_get(path: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(_api_get(f"/api/{path}", _query_dict(request), config))

    @app.post("/api/llm/oneshot")
    @app.post("/api/agent/oneshot")
    async def api_llm_oneshot(request: Request) -> JSONResponse:
        _require_request(request, config)
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = {}
        result = _api_post("/api/llm/oneshot", body if isinstance(body, dict) else {}, config)
        return JSONResponse(result, status_code=200 if result.get("ok") else 400)

    @app.post("/api/chat/stream")
    async def chat_stream(request: Request) -> StreamingResponse:
        _require_request(request, config)
        body = await request.json()
        return _dashboard_chat_streaming_response(body, chat_runner, request, config)

    @app.post("/api/chat/attach")
    async def chat_attach(request: Request) -> StreamingResponse:
        _require_request(request, config)
        body = await request.json()
        session_id = str((body or {}).get("session_id") or "").strip()
        return _dashboard_chat_attach_response(session_id, request)

    @app.post("/api/chat/control")
    async def chat_control(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        return _dashboard_chat_control_response(body if isinstance(body, dict) else {})

    @app.post("/api/files/upload")
    async def upload_file(request: Request) -> JSONResponse:
        _require_request(request, config)
        content_type = request.headers.get("content-type", "").lower()
        filename = ""
        target_path = ""
        data = b""
        if content_type.startswith("application/json"):
            body = await request.json()
            body = body if isinstance(body, dict) else {}
            target_path = str(body.get("path") or body.get("dir") or "")
            filename = Path(str(body.get("name") or body.get("filename") or "upload.bin")).name
            data_url = str(body.get("data_url") or body.get("dataUrl") or "")
            if data_url:
                header, sep, payload = data_url.partition(",")
                if not sep or ";base64" not in header:
                    return JSONResponse({"ok": False, "error": "data_url must be base64"})
                try:
                    data = base64.b64decode(payload, validate=True)
                except Exception:  # noqa: BLE001
                    return JSONResponse({"ok": False, "error": "invalid data_url"})
            elif "content" in body:
                data = str(body.get("content") or "").encode("utf-8")
            else:
                return JSONResponse({"ok": False, "error": "missing data_url or content"})
        else:
            form = await request.form()
            upload = form.get("file")
            if upload is None or not hasattr(upload, "read"):
                return JSONResponse({"ok": False, "error": "missing file"})
            target_path = str(form.get("path") or "")
            filename = Path(str(getattr(upload, "filename", "") or "upload.bin")).name
            data = await upload.read()

        target_dir = Path(target_path or Path.home()).expanduser().resolve()
        if not target_dir.is_dir():
            return JSONResponse({"ok": False, "error": "target is not a directory"})
        target = target_dir / filename
        if dash._is_sensitive_path(target):
            return JSONResponse({"ok": False, "error": "blocked: refusing to write a "
                                 "credential/key/SSH path through the dashboard."})
        try:
            target.write_bytes(data)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "error": str(exc)})
        return JSONResponse({"ok": True, "path": str(target), "size": target.stat().st_size})

    @app.post("/api/{path:path}")
    async def api_post(path: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        # Tolerate an empty or malformed body (default to {}) — some POST endpoints
        # take no payload (e.g. /api/curator), and a 500 on missing JSON is hostile.
        raw = await request.body()
        try:
            body = json.loads(raw) if raw else {}
        except ValueError:
            body = {}
        if not isinstance(body, dict):
            body = {}
        if f"/api/{path}" == "/api/chat":
            return await _dashboard_chat_json_response(body, chat_runner, request)
        result = _api_post(f"/api/{path}", body, config, chat_runner, chat_fallback=False)
        if result is _CHAT_FALLBACK:
            return await _dashboard_chat_json_response(body, chat_runner, request)
        return JSONResponse(result)

    @app.websocket("/api/pty")
    async def pty_socket(ws: WebSocket) -> None:
        if not _websocket_authorized(ws, config):
            await ws.close(code=4401, reason="unauthorized")
            return
        await ws.accept()
        try:
            from ..dashboard_pty import PtyBridge, dashboard_terminal_argv, dashboard_terminal_env

            bridge = PtyBridge.spawn(
                dashboard_terminal_argv(ws.query_params.get("resume") or None),
                cwd=os.getcwd(),
                env=dashboard_terminal_env(),
                cols=int(ws.query_params.get("cols") or 100),
                rows=int(ws.query_params.get("rows") or 30),
            )
        except Exception as exc:  # noqa: BLE001
            msg = f"\r\nChat terminal unavailable: {exc}\r\n"
            await ws.send_text(msg)
            await ws.close(code=1011)
            return

        loop = asyncio.get_running_loop()

        async def pump_pty() -> None:
            while True:
                chunk = await loop.run_in_executor(None, bridge.read, 0.2)
                if chunk is None:
                    return
                if not chunk:
                    await asyncio.sleep(0)
                    continue
                try:
                    await ws.send_bytes(chunk)
                except Exception:
                    return

        reader = asyncio.create_task(pump_pty())
        try:
            while True:
                msg = await ws.receive()
                if msg.get("type") == "websocket.disconnect":
                    break
                raw = msg.get("bytes")
                if raw is None:
                    text = msg.get("text")
                    raw = text.encode() if isinstance(text, str) else b""
                match = _RESIZE_RE.match(raw or b"")
                if match:
                    bridge.resize(cols=int(match.group(1)), rows=int(match.group(2)))
                else:
                    bridge.write(raw or b"")
        finally:
            reader.cancel()
            try:
                await reader
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001
                pass
            bridge.close()

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str, request: Request) -> Response:
        if full_path.startswith("api/"):
            return JSONResponse({"error": "not found"}, status_code=404)
        return _html_response(config, request)

