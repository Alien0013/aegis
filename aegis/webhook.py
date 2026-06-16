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
import threading
import time
import base64
import binascii
from collections import deque
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import config as cfg
from .util import atomic_write, read_text, truncate

MAX_WEBHOOK_BYTES = 10_000_000
WEBHOOK_REPLAY_WINDOW_SECONDS = 300
_DELIVERY_ID_HEADERS = ("X-GitHub-Delivery", "svix-id", "X-Request-ID", "X-Request-Id", "Idempotency-Key")


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
        if not raw.strip():
            return []
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if not isinstance(data, list):
            return []
        hooks: list[dict] = []
        for item in data:
            hook = _normalize_hook_record(item)
            if hook is not None:
                hooks.append(hook)
        return hooks

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


def _list_of_strings(value) -> list[str]:
    if isinstance(value, str):
        return [p.strip() for p in value.split(",") if p.strip()]
    if isinstance(value, list):
        return [str(p).strip() for p in value if str(p).strip()]
    return []


def _normalize_hook_record(item) -> dict | None:
    if not isinstance(item, dict):
        return None
    name = str(item.get("name") or "").strip()
    prompt = str(item.get("prompt") or "").strip()
    if not name or not prompt:
        return None
    return {
        "name": name,
        "prompt": prompt,
        "secret": str(item.get("secret") or ""),
        "deliver": str(item.get("deliver") or ""),
        "events": _list_of_strings(item.get("events")),
        "skills": _list_of_strings(item.get("skills")),
    }


# --------------------------------------------------------------------------- #
# request helpers
# --------------------------------------------------------------------------- #
def _headers_get(headers, name: str) -> str:
    if headers is None:
        return ""
    get = getattr(headers, "get", None)
    if callable(get):
        return str(get(name, "") or get(name.lower(), "") or get(name.upper(), "") or "")
    if isinstance(headers, dict):
        lower = {str(k).lower(): v for k, v in headers.items()}
        return str(lower.get(name.lower(), "") or "")
    return ""


def _verify_sha256_hmac(secret: str, body: bytes, header: str) -> bool:
    raw = str(header or "").strip()
    if raw.startswith("sha256="):
        raw = raw[len("sha256="):]
    if not raw:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, raw)


def _verify_svix_signature(
    secret: str,
    body: bytes,
    *,
    msg_id: str,
    timestamp: str,
    signature_header: str,
    now: int | None = None,
) -> bool:
    if not (secret and msg_id and timestamp and signature_header):
        return False
    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        return False
    current = int(time.time()) if now is None else int(now)
    if abs(current - ts) > WEBHOOK_REPLAY_WINDOW_SECONDS:
        return False
    if secret.startswith("whsec_"):
        try:
            key = base64.b64decode(secret.removeprefix("whsec_"), validate=True)
        except (binascii.Error, ValueError):
            return False
    else:
        key = secret.encode()
    signed = msg_id.encode() + b"." + timestamp.encode() + b"." + body
    expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    for part in signature_header.split():
        try:
            version, signature = part.split(",", 1)
        except ValueError:
            continue
        if version == "v1" and hmac.compare_digest(signature, expected):
            return True
    return False


def verify_signature(secret: str, body: bytes, header) -> bool:
    """Constant-time webhook signature verification.

    ``header`` may be a legacy GitHub signature string or a headers mapping.
    """
    if not secret:
        return True
    if isinstance(header, str):
        return _verify_sha256_hmac(secret, body, header)

    svix_id = _headers_get(header, "svix-id")
    svix_timestamp = _headers_get(header, "svix-timestamp")
    svix_signature = _headers_get(header, "svix-signature")
    if svix_id or svix_timestamp or svix_signature:
        return _verify_svix_signature(
            secret,
            body,
            msg_id=svix_id,
            timestamp=svix_timestamp,
            signature_header=svix_signature,
        )

    gitlab_token = _headers_get(header, "X-Gitlab-Token")
    if gitlab_token:
        return hmac.compare_digest(gitlab_token, secret)

    github_signature = _headers_get(header, "X-Hub-Signature-256")
    if github_signature:
        return _verify_sha256_hmac(secret, body, github_signature)

    generic_signature = _headers_get(header, "X-Webhook-Signature")
    if generic_signature:
        return _verify_sha256_hmac(secret, body, generic_signature)

    return False


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


