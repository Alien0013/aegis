"""Local web dashboard: a single self-contained HTML page over the stdlib
``http.server``.

Serves an overview of the running AEGIS install — provider/model, recent
sessions, and persistent memory — plus a chat box wired to the agent. Bound to
``127.0.0.1`` only; there is no auth, so it must never be exposed publicly.

Routes::

    GET  /              the dashboard page (inline CSS/JS, no external assets)
    GET  /api/sessions  JSON: {provider, model, sessions[], memory, user}
    POST /api/chat      JSON in {message} -> {reply}
"""

from __future__ import annotations

import html
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .agent.agent import Agent
from .config import Config
from .memory import MemoryStore
from .session import SessionStore


def _overview(config: Config) -> dict:
    """Snapshot of provider/model, recent sessions, and memory for the UI."""
    store = MemoryStore()
    return {
        "provider": config.get("model.provider", "?"),
        "model": config.get("model.default", "?"),
        "sessions": SessionStore().list(limit=25),
        "memory": store.raw("memory"),
        "user": store.raw("user"),
    }


def _chat(config: Config, message: str) -> str:
    """Run a one-shot turn through a fresh agent and return the reply text."""
    agent = Agent.create(config)
    return agent.run(message).content


PAGE = """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AEGIS dashboard</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin: 0; font: 14px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace;
         background: #0d1117; color: #c9d1d9; }
  header { padding: 14px 20px; border-bottom: 1px solid #21262d;
           display: flex; align-items: baseline; gap: 14px; }
  header h1 { font-size: 15px; margin: 0; letter-spacing: 2px; color: #58a6ff; }
  header .model { color: #8b949e; }
  main { display: grid; grid-template-columns: 320px 1fr; gap: 0; height: calc(100vh - 51px); }
  aside { border-right: 1px solid #21262d; overflow-y: auto; padding: 16px 18px; }
  aside h2 { font-size: 11px; text-transform: uppercase; letter-spacing: 1px;
             color: #8b949e; margin: 22px 0 8px; }
  aside h2:first-child { margin-top: 0; }
  .sess { padding: 6px 8px; border-radius: 6px; border: 1px solid transparent; }
  .sess:hover { border-color: #21262d; background: #161b22; }
  .sess .t { color: #c9d1d9; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .sess .d { color: #6e7681; font-size: 12px; }
  pre.mem { white-space: pre-wrap; word-break: break-word; color: #adbac7;
            background: #161b22; border: 1px solid #21262d; border-radius: 6px;
            padding: 10px; font-size: 12.5px; margin: 0; }
  .empty { color: #6e7681; }
  section.chat { display: flex; flex-direction: column; height: 100%; }
  #log { flex: 1; overflow-y: auto; padding: 18px 22px; }
  .msg { margin: 0 0 14px; max-width: 80%; }
  .msg.user { margin-left: auto; }
  .msg .who { font-size: 11px; color: #6e7681; margin-bottom: 3px; }
  .msg .body { white-space: pre-wrap; word-break: break-word; padding: 9px 12px;
               border-radius: 8px; background: #161b22; border: 1px solid #21262d; }
  .msg.user .body { background: #1f2d3d; border-color: #2a3f55; }
  form { display: flex; gap: 8px; padding: 14px 22px; border-top: 1px solid #21262d; }
  textarea { flex: 1; resize: none; height: 44px; padding: 11px; border-radius: 8px;
             background: #0d1117; color: #c9d1d9; border: 1px solid #30363d;
             font: inherit; }
  textarea:focus { outline: none; border-color: #58a6ff; }
  button { padding: 0 18px; border-radius: 8px; border: 1px solid #238636;
           background: #238636; color: #fff; font: inherit; cursor: pointer; }
  button:disabled { opacity: .5; cursor: default; }
</style>
</head>
<body>
<header>
  <h1>AEGIS</h1>
  <span class="model" id="model">connecting…</span>
</header>
<main>
  <aside>
    <h2>Recent sessions</h2>
    <div id="sessions"><span class="empty">loading…</span></div>
    <h2>Memory</h2>
    <pre class="mem" id="memory">…</pre>
    <h2>User profile</h2>
    <pre class="mem" id="user">…</pre>
  </aside>
  <section class="chat">
    <div id="log"></div>
    <form id="form">
      <textarea id="input" placeholder="Message the agent…" autofocus></textarea>
      <button id="send" type="submit">Send</button>
    </form>
  </section>
</main>
<script>
const $ = (id) => document.getElementById(id);
const esc = (s) => { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; };

function addMsg(who, text) {
  const el = document.createElement('div');
  el.className = 'msg ' + (who === 'you' ? 'user' : 'agent');
  el.innerHTML = '<div class="who">' + esc(who) + '</div><div class="body">' + esc(text) + '</div>';
  $('log').appendChild(el);
  $('log').scrollTop = $('log').scrollHeight;
  return el;
}

async function refresh() {
  try {
    const r = await fetch('/api/sessions');
    const d = await r.json();
    $('model').textContent = d.provider + ' · ' + d.model;
    $('memory').textContent = d.memory || '(empty)';
    $('user').textContent = d.user || '(empty)';
    if (!d.memory) $('memory').classList.add('empty');
    if (!d.user) $('user').classList.add('empty');
    const box = $('sessions');
    box.innerHTML = '';
    if (!d.sessions.length) { box.innerHTML = '<span class="empty">none yet</span>'; return; }
    for (const s of d.sessions) {
      const el = document.createElement('div');
      el.className = 'sess';
      el.innerHTML = '<div class="t">' + esc(s.title || s.id) + '</div>' +
                     '<div class="d">' + esc((s.updated_at || '').replace('T', ' ')) + '</div>';
      box.appendChild(el);
    }
  } catch (e) { $('model').textContent = 'offline'; }
}

$('form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const text = $('input').value.trim();
  if (!text) return;
  $('input').value = '';
  $('send').disabled = true;
  addMsg('you', text);
  const pending = addMsg('agent', '…');
  try {
    const r = await fetch('/api/chat', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({message: text}),
    });
    const d = await r.json();
    pending.querySelector('.body').textContent = d.reply || d.error || '(no reply)';
  } catch (err) {
    pending.querySelector('.body').textContent = 'error: ' + err;
  }
  $('send').disabled = false;
  $('input').focus();
  refresh();
});

$('input').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); $('form').requestSubmit(); }
});

refresh();
setInterval(refresh, 15000);
</script>
</body>
</html>
"""


