"""FastAPI dashboard backend for the AEGIS web UI."""

from __future__ import annotations

import asyncio
import json
import os
import queue
import re
import threading
from pathlib import Path
from typing import Annotated, Any

from . import __version__
from .config import Config

try:
    from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket
    from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
except ImportError as exc:  # pragma: no cover - import check covers dependency presence
    raise RuntimeError(
        "AEGIS dashboard requires fastapi and uvicorn. Install with: "
        "python -m pip install 'fastapi' 'uvicorn[standard]'"
    ) from exc

from . import dashboard as dash

_RESIZE_RE = re.compile(rb"^\x1b\]1337;Resize=cols=(\d+);rows=(\d+)\x07$")


def _query_dict(request: Request) -> dict[str, list[str]]:
    return {key: request.query_params.getlist(key) for key in request.query_params.keys()}


def _authorized_token(config: Config, *, query: str = "", header: str = "",
                      auth: str = "", cookie: str = "") -> bool:
    token = dash._dashboard_token(config)
    if not token:
        return True
    bearer = auth.removeprefix("Bearer ").strip() if auth.startswith("Bearer ") else ""
    return token in (query, header, bearer, cookie)


def _request_authorized(request: Request, config: Config) -> bool:
    return _authorized_token(
        config,
        query=request.query_params.get("token", ""),
        header=request.headers.get("X-Aegis-Token", ""),
        auth=request.headers.get("Authorization", ""),
        cookie=request.cookies.get("aegis_dashboard_token", ""),
    )


def _require_request(request: Request, config: Config) -> None:
    if not _request_authorized(request, config):
        raise HTTPException(status_code=401, detail="Unauthorized")


def _websocket_authorized(ws: WebSocket, config: Config) -> bool:
    return _authorized_token(
        config,
        query=ws.query_params.get("token", ""),
        header=ws.headers.get("X-Aegis-Token", ""),
        auth=ws.headers.get("Authorization", ""),
        cookie=ws.cookies.get("aegis_dashboard_token", ""),
    )


def _html_response(config: Config) -> HTMLResponse:
    response = HTMLResponse(dash._page_with_bootstrap(config))
    token = dash._dashboard_token(config)
    if token:
        response.set_cookie(
            "aegis_dashboard_token",
            token,
            httponly=True,
            samesite="lax",
        )
    return response


def _api_get(path: str, query: dict[str, list[str]], config: Config) -> dict:
    if path == "/api/status":
        return dash._dashboard_status(config)
    if path == "/api/cockpit":
        return dash._dashboard_cockpit(config)
    if path == "/api/kanban":
        return dash._dashboard_kanban()
    if path == "/api/cron":
        return dash._dashboard_cron_jobs()
    if path == "/api/config":
        return dash._redacted_config(config)
    if path == "/api/models":
        return dash._dashboard_models(config)
    if path == "/api/analytics":
        from . import ratelimit
        from .usage_log import cost_report, daily_series

        days = int((query.get("days", ["30"])[0]) or 30)
        rep = cost_report(days, config)
        rep["series"] = daily_series(days, config)
        rep["balance"] = ratelimit.balance()
        return rep
    if path == "/api/keys":
        return dash._env_keys()
    if path == "/api/pairing":
        from .gateway.pairing import PairingStore

        return PairingStore().list()
    if path == "/api/mcp":
        servers = config.get("mcp.servers", {}) or {}
        return [{"name": n, "command": (s or {}).get("command", ""),
                 "args": (s or {}).get("args", [])} for n, s in servers.items()]
    if path == "/api/mcp/catalog":
        return dash._dashboard_mcp_catalog(
            config,
            live=(query.get("live", ["0"])[0] in {"1", "true", "yes"}),
        )
    if path == "/api/webhooks":
        from .webhook import WebhookStore

        return [{"name": w.name, "prompt": w.prompt} for w in WebhookStore().list()]
    if path == "/api/curator":
        from .curator import apply_transitions

        return apply_transitions(dry_run=True)
    if path == "/api/plugins":
        from .plugins import list_manifests, load_plugins

        api = load_plugins(quiet=True, config=config)
        return {"loaded": [p.name for p in api.files],
                "errors": [{"file": f.name, "error": e} for f, e in api.errors],
                "tools": len(api.tools),
                "tool_names": sorted(getattr(t, "name", str(t)) for t in api.tools),
                "channels": sorted(api.channels),
                "providers": sorted(api.providers),
                "manifests": [m.to_dict() for m in list_manifests(config)]}
    if path == "/api/profiles":
        return dash._profiles(config)
    if path == "/api/system":
        return dash._system_info()
    if path == "/api/traces":
        return dash._dashboard_traces(query, config)
    if path == "/api/trace":
        return dash._dashboard_trace_detail(query, config)
    if path == "/api/runs":
        return dash._dashboard_runs(query)
    if path == "/api/run":
        return dash._dashboard_run_detail(query, config)
    if path == "/api/agents":
        return dash._dashboard_agents(config)
    if path == "/api/agent":
        return dash._dashboard_agent_detail(query, config)
    if path == "/api/projects":
        return dash._dashboard_projects()
    if path == "/api/worktrees":
        return dash._dashboard_worktrees()
    if path == "/api/files":
        return dash._dashboard_files(query)
    if path == "/api/files/read":
        return dash._dashboard_file_read(query)
    if path == "/api/review":
        return dash._dashboard_review()
    if path == "/api/evals":
        return dash._dashboard_evals(config)
    if path == "/api/eval":
        return dash._dashboard_eval_detail(query, config)
    if path == "/api/logs":
        from . import config as cfg

        lp = cfg.logs_dir() / "aegis.log"
        lines = lp.read_text(errors="replace").splitlines()[-200:] if lp.exists() else []
        return {"path": str(lp), "lines": lines}
    if path == "/api/sessions":
        from .session import SessionStore

        return SessionStore().list(100)
    if path == "/api/session":
        from .session import SessionStore

        sid = query.get("id", [""])[0]
        session = SessionStore().load(sid)
        detail = dash._dashboard_session_detail(sid, config) if sid else {"found": False}
        return {
            "messages": [{"role": m.role, "content": m.content}
                         for m in (session.messages if session else []) if m.content],
            "detail": detail,
            "runs": detail.get("runs", []),
            "traces": detail.get("traces", []),
            "links": detail.get("links", {}),
            "lineage": {
                "parent": detail.get("parent"),
                "children": detail.get("children", []),
            } if detail.get("found") else {"parent": None, "children": []},
        }
    if path == "/api/memory":
        from .memory import MemoryStore

        ms = MemoryStore()
        return {"memory": ms.raw("memory"), "user": ms.raw("user")}
    if path == "/api/skills":
        from .skills import SkillsLoader

        return [{"name": s.name, "description": s.description}
                for s in sorted(SkillsLoader(config).available(), key=lambda s: s.name)]
    if path == "/api/tools":
        return dash._dashboard_tools(config)["tools"]
    return {"error": "not found"}


