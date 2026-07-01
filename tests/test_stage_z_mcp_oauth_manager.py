"""Focused Stage Z MCP OAuth manager parity regressions.

Hermes' MCP OAuth manager picks up externally-written disk tokens on the next
auth flow and deduplicates concurrent 401 recovery for the same failed token.
AEGIS currently exercises MCP OAuth through ``OAuthAuth`` plus the HTTP MCP
client boundary, so these tests pin the same contracts at that boundary.
"""

from __future__ import annotations

import copy
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


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
    return 200, {}, {
        "jsonrpc": "2.0",
        "id": payload["id"],
        "result": {
            "content": [{"type": "text", "text": "ok"}],
        },
    }


def _future_expiry() -> float:
    return time.time() + 3600


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


def test_existing_http_mcp_oauth_client_picks_up_disk_token_change(isolated_home) -> None:
    """An already-built HTTP MCP OAuth client should read a fresh disk token."""
    server_name = "remote"
    _write_mcp_oauth_token(isolated_home, server_name, {
        "access_token": "old-access",
        "refresh_token": "refresh-token",
        "token_type": "Bearer",
        "expires_at": _future_expiry(),
    })

    with _OAuthMCPServer() as http:
        seen_tool_auth: list[str | None] = []

        def mcp_handler(event):
            payload = event.get("json") or {}
            auth = event["headers"].get("authorization")
            if payload.get("method") == "tools/call":
                seen_tool_auth.append(auth)
            return _mcp_ok(event)

        http.mcp_handler = mcp_handler
        client = _client_from_oauth_spec(
            _config_for(server_name, http.url("/mcp"), http.url("/token")),
            server_name,
        )
        client.connect()

        _write_mcp_oauth_token(isolated_home, server_name, {
            "access_token": "new-access",
            "refresh_token": "refresh-token",
            "token_type": "Bearer",
            "expires_at": _future_expiry(),
        })

        content, is_error = client.call_tool("probe", {})

    assert (content, is_error) == ("ok", False)
    assert seen_tool_auth == ["Bearer new-access"]


def test_concurrent_same_token_mcp_oauth_401_reports_dedupe_refresh_attempts(
    isolated_home,
) -> None:
    """Concurrent 401 reports for the same MCP token should perform one refresh."""
    server_name = "remote"
    _write_mcp_oauth_token(isolated_home, server_name, {
        "access_token": "stale-access",
        "refresh_token": "refresh-token",
        "token_type": "Bearer",
        "expires_at": _future_expiry(),
    })

    with _OAuthMCPServer() as http:
        lock = threading.Lock()
        token_payloads: list[dict] = []

        def token_handler(event):
            payload = _token_request_payload(event)
            with lock:
                token_payloads.append(payload)
                n = len(token_payloads)
            time.sleep(0.05)
            return 200, {}, {
                "access_token": f"fresh-access-{n}",
                "refresh_token": f"refresh-token-{n}",
                "token_type": "Bearer",
                "expires_in": 3600,
            }

        http.token_handler = token_handler
        client = _client_from_oauth_spec(
            _config_for(server_name, http.url("/mcp"), http.url("/token")),
            server_name,
        )
        assert client.oauth is not None

        start = threading.Barrier(3)
        results: list[bool] = []
        errors: list[BaseException] = []

        def report_401() -> None:
            try:
                start.wait(timeout=2)
                results.append(client.oauth.report("auth", {
                    "status_code": 401,
                    "message": "expired",
                    "failed_access_token": "stale-access",
                }))
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=report_401) for _ in range(2)]
        for thread in threads:
            thread.start()
        start.wait(timeout=2)
        for thread in threads:
            thread.join(timeout=5)

    assert not errors
    assert results == [True, True]
    assert len(token_payloads) == 1, (
        "same-token concurrent MCP OAuth 401 recovery should share one refresh; "
        f"got {len(token_payloads)} refresh attempts"
    )
    assert token_payloads[0]["grant_type"] == "refresh_token"
