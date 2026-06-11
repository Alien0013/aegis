"""Local web dashboard — a self-contained single-page app (no build step).

`aegis dashboard` serves a control UI at http://127.0.0.1:9119: chat, sessions,
memory, skills, tools, and status. The frontend lives in static/dashboard.html
(shipped as package data — a plain file, no node toolchain) and talks to the
JSON API below. Binds loopback by default and can require the configured
dashboard token; do not expose it publicly without trusted network controls.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import __version__
from .config import Config


def _page() -> bytes:
    """The SPA frontend, read from package data (falls back to a stub if missing)."""
    try:
        from importlib import resources
        return (resources.files("aegis") / "static" / "dashboard.html").read_bytes()
    except Exception:  # noqa: BLE001 - broken install; keep the API usable
        return b"<h1>AEGIS</h1><p>dashboard.html missing from install; API is at /api/*</p>"





def _redacted_config(config: Config) -> dict:
    """Flattened config for the UI, with secret-looking values masked (never echo keys)."""
    import re as _re
    secret = _re.compile(r"key|token|secret|password|client_secret", _re.IGNORECASE)
    out: dict[str, str] = {}

    def walk(prefix, node):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(f"{prefix}.{k}" if prefix else k, v)
        else:
            val = node
            if secret.search(prefix) and val:
                val = "••••••" + str(val)[-4:] if len(str(val)) > 4 else "••••••"
            out[prefix] = val

    walk("", getattr(config, "data", {}) or {})
    return out


_COMMON_KEYS = [
    "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY", "OPENROUTER_API_KEY",
    "GROQ_API_KEY", "DEEPSEEK_API_KEY", "XAI_API_KEY", "MISTRAL_API_KEY",
    "TELEGRAM_BOT_TOKEN", "DISCORD_BOT_TOKEN", "SLACK_BOT_TOKEN", "SLACK_APP_TOKEN",
    "NTFY_TOPIC", "TAVILY_API_KEY", "BRAVE_API_KEY",
]


def _env_keys() -> list:
    """Known + present env keys with set-status only — values are NEVER returned."""
    from . import config as cfg
    present = set()
    p = cfg.env_path()
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                present.add(line.split("=", 1)[0].strip())
    names = list(dict.fromkeys(_COMMON_KEYS + sorted(present)))
    return [{"key": k, "set": k in present} for k in names]


def _profiles(config: Config) -> dict:
    """Available personality files (workspace/personalities/*.md) + the active one."""
    from . import config as cfg
    d = cfg.workspace_dir() / "personalities"
    names = sorted(p.stem for p in d.glob("*.md")) if d.exists() else []
    return {"active": config.get("agent.personality") or "", "available": names}


def _system_info() -> dict:
    """Host + install facts and recent checkpoints for the System tab (no psutil dependency)."""
    import platform
    import shutil
    from . import __version__
    from . import config as cfg
    home = cfg.get_home()
    du = shutil.disk_usage(str(home))
    try:
        from .checkpoints import CheckpointStore
        cps = [{"id": c.id, "label": c.label, "at": getattr(c, "created_at", "")}
               for c in CheckpointStore().list()[:20]]
    except Exception:  # noqa: BLE001
        cps = []
    return {
        "version": __version__,
        "python": platform.python_version(),
        "platform": f"{platform.system()} ({platform.machine()})",
        "aegis_home": str(home),
        "disk_free_gb": round(du.free / 1e9, 1),
        "disk_total_gb": round(du.total / 1e9, 1),
        "checkpoints": cps,
    }


def make_handler(config: Config):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _authorized(self) -> bool:
            token = config.get("server.dashboard_token")
            if not token:
                return True
            parsed = urlparse(self.path)
            query_token = parse_qs(parsed.query).get("token", [""])[0]
            auth = self.headers.get("Authorization", "")
            header_token = auth.removeprefix("Bearer ").strip() if auth.startswith("Bearer ") else ""
            return token in (query_token, header_token, self.headers.get("X-Aegis-Token", ""))

        def _json(self, obj):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(obj).encode())

        def _unauthorized(self):
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "unauthorized"}).encode())

        def do_GET(self):  # noqa: N802
            from .session import SessionStore
            u = urlparse(self.path)
            path, q = u.path, parse_qs(u.query)
            if path == "/":
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(_page())
            elif not self._authorized():
                self._unauthorized()
            elif path == "/api/status":
                from .skills import SkillsLoader
                from .tools.registry import default_registry
                self._json({"version": __version__, "provider": config.get("model.provider"),
                            "model": config.get("model.default"),
                            "sessions": len(SessionStore().list(9999)),
                            "skills": len(SkillsLoader(config).available()),
                            "tools": len(default_registry().all()),
                            "exec_mode": config.get("tools.exec_mode")})
            elif path == "/events":
                self._stream_events()
            elif path == "/api/kanban":
                from .kanban import KanbanStore
                ks = KanbanStore()
                self._json({s: [{"id": t.id, "title": t.title, "body": t.body,
                                 "assignee": t.assignee, "priority": t.priority}
                                for t in ks.list(status=s)]
                            for s in ("ready", "in_progress", "done", "blocked")})
            elif path == "/api/cron":
                from .cron import CronStore
                self._json([{"id": j.id, "schedule": j.schedule, "prompt": j.prompt,
                             "enabled": j.enabled, "one_shot": bool(j.run_at)}
                            for j in CronStore().list()])
            elif path == "/api/config":
                self._json(_redacted_config(config))
            elif path == "/api/models":
                from .onboarding import MODEL_PRESETS
                from .providers.registry import list_providers
                self._json({
                    "provider": config.get("model.provider"),
                    "model": config.get("model.default"),
                    "providers": sorted(list_providers()),
                    "presets": {p: [m for m, _ in MODEL_PRESETS.get(p, [])] for p in MODEL_PRESETS},
                })
            elif path == "/api/analytics":
                from .usage_log import cost_report, daily_series
                days = int((q.get("days", ["30"])[0]) or 30)
                rep = cost_report(days)
                rep["series"] = daily_series(days)
                self._json(rep)
            elif path == "/api/keys":
                self._json(_env_keys())
            elif path == "/api/pairing":
                from .gateway.pairing import PairingStore
                self._json(PairingStore().list())
            elif path == "/api/mcp":
                servers = config.get("mcp.servers", {}) or {}
                self._json([{"name": n, "command": (s or {}).get("command", ""),
                             "args": (s or {}).get("args", [])} for n, s in servers.items()])
            elif path == "/api/webhooks":
                from .webhook import WebhookStore
                self._json([{"name": w.name, "prompt": w.prompt} for w in WebhookStore().list()])
            elif path == "/api/curator":
                from .curator import apply_transitions
                self._json(apply_transitions(dry_run=True))      # preview
            elif path == "/api/plugins":
                from .plugins import load_plugins
                api = load_plugins(quiet=True)
                self._json({"loaded": [p.name for p in api.files],
                            "errors": [{"file": f.name, "error": e} for f, e in api.errors],
                            "tools": len(api.tools), "channels": list(api.channels)})
            elif path == "/api/profiles":
                self._json(_profiles(config))
            elif path == "/api/system":
                self._json(_system_info())
            elif path == "/api/logs":
                from . import config as _cfg
                lp = _cfg.logs_dir() / "aegis.log"
                lines = lp.read_text(errors="replace").splitlines()[-200:] if lp.exists() else []
                self._json({"path": str(lp), "lines": lines})
            elif path == "/api/sessions":
                self._json(SessionStore().list(100))
            elif path == "/api/session":
                s = SessionStore().load(q.get("id", [""])[0])
                self._json({"messages": [{"role": m.role, "content": m.content}
                                         for m in (s.messages if s else []) if m.content]})
            elif path == "/api/memory":
                from .memory import MemoryStore
                ms = MemoryStore()
                self._json({"memory": ms.raw("memory"), "user": ms.raw("user")})
            elif path == "/api/skills":
                from .skills import SkillsLoader
                self._json([{"name": s.name, "description": s.description}
                            for s in sorted(SkillsLoader(config).available(), key=lambda s: s.name)])
            elif path == "/api/tools":
                from .tools.registry import default_registry
                self._json([{"name": t.name, "description": t.description.splitlines()[0],
                             "groups": t.groups} for t in default_registry().all()])
            else:
                self._json({"error": "not found"})

        def _stream_events(self):
            """Server-Sent Events: live mirror of gateway/agent activity (EventSource client)."""
            import queue as _queue

            from .eventbus import BUS
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            sub = BUS.subscribe()
            try:
                while True:
                    try:
                        ev = sub.get(timeout=15)
                        self.wfile.write(f"data: {json.dumps(ev)}\n\n".encode())
                    except _queue.Empty:
                        self.wfile.write(b": keepalive\n\n")   # hold the connection open
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ValueError):
                pass                                            # client disconnected
            finally:
                BUS.unsubscribe(sub)

        def do_POST(self):  # noqa: N802
            if not self._authorized():
                self._unauthorized()
                return
            from .agent.agent import Agent
            from .session import Session, SessionStore
            n = int(self.headers.get("content-length", 0))
            body = json.loads(self.rfile.read(n) or b"{}")
            ppath = urlparse(self.path).path
            if ppath == "/api/kanban":
                from .kanban import KanbanStore
                ks = KanbanStore()
                act = body.get("action")
                if act == "create":
                    t = ks.create((body.get("title") or "untitled").strip(), body.get("body", ""))
                    return self._json({"id": t.id})
                if act == "move" and body.get("id") and \
                        body.get("status") in ("ready", "in_progress", "done", "blocked"):
                    ks._set_status(body["id"], body["status"])
                    return self._json({"ok": True})
                if act == "decompose" and body.get("goal"):
                    from .kanban_auto import decompose
                    cards = decompose(body["goal"], config, store=ks)
                    return self._json({"ok": True, "created": len(cards)})
                if act == "run":
                    import threading
                    from .kanban_auto import run_board
                    threading.Thread(target=run_board, args=(config,),
                                     kwargs={"store": ks}, daemon=True).start()
                    return self._json({"ok": True, "started": True})
                return self._json({"error": "bad kanban request"})
            if ppath == "/api/cron":
                from .cron import CronStore
                cs = CronStore()
                act = body.get("action")
                if act == "add" and body.get("schedule") and body.get("prompt"):
                    j = cs.add(body["schedule"], body["prompt"], body.get("channel", ""))
                    return self._json({"id": j.id})
                if act == "remove" and body.get("id"):
                    return self._json({"ok": cs.remove(body["id"])})
                if act == "toggle" and body.get("id"):
                    return self._json({"ok": cs.set_enabled(body["id"], bool(body.get("enabled", True)))})
                return self._json({"error": "bad cron request"})
            if ppath == "/api/config":
                key, val = body.get("key"), body.get("value")
                if key:
                    config.set(key, val)
                    return self._json({"ok": True})
                return self._json({"error": "missing key"})
            if ppath == "/api/models":
                prov, model = body.get("provider"), body.get("model")
                if prov:
                    config.set("model.provider", prov)
                if model:
                    config.set("model.default", model)
                return self._json({"ok": True, "provider": config.get("model.provider"),
                                   "model": config.get("model.default")})
            if ppath == "/api/keys":
                from .config import set_env_var
                if body.get("key"):
                    set_env_var(body["key"].strip(), body.get("value", ""))
                    return self._json({"ok": True})
                return self._json({"error": "missing key"})
            if ppath == "/api/pairing":
                from .gateway.pairing import PairingStore
                ps = PairingStore()
                act, plat = body.get("action"), body.get("platform", "")
                if act == "approve" and body.get("code"):
                    return self._json({"ok": ps.approve(plat, body["code"])})
                if act == "revoke" and body.get("user_id"):
                    return self._json({"ok": ps.revoke(plat, body["user_id"])})
                return self._json({"error": "bad pairing request"})
            if ppath == "/api/system":
                if body.get("action") == "backup":
                    from .backup import create_backup
                    return self._json({"ok": True, "path": str(create_backup())})
                return self._json({"error": "unknown system action"})
            if ppath == "/api/curator":
                from .curator import apply_transitions
                return self._json(apply_transitions(dry_run=False))   # apply
            if ppath == "/api/profiles":
                config.set("agent.personality", body.get("name") or "")
                return self._json({"ok": True, "active": config.get("agent.personality")})
            if ppath == "/api/mcp":
                servers = dict(config.get("mcp.servers", {}) or {})
                act = body.get("action")
                if act == "add" and body.get("name") and body.get("command"):
                    parts = str(body["command"]).split()
                    servers[body["name"]] = {"command": parts[0], "args": parts[1:]}
                    config.data.setdefault("mcp", {})["servers"] = servers
                    config.save()
                    return self._json({"ok": True})
                if act == "remove" and body.get("name") in servers:
                    servers.pop(body["name"])
                    config.data.setdefault("mcp", {})["servers"] = servers
                    config.save()
                    return self._json({"ok": True})
                return self._json({"error": "bad mcp request"})
            if ppath == "/api/webhooks":
                from .webhook import WebhookStore
                ws = WebhookStore()
                act = body.get("action")
                if act == "add" and body.get("name") and body.get("prompt"):
                    ws.add(body["name"], body["prompt"])
                    return self._json({"ok": True})
                if act == "remove" and body.get("name"):
                    return self._json({"ok": ws.remove(body["name"])})
                return self._json({"error": "bad webhook request"})
            store = SessionStore()
            session = store.load(body.get("session_id") or "") or Session.create()
            agent = Agent.create(config, session=session, store=store)
            try:
                reply = agent.run(body.get("message", "")).content
            except Exception as e:  # noqa: BLE001
                reply = f"error: {e}"
            self._json({"reply": reply or "(no response)", "session_id": session.id})

    return H


def _dashboard_url(config: Config, host: str, port: int) -> str:
    token = config.get("server.dashboard_token")
    base = f"http://{host}:{port}"
    return f"{base}/?token={token}" if token else base


def serve_dashboard(config: Config, host: str = "127.0.0.1", port: int = 9119,
                    open_browser: bool = False) -> None:
    handler = make_handler(config)
    requested = port
    for candidate in range(port, port + 50):       # auto-select if the port is occupied
        try:
            httpd = ThreadingHTTPServer((host, candidate), handler)
            break
        except OSError:
            continue
    else:
        raise OSError(f"no free port in {requested}–{requested + 49} on {host}")
    port = httpd.server_address[1]
    if port != requested:
        print(f"  (port {requested} busy — using {port})")
    url = _dashboard_url(config, host, port)
    print(f"AEGIS control panel → {url}")
    print("  (leave this running; press Ctrl+C to stop)")
    if open_browser:
        import threading
        import webbrowser
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\ndashboard stopped.")


def cmd_dashboard(args, config: Config) -> int:
    host = getattr(args, "host", None) or config.get("server.dashboard_host", "127.0.0.1")
    port = getattr(args, "port", None) or config.get("server.dashboard_port", 9119)
    # Beginner-friendly default: open the browser unless asked not to.
    open_browser = not getattr(args, "no_open", False)
    serve_dashboard(config, host=host, port=int(port), open_browser=open_browser)
    return 0
