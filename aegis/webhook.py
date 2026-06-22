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
import os
import threading
import time
import base64
import binascii
import ipaddress
import uuid
from collections import deque
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import config as cfg
from .util import atomic_write, read_text, truncate

MAX_WEBHOOK_BYTES = 10_000_000
WEBHOOK_REPLAY_WINDOW_SECONDS = 300
_DELIVERY_ID_HEADERS = ("X-GitHub-Delivery", "svix-id", "X-Request-ID", "X-Request-Id", "Idempotency-Key")
_DELIVERY_ID_BODY_KEYS = ("delivery_id", "event_id", "message_id", "id")
_DELIVERY_ID_BODY_PATHS = (
    ("key", "id"),
    ("message", "key", "id"),
    ("data", "key", "id"),
)


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
        path = _webhooks_path()
        atomic_write(path, json.dumps(hooks, indent=2))
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

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


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _is_loopback_host(host: str) -> bool:
    try:
        return ipaddress.ip_address(str(host or "")).is_loopback
    except ValueError:
        return str(host or "").lower() in {"localhost"}


def _unsigned_webhook_allowed(config, client_host: str) -> bool:
    if _env_truthy("AEGIS_WEBHOOK_INSECURE_NO_AUTH") or _env_truthy("WEBHOOK_INSECURE_NO_AUTH"):
        return True
    allow_loopback = bool(config.get("webhook.allow_unsigned_loopback", True))
    return allow_loopback and _is_loopback_host(client_host)


def _verify_sha256_hmac(secret: str, body: bytes, header: str) -> bool:
    raw = str(header or "").strip()
    if raw.startswith("sha256="):
        raw = raw[len("sha256="):]
    if not raw:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, raw)


def _verify_timestamped_sha256_hmac(
    secret: str,
    body: bytes,
    header: str,
    *,
    timestamp: str,
    delivery_id: str,
    now: int | None = None,
) -> bool:
    raw = str(header or "").strip()
    if raw.startswith("sha256="):
        raw = raw[len("sha256="):]
    if not (raw and timestamp and delivery_id):
        return False
    try:
        ts = int(str(timestamp).strip())
    except (TypeError, ValueError):
        return False
    current = int(time.time()) if now is None else int(now)
    if abs(current - ts) > WEBHOOK_REPLAY_WINDOW_SECONDS:
        return False
    signed = f"{ts}.{delivery_id}.".encode("utf-8") + body
    expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, raw)


def _generic_signature_delivery_id(headers, body: bytes) -> str:
    for header in _DELIVERY_ID_HEADERS:
        value = _headers_get(headers, header).strip()
        if value:
            return value
    _source, value = _body_delivery_id(body or b"")
    return value


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
        timestamp = (
            _headers_get(header, "X-Webhook-Timestamp")
            or _headers_get(header, "X-AEGIS-Timestamp")
            or _headers_get(header, "X-Timestamp")
        )
        return _verify_timestamped_sha256_hmac(
            secret,
            body,
            generic_signature,
            timestamp=timestamp,
            delivery_id=_generic_signature_delivery_id(header, body),
        )

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
        self._accepted_count = 0
        self._duplicate_count = 0
        self._discarded_count = 0
        self._pruned_expired = 0
        self._pruned_capacity = 0

    def _prune_locked(self, now: float) -> None:
        cutoff = now - self.ttl_seconds
        while self._order and (self._order[0][0] < cutoff or len(self._seen) > self.max_items):
            expired = self._order[0][0] < cutoff
            seen_at, key = self._order.popleft()
            if self._seen.get(key) == seen_at:
                self._seen.pop(key, None)
                if expired:
                    self._pruned_expired += 1
                else:
                    self._pruned_capacity += 1

    def record(self, key: str, *, now: float | None = None) -> bool:
        """Return True when this delivery id has not been processed recently."""
        key = str(key or "").strip()
        if not key:
            return True
        timestamp = time.time() if now is None else float(now)
        with self._lock:
            self._prune_locked(timestamp)
            seen_at = self._seen.get(key)
            if seen_at is not None and timestamp - seen_at < self.ttl_seconds:
                self._duplicate_count += 1
                return False
            if seen_at is not None:
                self._seen.pop(key, None)
            self._seen[key] = timestamp
            self._order.append((timestamp, key))
            self._prune_locked(timestamp)
            self._accepted_count += 1
            return True

    def discard(self, key: str) -> bool:
        """Remove a previously accepted delivery id so providers can retry failures."""
        key = str(key or "").strip()
        if not key:
            return False
        with self._lock:
            if key not in self._seen:
                return False
            self._seen.pop(key, None)
            self._discarded_count += 1
            return True

    def stats(self, *, now: float | None = None) -> dict[str, float | int]:
        timestamp = time.time() if now is None else float(now)
        with self._lock:
            self._prune_locked(timestamp)
            oldest = timestamp - self._order[0][0] if self._order else 0.0
            return {
                "entries": len(self._seen),
                "max_items": self.max_items,
                "ttl_seconds": self.ttl_seconds,
                "oldest_age_seconds": max(0.0, oldest),
                "accepted_count": self._accepted_count,
                "duplicate_count": self._duplicate_count,
                "discarded_count": self._discarded_count,
                "pruned_expired": self._pruned_expired,
                "pruned_capacity": self._pruned_capacity,
            }


