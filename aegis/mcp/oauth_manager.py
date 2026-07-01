"""Process-local MCP OAuth coordination.

AEGIS keeps MCP OAuth state behind a single manager so long-running MCP clients
can notice token changes written by another process and so concurrent 401s do
not stampede the token endpoint. AEGIS' provider OAuth implementation already
handles PKCE login, disk-backed tokens, and refresh; this module adds the
MCP-specific coordination layer around that existing auth provider.
"""

from __future__ import annotations

import json
import re
import socket
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx

from .. import config as cfg
from ..providers.auth import AuthError, AuthStore, OAuthAuth, OAuthConfig
from ..util import atomic_write, read_text


@dataclass
class _Pending401:
    event: threading.Event = field(default_factory=threading.Event)
    result: bool = False


@dataclass
class _OAuthEntry:
    server_url: str
    fingerprint: str
    store_path: str
    auth: OAuthAuth
    dynamic_client: bool = False
    last_mtime_ns: int = 0
    lock: threading.RLock = field(default_factory=threading.RLock)
    pending_401: dict[str, _Pending401] = field(default_factory=dict)


class ManagedMCPOAuth:
    """Small AuthProvider-shaped wrapper owned by :class:`MCPOAuthManager`."""

    def __init__(self, manager: "MCPOAuthManager", server_name: str, auth: OAuthAuth):
        self.manager = manager
        self.server_name = server_name
        self.auth = auth
        self.oauth = auth.oauth

    def headers(self) -> dict[str, str]:
        try:
            headers = self.auth.headers()
        except AuthError as exc:
            self.manager.handle_refresh_error(self.server_name, exc)
            raise
        self.manager.record_store_mtime(self.server_name)
        return headers

    def available(self) -> bool:
        return self.auth.available()

    def describe(self) -> str:
        return self.auth.describe()

    def report(self, kind: str, error_context=None) -> bool:
        if str(kind or "").lower() == "auth":
            return self.handle_401(_failed_access_token(error_context), error_context)
        return self.auth.report(kind, error_context)

    def handle_401(self, failed_access_token: str | None, error_context=None) -> bool:
        return self.manager.handle_401(self.server_name, failed_access_token, error_context)


