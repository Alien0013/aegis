"""Local web dashboard — a self-contained single-page app (no build step).

`aegis dashboard` serves a control UI at http://127.0.0.1:9119: chat, sessions,
memory, skills, tools, and status. Binds loopback by default and can require the
configured dashboard token; do not expose it publicly without trusted network
controls.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import __version__
from .config import Config

PAGE = """<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>AEGIS</title>
<style>
:root{--bg:#0d0b14;--panel:#16131f;--line:#2a2540;--fg:#e7e3f4;--mut:#9a93b8;--acc:#a06bff;--acc2:#6be0ff}
*{box-sizing:border-box}body{margin:0;font:14px/1.5 ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto;background:var(--bg);color:var(--fg)}
header{display:flex;align-items:center;gap:12px;padding:12px 18px;border-bottom:1px solid var(--line);background:var(--panel)}
.logo{font-weight:800;letter-spacing:.18em;background:linear-gradient(90deg,var(--acc),var(--acc2));-webkit-background-clip:text;background-clip:text;color:transparent}
.meta{color:var(--mut);font-size:12px;margin-left:auto}
.wrap{display:flex;height:calc(100vh - 53px)}
nav{width:148px;border-right:1px solid var(--line);background:var(--panel);padding:10px 8px;display:flex;flex-direction:column;gap:4px}
nav button{all:unset;cursor:pointer;padding:9px 12px;border-radius:8px;color:var(--mut);font-weight:600}
nav button.on,nav button:hover{background:#221c33;color:var(--fg)}
main{flex:1;overflow:auto;padding:18px}
.card{border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin-bottom:10px;background:var(--panel)}
.card b{color:var(--acc2)}.mut{color:var(--mut)}
#log{display:flex;flex-direction:column;gap:10px;max-width:820px}
.msg{padding:10px 14px;border-radius:12px;max-width:80%;white-space:pre-wrap;word-wrap:break-word}
.user{align-self:flex-end;background:#2a2140;border:1px solid var(--acc)}
.assistant{align-self:flex-start;background:var(--panel);border:1px solid var(--line)}
.bar{display:flex;gap:8px;max-width:820px;margin-top:12px}
input,textarea{flex:1;background:var(--panel);border:1px solid var(--line);color:var(--fg);border-radius:8px;padding:10px 12px;font:inherit}
button.send{background:var(--acc);color:#0d0b14;border:0;border-radius:8px;padding:0 18px;font-weight:700;cursor:pointer}
.row{display:flex;justify-content:space-between;gap:10px;padding:8px 0;border-bottom:1px solid var(--line);cursor:pointer}
.row:hover{color:var(--acc2)}.pill{font-size:11px;color:var(--mut);border:1px solid var(--line);border-radius:20px;padding:1px 8px}
h2{margin:.2em 0 .6em;font-size:16px}pre{white-space:pre-wrap;word-wrap:break-word;margin:.4em 0 0}
</style></head><body>
<header><span class=logo>&#9670; AEGIS</span><span id=sub class=mut></span><span class=meta id=stat></span></header>
<div class=wrap><nav id=nav></nav><main id=view></main></div>
<script>
const V=document.getElementById('view'),NAV=document.getElementById('nav');let sid=null,_es=null;
const TABS=['Chat','Live','Kanban','Sessions','Memory','Skills','Tools','Status'];
const qs=new URLSearchParams(location.search),tok=qs.get('token')||localStorage.aegisToken||'';if(tok)localStorage.aegisToken=tok;
function withTok(p){return '/api/'+p+(tok?(p.includes('?')?'&':'?')+'token='+encodeURIComponent(tok):'')}
async function api(p){const r=await fetch(withTok(p));return r.json()}
async function post(p,b){const r=await fetch(withTok(p),{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(b)});return r.json()}
function esc(s){return (s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
TABS.forEach(t=>{const b=document.createElement('button');b.textContent=t;b.onclick=()=>show(t,b);NAV.appendChild(b)});
async function boot(){const s=await api('status');document.getElementById('sub').textContent='v'+s.version+' \\u00b7 '+s.provider+'/'+s.model;
document.getElementById('stat').textContent=s.sessions+' sessions \\u00b7 '+s.skills+' skills \\u00b7 '+s.tools+' tools';show('Chat',NAV.children[0])}
function show(t,btn){if(_es){_es.close();_es=null}[...NAV.children].forEach(b=>b.classList.toggle('on',b===btn));({Chat:chat,Live:live,Kanban:kanban,Sessions:sessions,Memory:memory,Skills:skills,Tools:tools,Status:status}[t])()}
async function kanban(){const cols=['ready','in_progress','done','blocked'];const d=await api('kanban');
V.innerHTML='<h2>Kanban</h2><div class=bar><input id=kt placeholder="New task title\\u2026"><button class=send id=kadd>Add</button></div><div id=board style="display:flex;gap:12px;align-items:flex-start"></div>';
const board=document.getElementById('board');
cols.forEach(c=>{const col=document.createElement('div');col.style='flex:1;min-width:0';col.innerHTML='<h3 style="text-transform:capitalize">'+c.replace('_',' ')+' ('+(d[c]||[]).length+')</h3>';
(d[c]||[]).forEach(t=>{const card=document.createElement('div');card.className='card';card.innerHTML='<b>'+esc(t.title)+'</b>'+(t.body?'<pre>'+esc(t.body)+'</pre>':'')+'<div class=mut style="font-size:11px">'+cols.filter(s=>s!==c).map(s=>'<a href=# data-id="'+t.id+'" data-s="'+s+'">\\u2192 '+s.replace('_',' ')+'</a>').join(' \\u00b7 ')+'</div>';col.appendChild(card)});board.appendChild(col)});
board.querySelectorAll('a[data-s]').forEach(a=>a.onclick=async e=>{e.preventDefault();await post('kanban',{action:'move',id:a.dataset.id,status:a.dataset.s});kanban()});
document.getElementById('kadd').onclick=async()=>{const t=document.getElementById('kt').value.trim();if(!t)return;await post('kanban',{action:'create',title:t});kanban()}}
function live(){V.innerHTML='<h2>Live activity</h2><div id=feed><p class=mut>waiting for gateway activity\\u2026</p></div>';const feed=document.getElementById('feed');
_es=new EventSource('/events'+(tok?'?token='+encodeURIComponent(tok):''));
_es.onmessage=e=>{try{const d=JSON.parse(e.data);const r=document.createElement('div');r.className='row';r.innerHTML='<span>'+esc(d.platform||'')+' \\u00b7 '+esc(d.type)+'</span><span class=pill>'+esc((d.text||d.name||'').slice(0,80))+'</span>';if(feed.firstChild&&feed.firstChild.tagName==='P')feed.innerHTML='';feed.prepend(r)}catch(_){}}}
function chat(){V.innerHTML='<h2>Chat</h2><div id=log></div><div class=bar><input id=inp placeholder="Message AEGIS\\u2026" autofocus><button class=send id=go>Send</button></div>';
const log=document.getElementById('log'),inp=document.getElementById('inp');
function add(role,txt){const d=document.createElement('div');d.className='msg '+role;d.textContent=txt;log.appendChild(d);d.scrollIntoView()}
async function send(){const m=inp.value.trim();if(!m)return;add('user',m);inp.value='';const w=document.createElement('div');w.className='msg assistant';w.textContent='\\u2026';log.appendChild(w);
const r=await post('chat',{message:m,session_id:sid});sid=r.session_id;w.textContent=r.reply}
document.getElementById('go').onclick=send;inp.onkeydown=e=>{if(e.key==='Enter')send()}}
async function sessions(){const s=await api('sessions');V.innerHTML='<h2>Sessions</h2>'+(s.map(x=>`<div class=row onclick="openS('${x.id}')"><span>${esc(x.title)}</span><span class=pill>${x.updated_at}</span></div>`).join('')||'<p class=mut>none</p>')}
window.openS=async id=>{const s=await api('session?id='+id);V.innerHTML='<h2>'+esc(id)+'</h2>'+s.messages.map(m=>`<div class=card><b>${m.role}</b><pre>${esc(m.content)}</pre></div>`).join('')}
async function memory(){const m=await api('memory');V.innerHTML='<h2>Memory</h2><div class=card><b>MEMORY.md</b><pre>'+esc(m.memory||'(empty)')+'</pre></div><div class=card><b>USER.md</b><pre>'+esc(m.user||'(empty)')+'</pre></div>'}
async function skills(){const s=await api('skills');V.innerHTML='<h2>Skills ('+s.length+')</h2>'+s.map(x=>`<div class=card><b>${esc(x.name)}</b> \\u2014 ${esc(x.description)}</div>`).join('')}
async function tools(){const s=await api('tools');V.innerHTML='<h2>Tools ('+s.length+')</h2>'+s.map(x=>`<div class=row><span>${esc(x.name)}</span><span class=pill>${(x.groups||[]).join(',')||'safe'}</span></div>`).join('')}
async function status(){const s=await api('status');V.innerHTML='<h2>Status</h2>'+Object.entries(s).map(([k,v])=>`<div class=row><span class=mut>${k}</span><span>${esc(''+v)}</span></div>`).join('')}
boot();
</script></body></html>"""


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
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(PAGE.encode())
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
            if urlparse(self.path).path == "/api/kanban":
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
                return self._json({"error": "bad kanban request"})
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
    httpd = ThreadingHTTPServer((host, port), make_handler(config))
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
