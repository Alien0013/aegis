"""Stage Z MCP OAuth invalid_client cache-healing parity regressions.

Hermes treats ``invalid_client`` from the OAuth token endpoint as proof that a
cached dynamic MCP client registration is dead. AEGIS should do the same for
MCP-owned dynamic registration caches under ``$AEGIS_HOME/mcp-oauth`` while
leaving statically configured client ids alone.
"""

from __future__ import annotations

import copy
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest


class _InvalidClientOAuthServer:
    def __init__(self) -> None:
        self.events: list[dict] = []
        self.discovery_enabled = True
        self.invalid_client = False
        self.registration_count = 0
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler_cls())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> "_InvalidClientOAuthServer":
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    def url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    @property
    def token_url(self) -> str:
        return self.url("/oauth/token")

    @property
    def authorize_url(self) -> str:
        return self.url("/oauth/authorize")

    @property
    def registration_url(self) -> str:
        return self.url("/oauth/register")

    def clear_events(self) -> None:
        self.events.clear()

    def discovery_events(self) -> list[dict]:
        return [
            event for event in self.events
            if event["method"] == "GET" and ".well-known" in event["path"]
        ]

    def registration_events(self) -> list[dict]:
        return [
            event for event in self.events
            if event["method"] == "POST" and event["path"] == "/oauth/register"
        ]

    def token_events(self) -> list[dict]:
        return [
            event for event in self.events
            if event["method"] == "POST" and event["path"] == "/oauth/token"
        ]

    def _handler_cls(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):  # noqa: A002, ANN001
                return

            def do_GET(self):  # noqa: N802
                path = urlparse(self.path).path
                outer.events.append({
                    "method": "GET",
                    "path": path,
                    "headers": {k.lower(): v for k, v in self.headers.items()},
                })
                if not outer.discovery_enabled:
                    self._send(500, {"error": "discovery disabled"})
                    return
                if "oauth-protected-resource" in path:
                    self._send(200, {
                        "resource": outer.url("/mcp"),
                        "authorization_servers": [outer.base_url],
                    })
                    return
                if (
                    "oauth-authorization-server" in path
                    or "openid-configuration" in path
                ):
                    self._send(200, {
                        "issuer": outer.base_url,
                        "authorization_endpoint": outer.authorize_url,
                        "token_endpoint": outer.token_url,
                        "registration_endpoint": outer.registration_url,
                        "response_types_supported": ["code"],
                        "grant_types_supported": [
                            "authorization_code",
                            "refresh_token",
                        ],
                        "token_endpoint_auth_methods_supported": [
                            "client_secret_post",
                            "none",
                        ],
                    })
                    return
                self._send(404, {"error": "not found"})

            def do_POST(self):  # noqa: N802
                path = urlparse(self.path).path
                length = int(self.headers.get("Content-Length") or "0")
                raw = self.rfile.read(length)
                event = {
                    "method": "POST",
                    "path": path,
                    "headers": {k.lower(): v for k, v in self.headers.items()},
                    "body": raw,
                    "json": _maybe_json(raw),
                    "form": _maybe_form(raw),
                }
                outer.events.append(event)
                if path == "/oauth/token":
                    if outer.invalid_client:
                        self._send(400, {
                            "error": "invalid_client",
                            "error_description": "client registration is gone",
                        })
                        return
                    self._send(200, {
                        "access_token": "fresh-access",
                        "refresh_token": "refresh-token-2",
                        "token_type": "Bearer",
                        "expires_in": 3600,
                    })
                    return
                if path == "/oauth/register":
                    outer.registration_count += 1
                    self._send(201, {
                        "client_id": f"dynamic-client-{outer.registration_count}",
                        "client_secret": f"dynamic-secret-{outer.registration_count}",
                        "client_id_issued_at": 1,
                        "client_secret_expires_at": 0,
                    })
                    return
                if path == "/mcp":
                    payload = event.get("json") or {}
                    if "id" not in payload:
                        self._send_empty(202)
                        return
                    self._send(200, {
                        "jsonrpc": "2.0",
                        "id": payload["id"],
                        "result": {},
                    })
                    return
                self._send(404, {"error": "not found"})

            def _send(self, status: int, body: dict) -> None:
                body_bytes = json.dumps(body).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body_bytes)))
                self.end_headers()
                self.wfile.write(body_bytes)

            def _send_empty(self, status: int) -> None:
                self.send_response(status)
                self.send_header("Content-Length", "0")
                self.end_headers()

        return Handler


def _maybe_json(raw: bytes):
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def _maybe_form(raw: bytes) -> dict[str, str]:
    try:
        parsed = parse_qs(raw.decode("utf-8"), keep_blank_values=True)
    except UnicodeDecodeError:
        return {}
    return {key: values[-1] for key, values in parsed.items() if values}


def _token_payload(event: dict) -> dict:
    if isinstance(event.get("json"), dict):
        return event["json"]
    return event.get("form") or {}


def _config_for_dynamic_oauth(server_name: str, mcp_url: str):
    from aegis.config import Config, DEFAULT_CONFIG

    config = Config(copy.deepcopy(DEFAULT_CONFIG))
    config.data.setdefault("mcp", {})["servers"] = {
        server_name: {
            "url": mcp_url,
            "auth": "oauth",
            "oauth": {
                "client_name": "AEGIS Stage Z Test",
                "scope": "read write",
            },
        }
    }
    return config