class MCPOAuthManager:
    """Single source of truth for AEGIS MCP OAuth auth handles."""

    def __init__(self) -> None:
        self._entries: dict[str, _OAuthEntry] = {}
        self._entries_lock = threading.RLock()

    def get_or_build_auth(
        self,
        server_name: str,
        server_url: str,
        spec: dict,
    ) -> ManagedMCPOAuth | None:
        parsed = _oauth_config_from_spec(server_name, spec)
        if parsed is None:
            return None
        oauth, fingerprint, dynamic_client = parsed
        store = AuthStore()
        store_path = str(store.path)
        with self._entries_lock:
            entry = self._entries.get(server_name)
            if (
                entry is None
                or entry.server_url != server_url
                or entry.fingerprint != fingerprint
                or entry.store_path != store_path
            ):
                auth = OAuthAuth(oauth, store)
                entry = _OAuthEntry(
                    server_url=server_url,
                    fingerprint=fingerprint,
                    store_path=store_path,
                    auth=auth,
                    dynamic_client=dynamic_client,
                    last_mtime_ns=_auth_store_mtime(auth),
                )
                self._entries[server_name] = entry
            return ManagedMCPOAuth(self, server_name, entry.auth)

    def login(
        self,
        server_name: str,
        server_url: str,
        spec: dict,
        *,
        manual: bool = False,
    ) -> dict:
        """Run an interactive MCP OAuth login and persist tokens."""
        self.purge_login_state(server_name, spec)
        parsed = _oauth_config_from_spec(
            server_name,
            _login_prepared_spec(spec, manual=manual),
        )
        if parsed is None:
            raise AuthError(f"MCP OAuth for '{server_name}' is not configured")
        oauth, fingerprint, dynamic_client = parsed
        store = AuthStore()
        auth = OAuthAuth(oauth, store)
        creds = auth.login(manual=manual)
        with self._entries_lock:
            self._entries[server_name] = _OAuthEntry(
                server_url=server_url,
                fingerprint=fingerprint,
                store_path=str(store.path),
                auth=auth,
                dynamic_client=dynamic_client,
                last_mtime_ns=_auth_store_mtime(auth),
            )
        return creds

    def purge_login_state(self, server_name: str, spec: dict | None = None) -> None:
        """Remove MCP-owned OAuth state before a forced login."""
        with self._entries_lock:
            self._entries.pop(server_name, None)
        AuthStore().delete(_mcp_oauth_provider_name(server_name, spec))
        for path in (
            _client_info_path(server_name),
            _metadata_cache_path(server_name),
            _client_info_path(server_name).with_name(
                _client_info_path(server_name).name + ".bak"
            ),
        ):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    def record_store_mtime(self, server_name: str) -> None:
        entry = self._entry(server_name)
        if entry is None:
            return
        with entry.lock:
            entry.last_mtime_ns = _auth_store_mtime(entry.auth)

    def invalidate_if_disk_changed(self, server_name: str) -> bool:
        """Return True when auth.json changed since this MCP auth last used it."""
        entry = self._entry(server_name)
        if entry is None:
            return False
        with entry.lock:
            mtime_ns = _auth_store_mtime(entry.auth)
            if not mtime_ns:
                return False
            if not entry.last_mtime_ns:
                entry.last_mtime_ns = mtime_ns
                return False
            if mtime_ns != entry.last_mtime_ns:
                entry.last_mtime_ns = mtime_ns
                return True
            return False

    def handle_401(
        self,
        server_name: str,
        failed_access_token: str | None = None,
        error_context=None,
    ) -> bool:
        """Recover from an MCP HTTP 401/403 with disk reload or one refresh.

        Calls racing with the same failed access token share one refresh result.
        The caller should rebuild request headers and retry once when True is
        returned.
        """
        entry = self._entry(server_name)
        if entry is None:
            return False
        key = failed_access_token or "<unknown>"
        owner = False
        with entry.lock:
            pending = entry.pending_401.get(key)
            if pending is None:
                pending = _Pending401()
                entry.pending_401[key] = pending
                owner = True
        if not owner:
            pending.event.wait(timeout=60)
            return bool(pending.result)

        result = False
        try:
            if self.invalidate_if_disk_changed(server_name):
                result = True
            else:
                result = self._refresh_after_401(server_name, entry)
                if result:
                    self.record_store_mtime(server_name)
        finally:
            with entry.lock:
                pending.result = result
                pending.event.set()
                entry.pending_401.pop(key, None)
        return result

    def handle_refresh_error(self, server_name: str, error: Exception) -> bool:
        entry = self._entry(server_name)
        if entry is None or not entry.dynamic_client:
            return False
        if not _is_invalid_client_error_text(str(error)):
            return False
        poisoned = _poison_client_registration_cache(server_name)
        with self._entries_lock:
            self._entries.pop(server_name, None)
        return poisoned

    def remove(self, server_name: str) -> None:
        with self._entries_lock:
            self._entries.pop(server_name, None)

    def _entry(self, server_name: str) -> _OAuthEntry | None:
        with self._entries_lock:
            return self._entries.get(server_name)

    def _refresh_after_401(self, server_name: str, entry: _OAuthEntry) -> bool:
        """Refresh an MCP OAuth token, healing dynamic client cache when dead."""
        creds = (
            entry.auth._creds()  # noqa: SLF001
            or entry.auth.store.load(entry.auth.oauth.provider)
        )
        if not isinstance(creds, dict) or not creds.get("refresh_token"):
            return False
        refresh_payload = {
            "grant_type": "refresh_token",
            "refresh_token": creds["refresh_token"],
            "client_id": entry.auth.oauth.client_id,
        }
        if entry.auth.oauth.client_secret:
            refresh_payload["client_secret"] = entry.auth.oauth.client_secret
        try:
            data = entry.auth._post_token(refresh_payload)  # noqa: SLF001
        except AuthError as exc:
            if entry.dynamic_client and _is_invalid_client_error_text(str(exc)):
                _poison_client_registration_cache(server_name)
                with self._entries_lock:
                    self._entries.pop(server_name, None)
            return False
        data.setdefault("refresh_token", creds["refresh_token"])
        entry.auth._store_tokens(data)  # noqa: SLF001
        return True