class FixedWindowRateLimiter:
    def __init__(self, *, limit: int, window_seconds: float = 60.0) -> None:
        self.limit = max(0, int(limit or 0))
        self.window_seconds = max(1.0, float(window_seconds or 60))
        self._hits: dict[str, tuple[float, int]] = {}
        self._lock = threading.RLock()
        self._allowed_count = 0
        self._limited_count = 0
        self._pruned_windows = 0

    def _prune_locked(self, now: float) -> None:
        cutoff = now - self.window_seconds
        for key, (window_start, _count) in list(self._hits.items()):
            if window_start < cutoff:
                self._hits.pop(key, None)
                self._pruned_windows += 1

    def allow(self, key: str, *, now: float | None = None) -> bool:
        if self.limit <= 0:
            with self._lock:
                self._allowed_count += 1
            return True
        timestamp = time.time() if now is None else float(now)
        window_start = timestamp - (timestamp % self.window_seconds)
        with self._lock:
            self._prune_locked(timestamp)
            old_start, count = self._hits.get(key, (window_start, 0))
            if old_start != window_start:
                old_start, count = window_start, 0
            if count >= self.limit:
                self._hits[key] = (old_start, count)
                self._limited_count += 1
                return False
            self._hits[key] = (old_start, count + 1)
            self._allowed_count += 1
            return True

    def stats(self, *, now: float | None = None) -> dict[str, float | int]:
        timestamp = time.time() if now is None else float(now)
        with self._lock:
            self._prune_locked(timestamp)
            return {
                "entries": len(self._hits),
                "active_hits": sum(count for _start, count in self._hits.values()),
                "limit": self.limit,
                "window_seconds": self.window_seconds,
                "allowed_count": self._allowed_count,
                "limited_count": self._limited_count,
                "pruned_windows": self._pruned_windows,
            }


def _delivery_cache(config) -> DeliveryIdCache:
    ttl = config.get("webhook.idempotency_ttl_seconds", None)
    if ttl is None:
        ttl = config.get("server.idempotency_ttl_seconds", 3600)
    return DeliveryIdCache(
        ttl_seconds=float(ttl or 3600),
        max_items=int(config.get("webhook.idempotency_cache_max", 10000) or 10000),
    )


def _rate_limiter(config) -> FixedWindowRateLimiter:
    return FixedWindowRateLimiter(
        limit=int(config.get("webhook.rate_limit_per_minute", 60) or 0),
        window_seconds=60,
    )


_RUNTIME_LOCK = threading.RLock()
_RUNTIME: dict[str, object] = {}


def _configured_runtime_status(config) -> dict[str, dict[str, float | int]]:
    ttl = config.get("webhook.idempotency_ttl_seconds", None)
    if ttl is None:
        ttl = config.get("server.idempotency_ttl_seconds", 3600)
    return {
        "delivery_cache": {
            "entries": 0,
            "max_items": int(config.get("webhook.idempotency_cache_max", 10000) or 10000),
            "ttl_seconds": float(ttl or 3600),
            "oldest_age_seconds": 0.0,
            "accepted_count": 0,
            "duplicate_count": 0,
            "discarded_count": 0,
            "pruned_expired": 0,
            "pruned_capacity": 0,
        },
        "rate_limiter": {
            "entries": 0,
            "active_hits": 0,
            "limit": int(config.get("webhook.rate_limit_per_minute", 60) or 0),
            "window_seconds": 60.0,
            "allowed_count": 0,
            "limited_count": 0,
            "pruned_windows": 0,
        },
    }


def _register_runtime(
    delivery_cache: DeliveryIdCache,
    rate_limiter: FixedWindowRateLimiter,
    *,
    hook_count: int,
) -> None:
    with _RUNTIME_LOCK:
        _RUNTIME.clear()
        _RUNTIME.update({
            "started_at": time.time(),
            "hook_count": int(hook_count),
            "delivery_cache": delivery_cache,
            "rate_limiter": rate_limiter,
        })