def make_handler(config: Config):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet
            pass

        def _json(self, code: int, obj: dict) -> None:
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            path = self.path.split("?", 1)[0].rstrip("/") or "/"
            if path == "/":
                body = PAGE.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if path == "/api/sessions":
                return self._json(200, _overview(config))
            return self._json(404, {"error": "not found"})

        def do_POST(self):  # noqa: N802
            if self.path.split("?", 1)[0].rstrip("/") != "/api/chat":
                return self._json(404, {"error": "not found"})
            n = int(self.headers.get("content-length", 0))
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except json.JSONDecodeError:
                return self._json(400, {"error": "invalid json"})
            message = (body.get("message") or "").strip()
            if not message:
                return self._json(400, {"error": "message is required"})
            try:
                reply = _chat(config, message)
            except Exception as e:  # noqa: BLE001 - surface any agent failure to the UI
                return self._json(200, {"error": f"{type(e).__name__}: {e}"})
            return self._json(200, {"reply": reply})

    return Handler


def serve_dashboard(config: Config, host: str = "127.0.0.1", port: int = 9119) -> None:
    """Run the dashboard server (blocking). Always binds loopback."""
    if host not in ("127.0.0.1", "localhost", "::1"):
        # The dashboard is unauthenticated; refuse to listen on a public address.
        print(f"refusing non-loopback bind '{host}'; using 127.0.0.1 instead.")
        host = "127.0.0.1"
    httpd = ThreadingHTTPServer((host, port), make_handler(config))
    print(f"AEGIS dashboard on http://{host}:{port}  (Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\ndashboard stopped.")
    finally:
        httpd.server_close()


def cmd_dashboard(args, config: Config) -> int:
    host = getattr(args, "host", None) or config.get("server.host", "127.0.0.1")
    port = getattr(args, "port", None) or 9119
    serve_dashboard(config, host=host, port=int(port))
    return 0
