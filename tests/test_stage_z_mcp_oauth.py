"""Stage Z MCP OAuth parity contracts.

Hermes persists per-server MCP OAuth tokens on disk, reloads them for HTTP MCP
requests, refreshes expired tokens before the first request, and uses 401s as a
recoverable refresh/retry signal when a refresh token is available. AEGIS' MCP
client currently expresses that through its disk-backed AuthStore provider id
``mcp:<server>``; these tests pin the behavior at the MCP config/client boundary.
"""

from __future__ import annotations

import copy
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest


class _OAuthMCPServer:
    def __init__(self):
        self.events: list[dict] = []
        self.mcp_handler = None
        self.token_handler = None
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler_cls())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> "_OAuthMCPServer":
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

    def _handler_cls(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):  # noqa: A002, ANN001
                return

            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length") or "0")
                raw = self.rfile.read(length)
                event = {
                    "path": urlparse(self.path).path,
                    "headers": {k.lower(): v for k, v in self.headers.items()},
                    "body": raw,
                    "json": _maybe_json(raw),
                    "form": _maybe_form(raw),
                }
                outer.events.append(event)

                if event["path"] == "/token" and outer.token_handler:
                    status, headers, body = outer.token_handler(event)
                elif event["path"] == "/mcp" and outer.mcp_handler:
                    status, headers, body = outer.mcp_handler(event)
                else:
                    status, headers, body = 404, {}, {"error": "not found"}

                if isinstance(body, (dict, list)):
                    body_bytes = json.dumps(body).encode("utf-8")
                    headers = {"Content-Type": "application/json", **headers}
                elif isinstance(body, str):
                    body_bytes = body.encode("utf-8")
                elif body is None:
                    body_bytes = b""
                else:
                    body_bytes = bytes(body)

                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(body_bytes)))
                self.end_headers()
                if body_bytes:
                    self.wfile.write(body_bytes)

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


def _token_request_payload(event: dict) -> dict:
    if isinstance(event.get("json"), dict):
        return event["json"]
    return event.get("form") or {}


def _mcp_ok(event: dict) -> tuple[int, dict, dict | None]:
    payload = event.get("json") or {}
    if "id" not in payload:
        return 202, {}, None
    return 200, {}, {"jsonrpc": "2.0", "id": payload["id"], "result": {}}


def _future_expiry() -> float:
    return time.time() + 3600


def _expired() -> float:
    return time.time() - 60


def _write_mcp_oauth_token(home: str, server_name: str, token: dict) -> Path:
    from aegis.providers.auth import AuthStore

    path = Path(home) / "auth.json"
    AuthStore(path).save(f"mcp:{server_name}", token)
    return path


def _config_for(server_name: str, mcp_url: str, token_url: str):
    from aegis.config import Config, DEFAULT_CONFIG

    config = Config(copy.deepcopy(DEFAULT_CONFIG))
    config.data.setdefault("mcp", {})["servers"] = {
        server_name: {
            "url": mcp_url,
            "auth": "oauth",
            "oauth": {
                "client_id": "mcp-client",
                "client_secret": "mcp-secret",
                "token_url": token_url,
            },
        }
    }
    return config


def _client_from_oauth_spec(config, server_name: str):
    from aegis.mcp.client import build_manager

    manager = build_manager(config)
    assert len(manager.clients) == 1
    assert manager.clients[0].name == server_name
    return manager.clients[0]


def test_http_mcp_oauth_loads_disk_access_token_for_bearer_auth(isolated_home) -> None:
    """A configured OAuth HTTP MCP server should send the cached access token."""
    server_name = "remote"
    _write_mcp_oauth_token(isolated_home, server_name, {
        "access_token": "disk-access",
        "refresh_token": "refresh-token",
        "token_type": "Bearer",
        "expires_at": _future_expiry(),
    })

    with _OAuthMCPServer() as http:
        def mcp_handler(event):
            assert event["headers"].get("authorization") == "Bearer disk-access"
            return _mcp_ok(event)

        http.mcp_handler = mcp_handler
        client = _client_from_oauth_spec(
            _config_for(server_name, http.url("/mcp"), http.url("/token")),
            server_name,
        )

        client.connect()

    mcp_events = [event for event in http.events if event["path"] == "/mcp"]
    assert [event["headers"].get("authorization") for event in mcp_events] == [
        "Bearer disk-access",
        "Bearer disk-access",
    ]


