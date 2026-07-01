"""Stage Z MCP OAuth metadata/bootstrap parity regressions.

Hermes can bootstrap MCP OAuth config from protected-resource metadata plus
authorization-server metadata, then persist that discovered OAuth server/client
state so a later process does not need to refetch discovery. These tests pin the
same AEGIS MCP boundary without relying on the MCP SDK being installed.
"""

from __future__ import annotations

import copy
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


class _MetadataOAuthServer:
    def __init__(self) -> None:
        self.events: list[dict] = []
        self.discovery_enabled = True
        self.registration_count = 0
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler_cls())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> "_MetadataOAuthServer":
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

    def mcp_events(self) -> list[dict]:
        return [
            event for event in self.events
            if event["method"] == "POST" and event["path"] == "/mcp"
        ]

    def _handler_cls(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):  # noqa: A002, ANN001
                return

            def do_GET(self):  # noqa: N802
                path = urlparse(self.path).path
                event = {"method": "GET", "path": path, "headers": dict(self.headers)}
                outer.events.append(event)
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
                        "code_challenge_methods_supported": ["S256"],
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
                    self._send(200, {
                        "access_token": "fresh-discovered-access",
                        "refresh_token": "refresh-token-2",
                        "token_type": "Bearer",
                        "expires_in": 3600,
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
                if path != "/oauth/register":
                    self._send(404, {"error": "not found"})
                    return
                outer.registration_count += 1
                self._send(201, {
                    "client_id": f"dynamic-mcp-client-{outer.registration_count}",
                    "client_secret": f"dynamic-mcp-secret-{outer.registration_count}",
                    "client_id_issued_at": 1,
                    "client_secret_expires_at": 0,
                })

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


def _config_without_static_oauth(server_name: str, mcp_url: str):
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


def _metadata_cache_data(home: str, server_name: str) -> dict:
    path = Path(home) / "mcp-oauth" / f"{server_name}.metadata.json"
    assert path.exists()
    return json.loads(path.read_text(encoding="utf-8"))


def _client_cache_data(home: str, server_name: str) -> dict:
    path = Path(home) / "mcp-oauth" / f"{server_name}.client.json"
    assert path.exists()
    return json.loads(path.read_text(encoding="utf-8"))


def _json_contains_value(node, expected: str) -> bool:
    if isinstance(node, dict):
        return any(_json_contains_value(value, expected) for value in node.values())
    if isinstance(node, list):
        return any(_json_contains_value(value, expected) for value in node)
    return node == expected


def test_mcp_oauth_bootstraps_from_metadata_without_static_client_or_token_url(
    isolated_home,
) -> None:
    """OAuth MCP specs should discover token/client config from RFC metadata."""
    server_name = "metadata-remote"
    with _MetadataOAuthServer() as http:
        client = _client_from_config(
            _config_without_static_oauth(server_name, http.url("/mcp")),
            server_name,
        )

    assert client.oauth is not None
    assert client.oauth.oauth.provider == f"mcp:{server_name}"
    assert client.oauth.oauth.client_id == "dynamic-mcp-client-1"
    assert client.oauth.oauth.client_secret == "dynamic-mcp-secret-1"
    assert client.oauth.oauth.authorize_url == http.authorize_url
    assert client.oauth.oauth.token_url == http.token_url
    assert client.oauth.oauth.scopes == ["read", "write"]
    assert any(
        "oauth-protected-resource" in event["path"]
        for event in http.discovery_events()
    )
    assert any(
        "oauth-authorization-server" in event["path"]
        or "openid-configuration" in event["path"]
        for event in http.discovery_events()
    )
    assert len(http.registration_events()) == 1

    stored_metadata = _metadata_cache_data(isolated_home, server_name)
    stored_client = _client_cache_data(isolated_home, server_name)
    assert _json_contains_value(stored_metadata, http.token_url)
    assert _json_contains_value(stored_client, "dynamic-mcp-client-1")


def test_mcp_oauth_metadata_bootstrap_is_persisted_and_reused_without_refetch(
    isolated_home,
) -> None:
    """A second manager build should reuse persisted metadata/client bootstrap."""
    server_name = "metadata-reuse"
    with _MetadataOAuthServer() as http:
        first = _client_from_config(
            _config_without_static_oauth(server_name, http.url("/mcp")),
            server_name,
        )
        assert first.oauth is not None
        assert first.oauth.oauth.token_url == http.token_url
        assert first.oauth.oauth.client_id == "dynamic-mcp-client-1"
        assert http.discovery_events()
        assert len(http.registration_events()) == 1

        _reset_oauth_manager()
        http.events.clear()
        http.discovery_enabled = False

        second = _client_from_config(
            _config_without_static_oauth(server_name, http.url("/mcp")),
            server_name,
        )

    assert second.oauth is not None
    assert second.oauth.oauth.token_url == http.token_url
    assert second.oauth.oauth.authorize_url == http.authorize_url
    assert second.oauth.oauth.client_id == "dynamic-mcp-client-1"
    assert second.oauth.oauth.client_secret == "dynamic-mcp-secret-1"
    assert http.discovery_events() == []
    assert http.registration_events() == []

    stored_metadata = _metadata_cache_data(isolated_home, server_name)
    stored_client = _client_cache_data(isolated_home, server_name)
    assert _json_contains_value(stored_metadata, http.token_url)
    assert _json_contains_value(stored_client, "dynamic-mcp-client-1")


def test_mcp_oauth_metadata_bootstrap_refreshes_expired_disk_token(
    isolated_home,
) -> None:
    """Cold-start refresh should use discovered metadata, not guessed /token."""
    server_name = "metadata-refresh"
    _write_expired_mcp_oauth_token(isolated_home, server_name)

    with _MetadataOAuthServer() as http:
        client = _client_from_config(
            _config_without_static_oauth(server_name, http.url("/mcp")),
            server_name,
        )

        client.connect()

    assert len(http.registration_events()) == 1
    token_events = http.token_events()
    assert len(token_events) == 1
    payload = token_events[0]["form"] or token_events[0]["json"]
    assert payload["grant_type"] == "refresh_token"
    assert payload["refresh_token"] == "refresh-token"
    assert payload["client_id"] == "dynamic-mcp-client-1"
    assert payload["client_secret"] == "dynamic-mcp-secret-1"
    assert [event["headers"].get("authorization") for event in http.mcp_events()] == [
        "Bearer fresh-discovered-access",
        "Bearer fresh-discovered-access",
    ]

    stored_metadata = _metadata_cache_data(isolated_home, server_name)
    stored_client = _client_cache_data(isolated_home, server_name)
    assert _json_contains_value(stored_metadata, http.token_url)
    assert _json_contains_value(stored_client, "dynamic-mcp-client-1")