def _request_length(headers) -> tuple[int, str]:
    raw = headers.get("content-length", "0") or "0"
    try:
        size = int(raw)
    except (TypeError, ValueError):
        return 0, "invalid"
    if size < 0:
        return 0, "invalid"
    if size > MAX_WEBHOOK_BYTES:
        return size, "too_large"
    return size, ""


class DeliveryIdCache:
    """Bounded retry-dedupe cache for webhook delivery ids."""

    def __init__(self, *, ttl_seconds: float = 3600, max_items: int = 10000) -> None:
        self.ttl_seconds = max(1.0, float(ttl_seconds or 3600))
        self.max_items = max(1, int(max_items or 10000))
        self._seen: dict[str, float] = {}
        self._order: deque[tuple[float, str]] = deque()
        self._lock = threading.RLock()

    def _prune_locked(self, now: float) -> None:
        cutoff = now - self.ttl_seconds
        while self._order and (self._order[0][0] < cutoff or len(self._seen) > self.max_items):
            seen_at, key = self._order.popleft()
            if self._seen.get(key) == seen_at:
                self._seen.pop(key, None)

    def record(self, key: str, *, now: float | None = None) -> bool:
        """Return True when this delivery id has not been processed recently."""
        key = str(key or "").strip()
        if not key:
            return True
        timestamp = time.time() if now is None else float(now)
        with self._lock:
            seen_at = self._seen.get(key)
            if seen_at is not None and timestamp - seen_at < self.ttl_seconds:
                return False
            if seen_at is not None:
                self._seen.pop(key, None)
            self._seen[key] = timestamp
            self._order.append((timestamp, key))
            self._prune_locked(timestamp)
            return True


def _delivery_cache(config) -> DeliveryIdCache:
    ttl = config.get("webhook.idempotency_ttl_seconds", None)
    if ttl is None:
        ttl = config.get("server.idempotency_ttl_seconds", 3600)
    return DeliveryIdCache(
        ttl_seconds=float(ttl or 3600),
        max_items=int(config.get("webhook.idempotency_cache_max", 10000) or 10000),
    )


def _delivery_id(name: str, headers) -> str:
    for header in _DELIVERY_ID_HEADERS:
        value = str(headers.get(header, "") or "").strip()
        if value:
            return f"{name}:{header.lower()}:{value}"
    return ""


# --------------------------------------------------------------------------- #
# server
# --------------------------------------------------------------------------- #
def make_handler(config, store: WebhookStore):
    from .surface import SurfaceRunner

    runner = SurfaceRunner(config, include_mcp=True)
    delivery_cache = _delivery_cache(config)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet
            pass

        def _json(self, code: int, obj: dict) -> None:
            payload = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            self.wfile.flush()

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

            n, length_error = _request_length(self.headers)
            if length_error == "invalid":
                return self._json(400, {"error": "invalid content-length"})
            if length_error == "too_large":
                return self._json(413, {"error": "payload too large", "limit": MAX_WEBHOOK_BYTES})
            body = self.rfile.read(n) if n else b""
            if not verify_signature(hook.secret, body, self.headers):
                return self._json(401, {"error": "invalid signature"})

            # GitHub event allowlist: skip (200) when this event isn't in the hook's filter.
            if hook.events:
                event = self.headers.get("X-GitHub-Event", "")
                if event not in hook.events:
                    return self._json(200, {"ok": True, "skipped": "event"})
            delivery_id = _delivery_id(name, self.headers)
            if delivery_id and not delivery_cache.record(delivery_id):
                return self._json(200, {"ok": True, "duplicate": True})

            from .automation import build_prompt, delivery_targets, enqueue_delivery, is_silent
            prompt = build_prompt(
                render_prompt(hook.prompt, name, body),
                skills=hook.skills,
                config=config,
            )
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