def test_http_mcp_oauth_refreshes_expired_token_before_request(isolated_home) -> None:
    """Expired disk tokens should refresh through token_url before MCP traffic."""
    server_name = "remote"
    token_path = _write_mcp_oauth_token(isolated_home, server_name, {
        "access_token": "expired-access",
        "refresh_token": "refresh-token",
        "token_type": "Bearer",
        "expires_at": _expired(),
    })

    with _OAuthMCPServer() as http:
        def token_handler(event):
            payload = _token_request_payload(event)
            assert payload["grant_type"] == "refresh_token"
            assert payload["refresh_token"] == "refresh-token"
            assert payload["client_id"] == "mcp-client"
            assert payload["client_secret"] == "mcp-secret"
            return 200, {}, {
                "access_token": "fresh-access",
                "refresh_token": "refresh-token-2",
                "token_type": "Bearer",
                "expires_in": 3600,
            }

        def mcp_handler(event):
            assert event["headers"].get("authorization") == "Bearer fresh-access"
            return _mcp_ok(event)

        http.token_handler = token_handler
        http.mcp_handler = mcp_handler
        client = _client_from_oauth_spec(
            _config_for(server_name, http.url("/mcp"), http.url("/token")),
            server_name,
        )

        client.connect()

    assert [event["path"] for event in http.events][:2] == ["/token", "/mcp"]
    saved = json.loads(token_path.read_text(encoding="utf-8"))[f"mcp:{server_name}"]
    assert saved["access_token"] == "fresh-access"
    assert saved["refresh_token"] == "refresh-token-2"
    assert saved["expires_at"] > time.time()


def test_http_mcp_oauth_401_refreshes_and_retries_request(isolated_home) -> None:
    """A 401 with a refresh token should refresh once and retry the MCP request."""
    server_name = "remote"
    _write_mcp_oauth_token(isolated_home, server_name, {
        "access_token": "stale-access",
        "refresh_token": "refresh-token",
        "token_type": "Bearer",
        "expires_at": _future_expiry(),
    })

    with _OAuthMCPServer() as http:
        mcp_authorizations: list[str | None] = []

        def token_handler(event):
            payload = _token_request_payload(event)
            assert payload["grant_type"] == "refresh_token"
            assert payload["refresh_token"] == "refresh-token"
            return 200, {}, {
                "access_token": "fresh-access",
                "refresh_token": "refresh-token",
                "token_type": "Bearer",
                "expires_in": 3600,
            }

        def mcp_handler(event):
            auth = event["headers"].get("authorization")
            mcp_authorizations.append(auth)
            if auth == "Bearer stale-access":
                return 401, {"WWW-Authenticate": 'Bearer error="invalid_token"'}, "expired"
            assert auth == "Bearer fresh-access"
            return _mcp_ok(event)

        http.token_handler = token_handler
        http.mcp_handler = mcp_handler
        client = _client_from_oauth_spec(
            _config_for(server_name, http.url("/mcp"), http.url("/token")),
            server_name,
        )

        client.connect()

    assert mcp_authorizations[:2] == ["Bearer stale-access", "Bearer fresh-access"]
    assert len([event for event in http.events if event["path"] == "/token"]) == 1
    assert client.state == "connected"
    assert client.auth_refresh_needed is False