_MCP_OAUTH_MANAGER = MCPOAuthManager()


def get_mcp_oauth_manager() -> MCPOAuthManager:
    return _MCP_OAUTH_MANAGER


def reset_mcp_oauth_manager_for_tests() -> None:
    global _MCP_OAUTH_MANAGER
    _MCP_OAUTH_MANAGER = MCPOAuthManager()


def _oauth_config_from_spec(name: str, spec: dict) -> tuple[OAuthConfig, str, bool] | None:
    raw = spec.get("oauth")
    auth_mode = str(spec.get("auth") or "").lower()
    if raw is None and auth_mode != "oauth":
        return None
    oauth_cfg = raw if isinstance(raw, dict) else {}
    server_url = str(spec.get("url") or "")
    discovered = _resolve_mcp_oauth_metadata(name, server_url, oauth_cfg)
    client_info = _resolve_mcp_oauth_client_info(name, server_url, oauth_cfg, discovered)
    dynamic_client = bool(client_info.get("_dynamic"))
    provider = str(oauth_cfg.get("provider") or f"mcp:{name}")
    client_id = str(
        oauth_cfg.get("client_id")
        or oauth_cfg.get("clientId")
        or client_info.get("client_id")
        or ""
    )
    token_url = str(
        oauth_cfg.get("token_url")
        or oauth_cfg.get("tokenUrl")
        or discovered.get("token_endpoint")
        or ""
    )
    authorize_url = str(
        oauth_cfg.get("authorize_url")
        or oauth_cfg.get("authorization_url")
        or oauth_cfg.get("authorizeUrl")
        or discovered.get("authorization_endpoint")
        or server_url
        or ""
    )
    if not client_id or not token_url:
        return None
    scopes = oauth_cfg.get("scopes")
    if scopes is None:
        scope = oauth_cfg.get("scope")
        scopes = str(scope).split() if isinstance(scope, str) else []
    elif isinstance(scopes, str):
        scopes = scopes.split()
    oauth = OAuthConfig(
        provider=provider,
        client_id=client_id,
        client_secret=(
            oauth_cfg.get("client_secret")
            or oauth_cfg.get("clientSecret")
            or client_info.get("client_secret")
        ),
        authorize_url=authorize_url,
        token_url=token_url,
        scopes=[str(scope) for scope in (scopes or [])],
        required_api_scopes=[
            str(scope) for scope in (oauth_cfg.get("required_api_scopes") or [])
        ],
        token_request_json=bool(oauth_cfg.get("token_request_json", False)),
        redirect_uri=(
            oauth_cfg.get("redirect_uri")
            or oauth_cfg.get("redirectUrl")
            or None
        ),
        use_localhost_callback=_truthy(
            oauth_cfg.get("use_localhost_callback"),
            default=True,
        ),
        localhost_port=_int_value(oauth_cfg.get("localhost_port"), default=0),
        callback_host=str(oauth_cfg.get("callback_host") or "127.0.0.1"),
        callback_path=str(oauth_cfg.get("callback_path") or "/callback"),
        extra_authorize_params={
            str(k): str(v)
            for k, v in dict(oauth_cfg.get("extra_authorize_params") or {}).items()
        },
        api_extra_headers=dict(oauth_cfg.get("api_extra_headers") or {}),
        code_contains_state=bool(oauth_cfg.get("code_contains_state", False)),
    )
    fingerprint = json.dumps(
        {
            "provider": provider,
            "client_id": client_id,
            "client_secret": bool(oauth.client_secret),
            "authorize_url": authorize_url,
            "token_url": token_url,
            "scopes": oauth.scopes,
            "required_api_scopes": oauth.required_api_scopes,
            "token_request_json": oauth.token_request_json,
            "redirect_uri": oauth.redirect_uri,
            "use_localhost_callback": oauth.use_localhost_callback,
            "localhost_port": oauth.localhost_port,
            "callback_host": oauth.callback_host,
            "callback_path": oauth.callback_path,
            "extra_authorize_params": oauth.extra_authorize_params,
            "code_contains_state": oauth.code_contains_state,
            "api_extra_headers": oauth.api_extra_headers,
            "metadata": _fingerprintable_metadata(discovered),
            "client_info": _fingerprintable_client_info(client_info),
        },
        sort_keys=True,
    )
    return oauth, fingerprint, dynamic_client


