"""Webhook listener: turn inbound HTTP POSTs into agent runs.

A stdlib :class:`~http.server.ThreadingHTTPServer` accepts ``POST /hook/<name>``.
The JSON (or raw) request body is rendered into a per-subscription prompt
template and executed through the shared ``SurfaceRunner`` path; the agent's
reply is returned in the HTTP response.

Subscriptions live in ``cfg.sub("webhooks.json")`` as a list of
``{name, prompt, secret}``. When a subscription has a ``secret`` the request
must carry a valid ``X-Hub-Signature-256`` header (GitHub-style
``sha256=<hexdigest>`` HMAC over the raw body), otherwise it is rejected 401.

The ``prompt`` template may reference the payload via ``str.format`` style
fields: ``{body}`` (raw text), ``{name}`` (hook name), and any top-level key of
a JSON object payload (e.g. ``{action}``). Unknown fields are left intact.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import config as cfg
from .util import atomic_write, read_text, truncate


def _webhooks_path():
    return cfg.sub("webhooks.json")


@dataclass
class Webhook:
    name: str
    prompt: str
    secret: str = ""
    deliver: str = ""                                  # comma-sep "platform:chat_id" delivery targets
    events: list[str] = field(default_factory=list)    # X-GitHub-Event allowlist (empty = all)
    skills: list[str] = field(default_factory=list)    # skills to load before running


class WebhookStore:
    """JSON-backed CRUD for webhook subscriptions (keyed by unique ``name``)."""

    def _load(self) -> list[dict]:
        raw = read_text(_webhooks_path())
        return json.loads(raw) if raw.strip() else []

    def _save(self, hooks: list[dict]) -> None:
        atomic_write(_webhooks_path(), json.dumps(hooks, indent=2))

    def list(self) -> list[Webhook]:
        return [Webhook(**h) for h in self._load()]

    def get(self, name: str) -> Webhook | None:
        for h in self._load():
            if h["name"] == name:
                return Webhook(**h)
        return None

    def add(self, name: str, prompt: str, secret: str = "", deliver: str = "",
            events: list[str] | None = None, skills: list[str] | None = None) -> Webhook:
        """Add or replace the subscription named ``name``."""
        hook = Webhook(name=name, prompt=prompt, secret=secret, deliver=deliver,
                       events=events or [], skills=skills or [])
        hooks = [h for h in self._load() if h["name"] != name]
        hooks.append(hook.__dict__)
        self._save(hooks)
        return hook

    def remove(self, name: str) -> bool:
        hooks = self._load()
        kept = [h for h in hooks if h["name"] != name]
        self._save(kept)
        return len(kept) != len(hooks)


# --------------------------------------------------------------------------- #
# request helpers
# --------------------------------------------------------------------------- #
def verify_signature(secret: str, body: bytes, header: str) -> bool:
    """Constant-time check of a GitHub-style ``sha256=<hex>`` HMAC header."""
    if not secret:
        return True
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header[len("sha256="):])


def render_prompt(template: str, name: str, body: bytes) -> str:
    """Render the prompt template against the payload.

    ``{body}`` and ``{name}`` are always available; top-level keys of a JSON
    object payload are added too. Missing/unknown ``{field}`` references are
    left verbatim rather than raising.
    """
    text = body.decode("utf-8", "replace")
    fields: dict[str, object] = {"name": name, "body": text}
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            for k, v in parsed.items():
                fields.setdefault(str(k), v)
    except (ValueError, TypeError):
        pass
    try:
        return template.format_map(_SafeDict(fields))
    except (ValueError, IndexError):
        # Malformed format spec / positional refs — fall back to raw template.
        return template


class _SafeDict(dict):
    def __missing__(self, key):  # leave {unknown} untouched
        return "{" + key + "}"


# --------------------------------------------------------------------------- #
# server
# --------------------------------------------------------------------------- #
def make_handler(config, store: WebhookStore):
    from .surface import SurfaceRunner

    runner = SurfaceRunner(config, include_mcp=True)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet
            pass

        def _json(self, code: int, obj: dict) -> None:
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(obj).encode())

        def do_GET(self):  # noqa: N802
            if self.path.rstrip("/") in ("/health", ""):
                return self._json(200, {"ok": True, "hooks": [h.name for h in store.list()]})
            return self._json(404, {"error": "not found"})

        def do_POST(self):  # noqa: N802
            path = self.path.split("?", 1)[0].rstrip("/")
            if not path.startswith("/hook/"):
                return self._json(404, {"error": "not found"})
            name = path[len("/hook/"):]
            hook = store.get(name)
            if hook is None:
                return self._json(404, {"error": f"unknown hook: {name}"})

            n = int(self.headers.get("content-length", 0) or 0)
            body = self.rfile.read(n) if n else b""
            sig = self.headers.get("X-Hub-Signature-256", "")
            if not verify_signature(hook.secret, body, sig):
                return self._json(401, {"error": "invalid signature"})

            # GitHub event allowlist: skip (200) when this event isn't in the hook's filter.
            if hook.events:
                event = self.headers.get("X-GitHub-Event", "")
                if event not in hook.events:
                    return self._json(200, {"ok": True, "skipped": "event"})

            from .automation import build_prompt, delivery_targets, enqueue_delivery, is_silent
            prompt = build_prompt(render_prompt(hook.prompt, name, body), skills=hook.skills)
            targets = delivery_targets(hook.deliver)
            first_target = targets[0] if targets else ""
            platform, _, chat_id = first_target.partition(":")
            try:
                result = runner.run_prompt(
                    prompt,
                    session_id=f"webhook:{name}",
                    title=f"webhook {name}",
                    surface="webhook",
                    meta={"webhook": name},
                    platform=platform if platform and chat_id else None,
                    chat_id=chat_id if platform and chat_id else None,
                )
                reply = result.text
            except Exception as e:  # noqa: BLE001
                return self._json(500, {"error": str(e)})

            # Deliver to configured channels via the durable outbox, honoring [SILENT].
            if hook.deliver and not is_silent(reply):
                for target in targets:
                    enqueue_delivery(target, reply)
            return self._json(200, {"ok": True, "reply": reply})

    return Handler


def serve_webhooks(config, host: str = "127.0.0.1", port: int = 8791) -> None:
    """Blocking server loop. POST ``/hook/<name>`` to trigger an agent run."""
    store = WebhookStore()
    httpd = ThreadingHTTPServer((host, port), make_handler(config, store))
    hooks = ", ".join(h.name for h in store.list()) or "none configured"
    print(f"AEGIS webhook listener on http://{host}:{port}/hook/<name>  (hooks: {hooks})")
    print("Ctrl+C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nwebhook listener stopped.")
    finally:
        httpd.server_close()


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def cmd_webhook(args, config) -> int:
    """CLI entry: ``add`` / ``list`` / ``remove`` / ``serve``."""
    store = WebhookStore()
    action = getattr(args, "action", None) or "list"

    if action == "add":
        name = getattr(args, "name", None)
        prompt = getattr(args, "prompt", None)
        if isinstance(prompt, list):
            prompt = " ".join(prompt)
        if not name or not prompt:
            print('usage: aegis webhook add <name> "<prompt>" [--secret S] '
                  '[--deliver telegram:ID] [--events pull_request,push] [--skills github-review]')
            return 2
        events = [e.strip() for e in (getattr(args, "events", "") or "").split(",") if e.strip()]
        skills = [s.strip() for s in (getattr(args, "skills", "") or "").split(",") if s.strip()]
        hook = store.add(name, prompt, getattr(args, "secret", "") or "",
                         deliver=getattr(args, "deliver", "") or "", events=events, skills=skills)
        bits = ["(signed)"] if hook.secret else []
        if hook.deliver:
            bits.append(f"→{hook.deliver}")
        if hook.events:
            bits.append(f"events={','.join(hook.events)}")
        if hook.skills:
            bits.append(f"skills={','.join(hook.skills)}")
        print(f"added webhook '{hook.name}' {' '.join(bits)}: {truncate(hook.prompt, 60)}")
        return 0

    if action == "remove":
        name = getattr(args, "name", None)
        if not name:
            print("usage: aegis webhook remove <name>")
            return 2
        print("removed" if store.remove(name) else "not found")
        return 0

    if action == "serve":
        serve_webhooks(
            config,
            host=getattr(args, "host", None) or config.get("server.host", "127.0.0.1"),
            port=int(getattr(args, "port", None) or 8791),
        )
        return 0

    # list (default)
    hooks = store.list()
    if not hooks:
        print("(no webhooks)")
        return 0
    for h in hooks:
        lock = "🔒" if h.secret else "  "
        print(f"  {lock} {h.name:<16} {truncate(h.prompt, 60)}")
    return 0