def _api_post(path: str, body: dict, config: Config, chat_runner: Any) -> dict:
    if path == "/api/kanban":
        from .kanban import KanbanStore

        ks = KanbanStore()
        act = body.get("action")
        if act == "create":
            t = ks.create((body.get("title") or "untitled").strip(), body.get("body", ""))
            return {"id": t.id}
        if act == "move" and body.get("id") and body.get("status") in (
            "ready", "in_progress", "done", "blocked"
        ):
            ks._set_status(body["id"], body["status"])
            return {"ok": True}
        if act == "decompose" and body.get("goal"):
            from .kanban_auto import decompose

            cards = decompose(body["goal"], config, store=ks)
            return {"ok": True, "created": len(cards)}
        if act == "run":
            from .kanban_auto import run_board

            threading.Thread(target=run_board, args=(config,), kwargs={"store": ks},
                             daemon=True).start()
            return {"ok": True, "started": True}
        return {"error": "bad kanban request"}
    if path == "/api/cron":
        from .cron import CronStore, build_delivery_sink, run_job

        cs = CronStore()
        act = body.get("action")
        if act == "add" and body.get("schedule") and body.get("prompt"):
            j = cs.add(body["schedule"], body["prompt"], body.get("channel", ""))
            return {"id": j.id}
        if act == "remove" and body.get("id"):
            return {"ok": cs.remove(body["id"])}
        if act == "toggle" and body.get("id"):
            return {"ok": cs.set_enabled(body["id"], bool(body.get("enabled", True)))}
        if act in {"run", "run_now"} and body.get("id"):
            sink = build_delivery_sink(config, verbose=False)
            return run_job(config, str(body["id"]), sink=sink, store=cs, verbose=False)
        return {"error": "bad cron request"}
    if path == "/api/config":
        key, val = body.get("key"), body.get("value")
        if key:
            config.set(key, val)
            return {"ok": True}
        return {"error": "missing key"}
    if path == "/api/models":
        from .providers import registry

        prov, model = body.get("provider"), body.get("model")
        target_provider = prov or config.get("model.provider")
        target_model = model or config.get("model.default")
        validation = registry.validate_model_choice(target_provider, target_model, config)
        if not validation.get("ok", True):
            return {"ok": False, "error": registry.model_validation_message(validation),
                    "validation": validation}
        if prov:
            config.set("model.provider", prov)
        if model:
            config.set("model.default", model)
        validation = registry.validate_model_choice(
            config.get("model.provider"), config.get("model.default"), config
        )
        return {"ok": True, "provider": config.get("model.provider"),
                "model": config.get("model.default"),
                "warning": registry.model_validation_message(validation),
                "validation": validation}
    if path == "/api/keys":
        from .config import set_env_var

        if body.get("key"):
            set_env_var(body["key"].strip(), body.get("value", ""))
            return {"ok": True}
        return {"error": "missing key"}
    if path == "/api/pairing":
        from .gateway.pairing import PairingStore

        ps = PairingStore()
        act, plat = body.get("action"), body.get("platform", "")
        if act == "approve" and body.get("code"):
            return {"ok": ps.approve(plat, body["code"])}
        if act == "revoke" and body.get("user_id"):
            return {"ok": ps.revoke(plat, body["user_id"])}
        return {"error": "bad pairing request"}
    if path == "/api/system":
        if body.get("action") == "backup":
            from .backup import create_backup

            return {"ok": True, "path": str(create_backup())}
        return {"error": "unknown system action"}
    if path == "/api/session":
        act = body.get("action")
        sid = (body.get("id") or body.get("session_id") or "").strip()
        if act == "branch" and sid:
            return dash._dashboard_branch_session(
                sid,
                title=str(body.get("title") or ""),
                reason=str(body.get("reason") or "dashboard"),
            )
        return {"error": "bad session request"}
    if path == "/api/eval":
        if body.get("action") in {"run", "run_suite"}:
            return dash._dashboard_run_eval(body, config)
        return {"error": "bad eval request"}
    if path == "/api/curator":
        from .curator import apply_transitions

        return apply_transitions(dry_run=False)
    if path == "/api/profiles":
        config.set("agent.personality", body.get("name") or "")
        return {"ok": True, "active": config.get("agent.personality")}
    if path == "/api/mcp":
        servers = dict(config.get("mcp.servers", {}) or {})
        act = body.get("action")
        if act == "install" and body.get("name"):
            try:
                from .mcp.client import install_from_catalog

                spec = install_from_catalog(config, str(body["name"]))
                target = spec.get("url") or " ".join([spec.get("command", ""), *(spec.get("args") or [])])
                return {"ok": True, "name": body["name"], "target": target.strip()}
            except KeyError:
                return {"ok": False, "error": "catalog entry not found"}
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "error": str(exc)}
        if act == "add" and body.get("name") and body.get("command"):
            parts = str(body["command"]).split()
            servers[body["name"]] = {"command": parts[0], "args": parts[1:]}
            config.data.setdefault("mcp", {})["servers"] = servers
            config.save()
            return {"ok": True}
        if act == "remove" and body.get("name") in servers:
            servers.pop(body["name"])
            config.data.setdefault("mcp", {})["servers"] = servers
            config.save()
            return {"ok": True}
        return {"error": "bad mcp request"}
    if path == "/api/plugins":
        act = body.get("action")
        name = str(body.get("name") or "").strip()
        try:
            from . import plugins as plugin_runtime

            if act == "install" and body.get("source"):
                installed = plugin_runtime.install(
                    str(body["source"]),
                    config,
                    force=bool(body.get("force", False)),
                )
                return {"ok": True, "name": installed}
            if act == "enable" and name:
                return {"ok": plugin_runtime.enable(name, config)}
            if act == "disable" and name:
                return {"ok": plugin_runtime.disable(name, config)}
            if act == "remove" and name:
                return {"ok": plugin_runtime.remove(name, config)}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        return {"error": "bad plugins request"}
    if path == "/api/webhooks":
        from .webhook import WebhookStore

        ws = WebhookStore()
        act = body.get("action")
        if act == "add" and body.get("name") and body.get("prompt"):
            ws.add(body["name"], body["prompt"])
            return {"ok": True}
        if act == "remove" and body.get("name"):
            return {"ok": ws.remove(body["name"])}
        return {"error": "bad webhook request"}
    if path == "/api/memory":
        from .memory import MemoryStore

        ms = MemoryStore()
        act = body.get("action")
        target = body.get("target", "memory")
        if target not in ("memory", "user"):
            return {"error": "target must be 'memory' or 'user'"}
        if act == "add" and body.get("content"):
            return {"result": ms.add(target, body["content"])}
        if act == "remove" and body.get("match"):
            return {"result": ms.remove(target, body["match"])}
        return {"error": "bad memory request"}
    if path == "/api/files/mkdir":
        parent = Path(str(body.get("path") or Path.home())).expanduser().resolve()
        name = Path(str(body.get("name") or "")).name
        if not name:
            return {"ok": False, "error": "missing name"}
        target = parent / name
        try:
            target.mkdir(parents=bool(body.get("parents", False)), exist_ok=bool(body.get("exist_ok", False)))
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "path": str(target)}
    return dash._dashboard_chat_response(body, chat_runner)