def _login_prepared_spec(spec: dict, *, manual: bool) -> dict:
    """Pin MCP login redirect metadata before dynamic client registration."""
    prepared = dict(spec)
    raw = prepared.get("oauth")
    oauth_cfg = dict(raw) if isinstance(raw, dict) else {}
    prepared["oauth"] = oauth_cfg
    if not prepared.get("auth"):
        prepared["auth"] = "oauth"

    if oauth_cfg.get("redirect_uri") or oauth_cfg.get("redirectUrl"):
        return prepared
    if not _truthy(oauth_cfg.get("use_localhost_callback"), default=True):
        return prepared

    host = str(oauth_cfg.get("callback_host") or "127.0.0.1")
    path = str(oauth_cfg.get("callback_path") or "/callback")
    if not path.startswith("/"):
        path = f"/{path}"
        oauth_cfg["callback_path"] = path
    port = _int_value(oauth_cfg.get("localhost_port"), default=0)
    if port <= 0:
        port = _find_free_local_port(host)
    oauth_cfg["localhost_port"] = port
    oauth_cfg["redirect_uri"] = f"http://{host}:{port}{path}"
    if manual:
        oauth_cfg["use_localhost_callback"] = True
    return prepared


def _mcp_oauth_provider_name(server_name: str, spec: dict | None) -> str:
    raw = (spec or {}).get("oauth") if isinstance(spec, dict) else None
    oauth_cfg = raw if isinstance(raw, dict) else {}
    return str(oauth_cfg.get("provider") or f"mcp:{server_name}")


def _resolve_mcp_oauth_metadata(
    server_name: str,
    server_url: str,
    oauth_cfg: dict,
) -> dict:
    """Return OAuth AS metadata from config, cache, or MCP discovery.

    Persist OAuth server metadata so a restarted MCP client can refresh with
    the real token endpoint instead of guessing ``{server_url}/token``. AEGIS
    keeps the behavior in an SDK-free, synchronous form.
    """
    configured = {
        key: oauth_cfg.get(key)
        for key in (
            "issuer",
            "authorization_endpoint",
            "authorization_url",
            "authorize_url",
            "token_endpoint",
            "token_url",
            "registration_endpoint",
        )
        if oauth_cfg.get(key)
    }
    if configured.get("token_url") and not configured.get("token_endpoint"):
        configured["token_endpoint"] = configured["token_url"]
    if configured.get("authorize_url") and not configured.get("authorization_endpoint"):
        configured["authorization_endpoint"] = configured["authorize_url"]
    if configured.get("authorization_url") and not configured.get("authorization_endpoint"):
        configured["authorization_endpoint"] = configured["authorization_url"]
    if configured.get("token_endpoint") and configured.get("authorization_endpoint"):
        return configured

    cached = _load_json(_metadata_cache_path(server_name))
    if cached and cached.get("server_url") == server_url:
        metadata = cached.get("metadata")
        if isinstance(metadata, dict):
            merged = {**metadata, **configured}
            if merged.get("token_endpoint") or merged.get("authorization_endpoint"):
                return merged

    if not _metadata_discovery_enabled(oauth_cfg):
        return configured

    discovered = _discover_mcp_oauth_metadata(server_url, oauth_cfg)
    merged = {**discovered, **configured}
    if discovered:
        _save_json(
            _metadata_cache_path(server_name),
            {
                "server_url": server_url,
                "metadata": discovered,
                "discovered_at": time.time(),
            },
        )
    return merged