def _config_for_static_oauth(
    server_name: str,
    mcp_url: str,
    token_url: str,
    authorize_url: str,
):
    from aegis.config import Config, DEFAULT_CONFIG

    config = Config(copy.deepcopy(DEFAULT_CONFIG))
    config.data.setdefault("mcp", {})["servers"] = {
        server_name: {
            "url": mcp_url,
            "auth": "oauth",
            "oauth": {
                "client_id": "static-client",
                "client_secret": "static-secret",
                "token_url": token_url,
                "authorize_url": authorize_url,
                "registration_endpoint": token_url.rsplit("/", 1)[0] + "/register",
            },
        }
    }
    return config


def _client_from_config(config, server_name: str):
    from aegis.mcp.client import build_manager

    manager = build_manager(config)
    assert len(manager.clients) == 1
    assert manager.clients[0].name == server_name
    return manager.clients[0]


def _reset_oauth_manager() -> None:
    from aegis.mcp.oauth_manager import reset_mcp_oauth_manager_for_tests

    reset_mcp_oauth_manager_for_tests()


def _write_expired_mcp_oauth_token(home: str, server_name: str) -> None:
    from aegis.providers.auth import AuthStore

    AuthStore(Path(home) / "auth.json").save(f"mcp:{server_name}", {
        "access_token": "expired-access",
        "refresh_token": "refresh-token",
        "token_type": "Bearer",
        "expires_at": time.time() - 60,
    })


def _cache_path(home: str, server_name: str, suffix: str) -> Path:
    return Path(home) / "mcp-oauth" / f"{server_name}.{suffix}.json"


def _cache_data(home: str, server_name: str, suffix: str) -> dict:
    path = _cache_path(home, server_name, suffix)
    assert path.exists()
    return json.loads(path.read_text(encoding="utf-8"))


def _cache_absent_or_poisoned(path: Path) -> bool:
    if not path.exists():
        return True
    data = json.loads(path.read_text(encoding="utf-8"))
    return bool(data.get("poisoned") or data.get("invalidated"))


def _json_contains_value(node, expected: str) -> bool:
    if isinstance(node, dict):
        return any(_json_contains_value(value, expected) for value in node.values())
    if isinstance(node, list):
        return any(_json_contains_value(value, expected) for value in node)
    return node == expected


def _connect_allowing_invalid_client_failure(client) -> None:
    try:
        client.connect()
    except Exception as exc:  # noqa: BLE001
        assert "invalid_client" in str(exc) or "Token endpoint 400" in str(exc)


def test_dynamic_invalid_client_refresh_poisons_mcp_oauth_caches_for_reregister(
    isolated_home,
) -> None:
    """invalid_client from a discovered token endpoint should retire caches."""
    server_name = "invalid-client-dynamic"
    _write_expired_mcp_oauth_token(isolated_home, server_name)

    with _InvalidClientOAuthServer() as http:
        client = _client_from_config(
            _config_for_dynamic_oauth(server_name, http.url("/mcp")),
            server_name,
        )
        assert client.oauth is not None
        assert client.oauth.oauth.client_id == "dynamic-client-1"
        assert len(http.registration_events()) == 1
        assert _json_contains_value(
            _cache_data(isolated_home, server_name, "client"),
            "dynamic-client-1",
        )

        http.invalid_client = True
        _connect_allowing_invalid_client_failure(client)

        token_payload = _token_payload(http.token_events()[0])
        assert token_payload["grant_type"] == "refresh_token"
        assert token_payload["client_id"] == "dynamic-client-1"
        assert token_payload["client_secret"] == "dynamic-secret-1"

        client_cache = _cache_path(isolated_home, server_name, "client")
        metadata_cache = _cache_path(isolated_home, server_name, "metadata")
        assert _cache_absent_or_poisoned(client_cache)
        assert _cache_absent_or_poisoned(metadata_cache)

        _reset_oauth_manager()
        http.invalid_client = False
        http.clear_events()

        rebuilt = _client_from_config(
            _config_for_dynamic_oauth(server_name, http.url("/mcp")),
            server_name,
        )

    assert rebuilt.oauth is not None
    assert rebuilt.oauth.oauth.client_id == "dynamic-client-2"
    assert len(http.discovery_events()) >= 2
    assert len(http.registration_events()) == 1
    assert _json_contains_value(
        _cache_data(isolated_home, server_name, "client"),
        "dynamic-client-2",
    )


def test_static_invalid_client_refresh_does_not_dynamic_register(
    isolated_home,
) -> None:
    """A configured client_id is a config problem, not a dynamic-cache problem."""
    server_name = "invalid-client-static"
    _write_expired_mcp_oauth_token(isolated_home, server_name)

    with _InvalidClientOAuthServer() as http:
        client = _client_from_config(
            _config_for_static_oauth(
                server_name,
                http.url("/mcp"),
                http.token_url,
                http.authorize_url,
            ),
            server_name,
        )
        assert client.oauth is not None
        assert client.oauth.oauth.client_id == "static-client"

        http.invalid_client = True
        with pytest.raises(Exception, match="invalid_client|Token endpoint 400"):
            client.connect()

        token_payload = _token_payload(http.token_events()[0])
        assert token_payload["grant_type"] == "refresh_token"
        assert token_payload["client_id"] == "static-client"
        assert token_payload["client_secret"] == "static-secret"

        _reset_oauth_manager()
        http.invalid_client = False
        http.clear_events()

        rebuilt = _client_from_config(
            _config_for_static_oauth(
                server_name,
                http.url("/mcp"),
                http.token_url,
                http.authorize_url,
            ),
            server_name,
        )

    assert rebuilt.oauth is not None
    assert rebuilt.oauth.oauth.client_id == "static-client"
    assert http.discovery_events() == []
    assert http.registration_events() == []
    assert not _cache_path(isolated_home, server_name, "client").exists()