def create_app(config: Config) -> FastAPI:
    from .session import SessionStore
    from .surface import SurfaceRunner

    app = FastAPI(title="AEGIS", version=__version__)
    chat_runner = SurfaceRunner(config, store=SessionStore(), include_mcp=True)

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        return _html_response(config)

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
        _require_request(request, config)

        def stream():
            from .eventbus import BUS

            sub = BUS.subscribe()
            try:
                while True:
                    try:
                        ev = sub.get(timeout=15)
                        yield f"data: {json.dumps(ev)}\n\n".encode()
                    except queue.Empty:
                        yield b": keepalive\n\n"
            finally:
                BUS.unsubscribe(sub)

        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.websocket("/api/ws")
    async def event_socket(ws: WebSocket) -> None:
        if not _websocket_authorized(ws, config):
            await ws.close(code=4401, reason="unauthorized")
            return
        from .eventbus import BUS

        sub = BUS.subscribe()
        await ws.accept()
        loop = asyncio.get_running_loop()

        async def pump_events() -> None:
            idle_ticks = 0
            while True:
                try:
                    event = await loop.run_in_executor(None, lambda: sub.get(timeout=0.2))
                    idle_ticks = 0
                    await ws.send_json(event)
                except queue.Empty:
                    idle_ticks += 1
                    if idle_ticks >= 75:
                        idle_ticks = 0
                        await ws.send_json({"type": "keepalive"})
                except Exception:
                    return

        writer = asyncio.create_task(pump_events())
        try:
            while True:
                msg = await ws.receive()
                if msg.get("type") == "websocket.disconnect":
                    break
                if msg.get("text") == "ping":
                    await ws.send_json({"type": "pong"})
        finally:
            writer.cancel()
            try:
                await writer
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001
                pass
            BUS.unsubscribe(sub)

    @app.get("/api/{path:path}")
    async def api_get(path: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse(_api_get(f"/api/{path}", _query_dict(request), config))

    @app.post("/api/chat/stream")
    async def chat_stream(request: Request) -> StreamingResponse:
        _require_request(request, config)
        body = await request.json()
        events_q: queue.Queue[dict | object] = queue.Queue()
        sentinel = object()

        def worker() -> None:
            try:
                dash._dashboard_chat_stream(body, chat_runner, events_q.put)
            finally:
                events_q.put(sentinel)

        threading.Thread(target=worker, daemon=True).start()

        def stream():
            while True:
                item = events_q.get()
                if item is sentinel:
                    break
                yield f"data: {json.dumps(item)}\n\n".encode()

        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.post("/api/files/upload")
    async def upload_file(
        request: Request,
        file: Annotated[UploadFile, File()],
        path: Annotated[str, Form()] = "",
    ) -> JSONResponse:
        _require_request(request, config)
        target_dir = Path(path or Path.home()).expanduser().resolve()
        if not target_dir.is_dir():
            return JSONResponse({"ok": False, "error": "target is not a directory"})
        filename = Path(file.filename or "upload.bin").name
        target = target_dir / filename
        try:
            data = await file.read()
            target.write_bytes(data)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "error": str(exc)})
        return JSONResponse({"ok": True, "path": str(target), "size": target.stat().st_size})

    @app.post("/api/{path:path}")
    async def api_post(path: str, request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        return JSONResponse(_api_post(f"/api/{path}", body, config, chat_runner))

    @app.websocket("/api/pty")
    async def pty_socket(ws: WebSocket) -> None:
        if not _websocket_authorized(ws, config):
            await ws.close(code=4401, reason="unauthorized")
            return
        await ws.accept()
        try:
            from .dashboard_pty import PtyBridge, dashboard_tui_argv

            bridge = PtyBridge.spawn(
                dashboard_tui_argv(ws.query_params.get("resume") or None),
                cwd=os.getcwd(),
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
    async def spa(full_path: str) -> Response:
        if full_path.startswith("api/"):
            return JSONResponse({"error": "not found"}, status_code=404)
        return _html_response(config)

    return app


def run_dashboard(config: Config, host: str, port: int, *, open_browser: bool = False) -> None:
    import socket
    import uvicorn

    requested = port
    selected = None
    for candidate in range(port, port + 50):
        with socket.socket() as s:
            try:
                s.bind((host, candidate))
            except OSError:
                continue
            selected = candidate
            break
    if selected is None:
        raise OSError(f"no free port in {requested}-{requested + 49} on {host}")
    port = selected
    if port != requested:
        print(f"  (port {requested} busy - using {port})")
    url = dash._dashboard_url(config, host, port)
    print(f"AEGIS control panel -> {url}")
    print("  (leave this running; press Ctrl+C to stop)")
    if open_browser:
        import webbrowser

        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    uvicorn.run(create_app(config), host=host, port=port, log_level="warning")