def _resolve_mcp_oauth_client_info(
    server_name: str,
    server_url: str,
    oauth_cfg: dict,
    metadata: dict,
) -> dict:
    configured_client_id = oauth_cfg.get("client_id") or oauth_cfg.get("clientId")
    if configured_client_id:
        return {
            "client_id": configured_client_id,
            "client_secret": oauth_cfg.get("client_secret") or oauth_cfg.get("clientSecret"),
            "_dynamic": False,
        }

    cached = _load_json(_client_info_path(server_name))
    if cached and cached.get("server_url") == server_url:
        client_info = cached.get("client_info")
        if isinstance(client_info, dict) and client_info.get("client_id"):
            return {**client_info, "_dynamic": True}

    registration_endpoint = str(metadata.get("registration_endpoint") or "")
    if not registration_endpoint or not _metadata_discovery_enabled(oauth_cfg):
        return {}
    client_info = _register_mcp_oauth_client(registration_endpoint, oauth_cfg)
    if client_info.get("client_id"):
        _save_json(
            _client_info_path(server_name),
            {
                "server_url": server_url,
                "client_info": client_info,
                "registered_at": time.time(),
            },
        )
    return {**client_info, "_dynamic": True} if client_info else {}


def _discover_mcp_oauth_metadata(server_url: str, oauth_cfg: dict) -> dict:
    protected_resource = _discover_protected_resource_metadata(server_url, oauth_cfg)
    auth_server = _first_authorization_server(protected_resource, oauth_cfg, server_url)
    metadata = _discover_authorization_server_metadata(auth_server, server_url, oauth_cfg)
    if not metadata and protected_resource:
        metadata = {}
    if protected_resource:
        metadata.setdefault("protected_resource", protected_resource)
    return metadata


def _discover_protected_resource_metadata(server_url: str, oauth_cfg: dict) -> dict:
    for url in _protected_resource_metadata_urls(server_url, oauth_cfg):
        data = _get_json(url)
        if data:
            return data
    return {}


def _discover_authorization_server_metadata(
    auth_server: str,
    server_url: str,
    oauth_cfg: dict,
) -> dict:
    for url in _authorization_server_metadata_urls(auth_server, server_url, oauth_cfg):
        data = _get_json(url)
        if data and (
            data.get("token_endpoint")
            or data.get("authorization_endpoint")
            or data.get("registration_endpoint")
        ):
            return data
    return {}


def _get_json(url: str) -> dict:
    if not url:
        return {}
    try:
        with httpx.Client(timeout=10.0, follow_redirects=True) as client:
            response = client.get(url, headers={"Accept": "application/json"})
    except httpx.HTTPError:
        return {}
    if response.status_code >= 400:
        return {}
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _protected_resource_metadata_urls(server_url: str, oauth_cfg: dict) -> list[str]:
    explicit = oauth_cfg.get("protected_resource_metadata_url") or oauth_cfg.get(
        "resource_metadata_url"
    )
    urls = [str(explicit)] if explicit else []
    split = urlsplit(server_url)
    if not split.scheme or not split.netloc:
        return _dedupe(urls)
    origin = urlunsplit((split.scheme, split.netloc, "", "", ""))
    path = split.path.rstrip("/")
    if path:
        urls.append(f"{origin}/.well-known/oauth-protected-resource{path}")
    urls.append(f"{origin}/.well-known/oauth-protected-resource")
    return _dedupe(urls)


def _authorization_server_metadata_urls(
    auth_server: str,
    server_url: str,
    oauth_cfg: dict,
) -> list[str]:
    explicit = oauth_cfg.get("authorization_server_metadata_url") or oauth_cfg.get(
        "oauth_metadata_url"
    )
    urls = [str(explicit)] if explicit else []
    for base in (auth_server, server_url):
        split = urlsplit(str(base or ""))
        if not split.scheme or not split.netloc:
            continue
        origin = urlunsplit((split.scheme, split.netloc, "", "", ""))
        path = split.path.rstrip("/")
        if path:
            urls.append(f"{origin}/.well-known/oauth-authorization-server{path}")
        urls.append(f"{origin}/.well-known/oauth-authorization-server")
    return _dedupe(urls)