def webhook_runtime_status(config) -> dict[str, object]:
    configured = _configured_runtime_status(config)
    with _RUNTIME_LOCK:
        delivery_cache = _RUNTIME.get("delivery_cache")
        rate_limiter = _RUNTIME.get("rate_limiter")
        started_at = float(_RUNTIME.get("started_at") or 0.0)
        hook_count = int(_RUNTIME.get("hook_count") or 0)
    active = isinstance(delivery_cache, DeliveryIdCache) and isinstance(rate_limiter, FixedWindowRateLimiter)
    if not active:
        return {
            "active": False,
            "started_at": None,
            "uptime_seconds": 0.0,
            "hook_count": 0,
            **configured,
        }
    return {
        "active": True,
        "started_at": started_at,
        "uptime_seconds": max(0.0, time.time() - started_at),
        "hook_count": hook_count,
        "delivery_cache": delivery_cache.stats(),
        "rate_limiter": rate_limiter.stats(),
    }


def _body_path_value(source: dict, path: tuple[str, ...]) -> str:
    cur = source
    for key in path:
        if not isinstance(cur, dict):
            return ""
        cur = cur.get(key)
    if isinstance(cur, (str, int, float, bool)):
        return str(cur).strip()
    return ""


def _body_delivery_id(body: bytes) -> tuple[str, str]:
    if not body:
        return "", ""
    try:
        payload = json.loads(body)
    except Exception:  # noqa: BLE001
        return "", ""
    if not isinstance(payload, dict):
        return "", ""
    for key in _DELIVERY_ID_BODY_KEYS:
        value = payload.get(key)
        if isinstance(value, (str, int, float, bool)):
            text = str(value).strip()
            if text:
                return f"body:{key}", text
    for path in _DELIVERY_ID_BODY_PATHS:
        text = _body_path_value(payload, path)
        if text:
            return f"body:{'.'.join(path)}", text
    return "", ""


def _delivery_id(name: str, headers, body: bytes | None = None) -> str:
    for header in _DELIVERY_ID_HEADERS:
        value = _headers_get(headers, header).strip()
        if value:
            return f"{name}:{header.lower()}:{value}"
    source, value = _body_delivery_id(body or b"")
    if source and value:
        return f"{name}:{source}:{value}"
    return ""


def _webhook_session_id(name: str, delivery_id: str) -> str:
    name_part = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in str(name or "hook"))
    if delivery_id:
        digest = hashlib.sha256(str(delivery_id).encode("utf-8")).hexdigest()[:24]
        return f"webhook:{name_part}:{digest}"
    return f"webhook:{name_part}:{uuid.uuid4().hex[:24]}"


# --------------------------------------------------------------------------- #
# server
# --------------------------------------------------------------------------- #
def make_handler(config, store: WebhookStore):
    from .surface import SurfaceRunner

    runner = SurfaceRunner(config, include_mcp=True)
    delivery_cache = _delivery_cache(config)
    rate_limiter = _rate_limiter(config)
    _register_runtime(delivery_cache, rate_limiter, hook_count=len(store.list()))
    handler_delivery_cache = delivery_cache
    handler_rate_limiter = rate_limiter

    class Handler(BaseHTTPRequestHandler):
        delivery_cache = handler_delivery_cache
        rate_limiter = handler_rate_limiter

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
            client_host = str((self.client_address or ("",))[0] or "")

            n, length_error = _request_length(self.headers)
            if length_error == "invalid":
                return self._json(400, {"error": "invalid content-length"})
            if length_error == "too_large":
                return self._json(413, {"error": "payload too large", "limit": MAX_WEBHOOK_BYTES})
            body = self.rfile.read(n) if n else b""
            if not hook.secret and not _unsigned_webhook_allowed(config, client_host):
                return self._json(401, {"error": "webhook secret required"})
            if not verify_signature(hook.secret, body, self.headers):
                return self._json(401, {"error": "invalid signature"})
            if not rate_limiter.allow(f"{name}:{client_host}"):
                return self._json(429, {"error": "rate limit exceeded"})

            # GitHub event allowlist: skip (200) when this event isn't in the hook's filter.
            if hook.events:
                event = self.headers.get("X-GitHub-Event", "")
                if event not in hook.events:
                    return self._json(200, {"ok": True, "skipped": "event"})
            delivery_id = _delivery_id(name, self.headers, body)
            delivery_recorded = False
            if delivery_id:
                delivery_recorded = delivery_cache.record(delivery_id)
                if not delivery_recorded:
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
            from .platforms import normalize_platform_name
            platform = normalize_platform_name(platform, default=str(platform or "").strip().lower())
            session_id = _webhook_session_id(name, delivery_id)
            try:
                result = runner.run_prompt(
                    prompt,
                    session_id=session_id,
                    title=f"webhook {name}",
                    surface="webhook",
                    meta={"webhook": name, "delivery_id": delivery_id},
                    platform=platform if platform and chat_id else None,
                    chat_id=chat_id if platform and chat_id else None,
                )
                reply = result.text
            except Exception as e:  # noqa: BLE001
                if delivery_recorded:
                    delivery_cache.discard(delivery_id)
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