def test_http_mcp_oauth_401_without_refresh_marks_auth_refresh_needed(isolated_home) -> None:
    """A 401 without refresh material should stop as an auth-refresh-needed state."""
    server_name = "remote"
    _write_mcp_oauth_token(isolated_home, server_name, {
        "access_token": "stale-access",
        "token_type": "Bearer",
        "expires_at": _future_expiry(),
    })

    with _OAuthMCPServer() as http:
        def token_handler(event):
            raise AssertionError("no refresh token exists, so token_url should not be called")

        def mcp_handler(event):
            assert event["headers"].get("authorization") == "Bearer stale-access"
            return 401, {"WWW-Authenticate": 'Bearer error="invalid_token"'}, "expired"

        http.token_handler = token_handler
        http.mcp_handler = mcp_handler
        client = _client_from_oauth_spec(
            _config_for(server_name, http.url("/mcp"), http.url("/token")),
            server_name,
        )

        with pytest.raises(Exception, match="HTTP 401"):
            client.connect()

    assert [event["path"] for event in http.events] == ["/mcp"]
    assert client.state == "auth_refresh_needed"
    assert client.auth_refresh_needed is True


def test_http_mcp_oauth_existing_client_picks_up_external_disk_token(isolated_home) -> None:
    """A long-lived OAuth MCP client should notice tokens refreshed on disk."""
    server_name = "remote-disk-refresh"
    token_path = _write_mcp_oauth_token(isolated_home, server_name, {
        "access_token": "first-access",
        "refresh_token": "refresh-token",
        "token_type": "Bearer",
        "expires_at": _future_expiry(),
    })

    with _OAuthMCPServer() as http:
        authorizations: list[str | None] = []

        def mcp_handler(event):
            authorizations.append(event["headers"].get("authorization"))
            return _mcp_ok(event)

        http.mcp_handler = mcp_handler
        client = _client_from_oauth_spec(
            _config_for(server_name, http.url("/mcp"), http.url("/token")),
            server_name,
        )
        assert client.oauth is not None

        client.connect()

        old_mtime = token_path.stat().st_mtime_ns
        for _ in range(5):
            time.sleep(0.02)
            _write_mcp_oauth_token(isolated_home, server_name, {
                "access_token": "external-access",
                "refresh_token": "refresh-token",
                "token_type": "Bearer",
                "expires_at": _future_expiry(),
            })
            if token_path.stat().st_mtime_ns != old_mtime:
                break

        assert client.oauth.manager.invalidate_if_disk_changed(server_name) is True
        client.list_tools(force=True)

    assert authorizations[:2] == ["Bearer first-access", "Bearer first-access"]
    assert authorizations[-1] == "Bearer external-access"


def test_mcp_oauth_manager_dedupes_concurrent_same_token_401_refresh(isolated_home) -> None:
    """Same-token concurrent 401s should share one refresh attempt."""
    server_name = "remote-dedupe"
    token_path = _write_mcp_oauth_token(isolated_home, server_name, {
        "access_token": "stale-access",
        "refresh_token": "refresh-token",
        "token_type": "Bearer",
        "expires_at": _future_expiry(),
    })

    with _OAuthMCPServer() as http:
        client = _client_from_oauth_spec(
            _config_for(server_name, http.url("/mcp"), http.url("/token")),
            server_name,
        )
    assert client.oauth is not None
    calls: list[dict] = []
    calls_lock = threading.Lock()
    barrier = threading.Barrier(5)

    def fake_post_token(payload: dict) -> dict:
        with calls_lock:
            calls.append(dict(payload))
        time.sleep(0.15)
        return {
            "access_token": "fresh-access",
            "refresh_token": "refresh-token",
            "token_type": "Bearer",
            "expires_in": 3600,
        }

    client.oauth.auth._post_token = fake_post_token

    def recover() -> bool:
        barrier.wait(timeout=3)
        return client.oauth.handle_401(
            "stale-access",
            {"status_code": 401, "message": "expired"},
        )

    with ThreadPoolExecutor(max_workers=5) as pool:
        results = [future.result(timeout=5) for future in [pool.submit(recover) for _ in range(5)]]

    assert results == [True, True, True, True, True]
    assert len(calls) == 1
    assert calls[0]["grant_type"] == "refresh_token"
    saved = json.loads(token_path.read_text(encoding="utf-8"))[f"mcp:{server_name}"]
    assert saved["access_token"] == "fresh-access"