def _first_authorization_server(
    protected_resource: dict,
    oauth_cfg: dict,
    server_url: str,
) -> str:
    configured = oauth_cfg.get("authorization_server") or oauth_cfg.get("issuer")
    if configured:
        return str(configured)
    servers = protected_resource.get("authorization_servers")
    if isinstance(servers, list):
        for value in servers:
            if value:
                return str(value)
    return server_url


def _register_mcp_oauth_client(registration_endpoint: str, oauth_cfg: dict) -> dict:
    payload = _client_registration_payload(oauth_cfg)
    try:
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            response = client.post(
                registration_endpoint,
                json=payload,
                headers={"Accept": "application/json", "Content-Type": "application/json"},
            )
    except httpx.HTTPError:
        return {}
    if response.status_code >= 400:
        return {}
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _client_registration_payload(oauth_cfg: dict) -> dict:
    redirect_uri = str(
        oauth_cfg.get("redirect_uri")
        or oauth_cfg.get("redirectUrl")
        or "http://127.0.0.1/callback"
    )
    payload: dict = {
        "client_name": str(oauth_cfg.get("client_name") or "AEGIS"),
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": (
            "client_secret_post"
            if oauth_cfg.get("client_secret") or oauth_cfg.get("clientSecret")
            else "none"
        ),
    }
    scopes = oauth_cfg.get("scopes")
    if scopes is None:
        scopes = oauth_cfg.get("scope")
    if isinstance(scopes, list):
        payload["scope"] = " ".join(str(scope) for scope in scopes)
    elif isinstance(scopes, str) and scopes.strip():
        payload["scope"] = scopes.strip()
    return payload


def _metadata_discovery_enabled(oauth_cfg: dict) -> bool:
    return bool(oauth_cfg.get("discovery", True))


def _metadata_cache_path(server_name: str) -> Path:
    return cfg.sub("mcp-oauth", f"{_safe_filename(server_name)}.metadata.json")


def _client_info_path(server_name: str) -> Path:
    return cfg.sub("mcp-oauth", f"{_safe_filename(server_name)}.client.json")


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    return cleaned[:128] or "default"


def _load_json(path: Path) -> dict:
    raw = read_text(path)
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _save_json(path: Path, data: dict) -> None:
    atomic_write(path, json.dumps(data, indent=2, sort_keys=True))
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _poison_client_registration_cache(server_name: str) -> bool:
    """Remove cached dynamic MCP OAuth client metadata after invalid_client."""
    client_path = _client_info_path(server_name)
    if not client_path.exists():
        return False
    backup = client_path.with_name(client_path.name + ".bak")
    try:
        backup.write_bytes(client_path.read_bytes())
    except OSError:
        pass
    try:
        client_path.unlink()
    except OSError:
        return False
    try:
        _metadata_cache_path(server_name).unlink(missing_ok=True)
    except OSError:
        pass
    return True


def _is_invalid_client_error_text(text: str) -> bool:
    return re.search(r"\binvalid_client\b", text.lower()) is not None


def _truthy(value, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() not in {"", "0", "false", "no", "off"}


def _int_value(value, *, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _find_free_local_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def _dedupe(values: list[str]) -> list[str]:
    out = []
    seen = set()
    for value in values:
        if value and value not in seen:
            out.append(value)
            seen.add(value)
    return out


def _fingerprintable_metadata(metadata: dict) -> dict:
    return {
        key: metadata.get(key)
        for key in (
            "issuer",
            "authorization_endpoint",
            "token_endpoint",
            "registration_endpoint",
        )
        if metadata.get(key)
    }


def _fingerprintable_client_info(client_info: dict) -> dict:
    return {
        "client_id": client_info.get("client_id"),
        "client_secret": bool(client_info.get("client_secret")),
    }


def _auth_store_mtime(auth: OAuthAuth) -> int:
    try:
        return auth.store.path.stat().st_mtime_ns
    except OSError:
        return 0


def _failed_access_token(error_context) -> str | None:
    if isinstance(error_context, dict):
        token = error_context.get("failed_access_token") or error_context.get("access_token")
        return str(token) if token else None
    token = getattr(error_context, "failed_access_token", None) or getattr(
        error_context,
        "access_token",
        None,
    )
    return str(token) if token else None
