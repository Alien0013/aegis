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
const TABS=['Chat','Live','Kanban','Cron','Models','Analytics','Keys','Pairing','Sessions','Memory','Skills','Tools','Logs','System','Config','Status'];
const qs=new URLSearchParams(location.search),tok=qs.get('token')||localStorage.aegisToken||'';if(tok)localStorage.aegisToken=tok;
function withTok(p){return '/api/'+p+(tok?(p.includes('?')?'&':'?')+'token='+encodeURIComponent(tok):'')}
async function api(p){const r=await fetch(withTok(p));return r.json()}
async function post(p,b){const r=await fetch(withTok(p),{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(b)});return r.json()}
function esc(s){return (s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
TABS.forEach(t=>{const b=document.createElement('button');b.textContent=t;b.onclick=()=>show(t,b);NAV.appendChild(b)});
async function boot(){const s=await api('status');document.getElementById('sub').textContent='v'+s.version+' \\u00b7 '+s.provider+'/'+s.model;
document.getElementById('stat').textContent=s.sessions+' sessions \\u00b7 '+s.skills+' skills \\u00b7 '+s.tools+' tools';show('Chat',NAV.children[0])}
function show(t,btn){if(_es){_es.close();_es=null}[...NAV.children].forEach(b=>b.classList.toggle('on',b===btn));({Chat:chat,Live:live,Kanban:kanban,Cron:cron,Models:models,Analytics:analytics,Keys:keys,Pairing:pairing,Sessions:sessions,Memory:memory,Skills:skills,Tools:tools,Logs:logs,System:system,Config:cfg,Status:status}[t])()}
async function logs(){const d=await api('logs');
V.innerHTML='<h2>Logs</h2><p class=mut>'+esc(d.path)+'</p><pre style="max-height:70vh;overflow:auto;font-size:12px">'+esc((d.lines||[]).join('\\n')||'(empty)')+'</pre>'}
async function system(){const s=await api('system');
const cp=(s.checkpoints||[]).map(c=>`<div class=row><span>${esc(c.id)} ${esc(c.label||'')}</span><span class=pill>${esc(c.at||'')}</span></div>`).join('')||'<p class=mut>no checkpoints</p>';
V.innerHTML='<h2>System</h2>'+[['version',s.version],['python',s.python],['platform',s.platform],['home',s.aegis_home],['disk',s.disk_free_gb+' / '+s.disk_total_gb+' GB free']].map(([k,v])=>`<div class=row><span class=mut>${k}</span><span>${esc(''+v)}</span></div>`).join('')+'<div class=bar><button class=send id=bk>Backup now</button><span id=bkr class=mut></span></div><h3>Checkpoints</h3>'+cp;
document.getElementById('bk').onclick=async()=>{document.getElementById('bkr').textContent='backing up\\u2026';const r=await post('system',{action:'backup'});document.getElementById('bkr').textContent=r.path?('\\u2705 '+r.path):'failed'}}
async function keys(){const d=await api('keys');
V.innerHTML='<h2>API keys</h2><p class=mut>Stored in ~/.aegis/.env (chmod 600). Values are never shown.</p><div class=bar><input id=kk placeholder="KEY (e.g. OPENAI_API_KEY)" style="flex:0 0 240px"><input id=kv placeholder="value" type=password><button class=send id=kset>Save</button></div>'+d.map(x=>`<div class=row><span>${esc(x.key)}</span><span class=pill>${x.set?'\\u2705 set':'\\u2014 not set'} <a href=# data-k="${esc(x.key)}">set</a></span></div>`).join('');
V.querySelectorAll('a[data-k]').forEach(a=>a.onclick=e=>{e.preventDefault();document.getElementById('kk').value=a.dataset.k;document.getElementById('kv').focus()});
document.getElementById('kset').onclick=async()=>{const k=document.getElementById('kk').value.trim(),v=document.getElementById('kv').value;if(!k)return;await post('keys',{key:k,value:v});document.getElementById('kv').value='';keys()}}
async function pairing(){const d=await api('pairing');const pend=d.pending||{},appr=d.approved||{};
let h='<h2>Pairing</h2><h3>Pending</h3>';const pe=Object.entries(pend);
h+=pe.length?pe.map(([plat,reqs])=>Object.entries(reqs||{}).map(([code,info])=>`<div class=row><span>${esc(plat)} \\u00b7 user ${esc(''+(info&&info.user_id||''))} \\u00b7 code <b>${esc(code)}</b></span><a href=# data-ap="${esc(plat)}" data-code="${esc(code)}">approve</a></div>`).join('')).join(''):'<p class=mut>none</p>';
h+='<h3>Approved</h3>';const ae=Object.entries(appr);
h+=ae.length?ae.map(([plat,users])=>(Array.isArray(users)?users:Object.keys(users||{})).map(u=>`<div class=row><span>${esc(plat)} \\u00b7 ${esc(''+u)}</span><a href=# data-rv="${esc(plat)}" data-u="${esc(''+u)}">revoke</a></div>`).join('')).join(''):'<p class=mut>none</p>';
V.innerHTML=h;
V.querySelectorAll('a[data-ap]').forEach(a=>a.onclick=async e=>{e.preventDefault();await post('pairing',{action:'approve',platform:a.dataset.ap,code:a.dataset.code});pairing()});
V.querySelectorAll('a[data-rv]').forEach(a=>a.onclick=async e=>{e.preventDefault();await post('pairing',{action:'revoke',platform:a.dataset.rv,user_id:a.dataset.u});pairing()})}
async function models(){const d=await api('models');
const opts=(d.presets[d.provider]||[]).map(m=>`<option ${m===d.model?'selected':''}>${esc(m)}</option>`).join('');
V.innerHTML='<h2>Model</h2><div class=card>Active: <b>'+esc(d.provider)+'</b> / <b>'+esc(d.model)+'</b></div><div class=bar><select id=mprov style="flex:0 0 200px">'+d.providers.map(p=>`<option ${p===d.provider?'selected':''}>${esc(p)}</option>`).join('')+'</select><select id=mmodel>'+opts+'</select><button class=send id=mset>Set</button></div><p class=mut>Switch the provider to refresh its model list.</p>';
const psel=document.getElementById('mprov'),msel=document.getElementById('mmodel');
psel.onchange=()=>{const ms=d.presets[psel.value]||[];msel.innerHTML=ms.map(m=>`<option>${esc(m)}</option>`).join('')||'<option>(type in Config)</option>'};
document.getElementById('mset').onclick=async()=>{await post('models',{provider:psel.value,model:msel.value});models()}}
async function analytics(){const r=await api('analytics?days=30');
const rows=Object.entries(r.by_model||{}).map(([m,v])=>`<div class=row><span>${esc(m)}</span><span class=pill>${v.calls} calls · $${(v.cost_usd||0).toFixed(4)}</span></div>`).join('');
V.innerHTML='<h2>Analytics (30 days)</h2><div class=card><b>'+r.calls+'</b> calls · <b>$'+(r.total_cost_usd||0).toFixed(4)+'</b> total · '+(r.cache_read_tokens||0)+' cached tokens</div>'+(rows||'<p class=mut>no usage yet</p>')}
async function cron(){const j=await api('cron');
V.innerHTML='<h2>Scheduled tasks</h2><div class=bar><input id=cs placeholder="schedule (e.g. every 2h · at 17:00 · 0 9 * * 1)" style="flex:0 0 240px"><input id=cp placeholder="prompt to run\\u2026"><button class=send id=cadd>Add</button></div>'+(j.length?'':'<p class=mut>no jobs</p>')+j.map(x=>`<div class=row><span>${x.enabled?'\\u25b6':'\\u23f8'} <b>${esc(x.schedule)}</b> \\u2014 ${esc(x.prompt)}${x.one_shot?' <span class=pill>once</span>':''}</span><span><a href=# data-t="${x.id}">toggle</a> \\u00b7 <a href=# data-r="${x.id}">delete</a></span></div>`).join('');
V.querySelectorAll('a[data-t]').forEach(a=>a.onclick=async e=>{e.preventDefault();const cur=j.find(x=>x.id===a.dataset.t);await post('cron',{action:'toggle',id:a.dataset.t,enabled:!cur.enabled});cron()});
V.querySelectorAll('a[data-r]').forEach(a=>a.onclick=async e=>{e.preventDefault();await post('cron',{action:'remove',id:a.dataset.r});cron()});
document.getElementById('cadd').onclick=async()=>{const s=document.getElementById('cs').value.trim(),p=document.getElementById('cp').value.trim();if(!s||!p)return;await post('cron',{action:'add',schedule:s,prompt:p});cron()}}
async function cfg(){const c=await api('config');const keys=Object.keys(c).sort();
V.innerHTML='<h2>Config</h2><div class=bar><input id=ck placeholder="key (e.g. model.default)" style="flex:0 0 260px"><input id=cv placeholder="value"><button class=send id=cset>Set</button></div>'+keys.map(k=>`<div class=row><span class=mut>${esc(k)}</span><span>${esc(''+c[k])}</span></div>`).join('');
document.getElementById('cset').onclick=async()=>{const k=document.getElementById('ck').value.trim(),v=document.getElementById('cv').value;if(!k)return;await post('config',{key:k,value:v});cfg()}}
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
                from .usage_log import cost_report
                self._json(cost_report(int((q.get("days", ["30"])[0]) or 30)))
            elif path == "/api/keys":
                self._json(_env_keys())
            elif path == "/api/pairing":
                from .gateway.pairing import PairingStore
                self._json(PairingStore().list())
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
