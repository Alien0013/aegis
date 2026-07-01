"""Stage Z MCP OAuth login parity regressions."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


class _LoginOAuthServer:
    def __init__(self) -> None:
        self.events: list[dict] = []
        self.registration_count = 0
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler_cls())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> "_LoginOAuthServer":
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
                if "oauth-protected-resource" in path:
                    self._send(200, {
                        "resource": outer.url("/mcp"),
                        "authorization_servers": [outer.base_url],
                    })
                    return
                if "oauth-authorization-server" in path:
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
                if path == "/oauth/register":
                    outer.registration_count += 1
                    payload = event["json"] or {}
                    self._send(201, {
                        "client_id": f"login-dynamic-client-{outer.registration_count}",
                        "client_secret": f"login-dynamic-secret-{outer.registration_count}",
                        "redirect_uris": payload.get("redirect_uris") or [],
                    })
                    return
                if path == "/oauth/token":
                    form = event["form"]
                    if form.get("grant_type") != "authorization_code":
                        self._send(400, {"error": "unsupported_grant_type"})
                        return
                    self._send(200, {
                        "access_token": "mcp-login-access",
                        "refresh_token": "mcp-login-refresh",
                        "token_type": "Bearer",
                        "expires_in": 3600,
                        "scope": "read write",
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


def _save_dynamic_mcp_server(server_name: str, mcp_url: str) -> None:
    from aegis.config import Config

    config = Config.load()
    config.data.setdefault("mcp", {})["servers"] = {
        server_name: {
            "url": mcp_url,
            "auth": "oauth",
            "oauth": {
                "client_name": "AEGIS Stage Z Login",
                "scope": "read write",
            },
        }
    }
    config.save()


def _save_static_mcp_server(
    server_name: str,
    mcp_url: str,
    token_url: str,
    authorize_url: str,
) -> None:
    from aegis.config import Config

    config = Config.load()
    config.data.setdefault("mcp", {})["servers"] = {
        server_name: {
            "url": mcp_url,
            "auth": "oauth",
            "oauth": {
                "client_id": "login-static-client",
                "client_secret": "login-static-secret",
                "token_url": token_url,
                "authorize_url": authorize_url,
                "scope": "read write",
            },
        }
    }
    config.save()


def test_mcp_login_discovers_registers_and_persists_manual_pkce_flow(
    isolated_home,
    monkeypatch,
    capsys,
) -> None:
    """`aegis mcp login` should perform real dynamic OAuth, not print a stub."""
    from aegis.cli.main import main
    from aegis.mcp.oauth_manager import reset_mcp_oauth_manager_for_tests
    from aegis.providers import auth as auth_mod
    from aegis.providers.auth import AuthStore

    reset_mcp_oauth_manager_for_tests()
    opened_urls: list[str] = []
    monkeypatch.setattr(auth_mod.webbrowser, "open", lambda url: opened_urls.append(url) or True)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "manual-login-code")

    server_name = "login-dynamic"
    AuthStore(Path(isolated_home) / "auth.json").save(f"mcp:{server_name}", {
        "access_token": "stale-access",
        "refresh_token": "stale-refresh",
        "token_type": "Bearer",
        "expires_at": time.time() + 3600,
    })
    cache_dir = Path(isolated_home) / "mcp-oauth"
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / f"{server_name}.client.json").write_text(
        json.dumps({
            "server_url": "http://stale.example/mcp",
            "client_info": {"client_id": "stale-client"},
        }),
        encoding="utf-8",
    )
    (cache_dir / f"{server_name}.metadata.json").write_text(
        json.dumps({
            "server_url": "http://stale.example/mcp",
            "metadata": {"token_endpoint": "http://stale.example/token"},
        }),
        encoding="utf-8",
    )

    with _LoginOAuthServer() as http:
        _save_dynamic_mcp_server(server_name, http.url("/mcp"))

        assert main(["mcp", "login", server_name, "--manual"]) == 0

        out = capsys.readouterr().out
        assert "OAuth login complete" in out
        assert len(http.discovery_events()) >= 2
        assert len(http.registration_events()) == 1
        assert len(http.token_events()) == 1

        registration = http.registration_events()[0]["json"]
        redirect_uri = registration["redirect_uris"][0]
        assert redirect_uri.startswith("http://127.0.0.1:")
        assert redirect_uri.endswith("/callback")

        token_form = http.token_events()[0]["form"]
        assert token_form["grant_type"] == "authorization_code"
        assert token_form["code"] == "manual-login-code"
        assert token_form["client_id"] == "login-dynamic-client-1"
        assert token_form["client_secret"] == "login-dynamic-secret-1"
        assert token_form["redirect_uri"] == redirect_uri
        assert token_form["code_verifier"]

        assert opened_urls
        authorize = urlparse(opened_urls[-1])
        authorize_params = parse_qs(authorize.query)
        assert f"{authorize.scheme}://{authorize.netloc}{authorize.path}" == http.authorize_url
        assert authorize_params["client_id"] == ["login-dynamic-client-1"]
        assert authorize_params["redirect_uri"] == [redirect_uri]
        assert authorize_params["code_challenge_method"] == ["S256"]
        assert authorize_params["scope"] == ["read write"]

    creds = AuthStore(Path(isolated_home) / "auth.json").load(f"mcp:{server_name}")
    assert creds is not None
    assert creds["access_token"] == "mcp-login-access"
    assert creds["refresh_token"] == "mcp-login-refresh"

    client_cache = Path(isolated_home) / "mcp-oauth" / f"{server_name}.client.json"
    metadata_cache = Path(isolated_home) / "mcp-oauth" / f"{server_name}.metadata.json"
    assert client_cache.exists()
    assert metadata_cache.exists()
    assert "stale-client" not in client_cache.read_text(encoding="utf-8")
    assert "stale.example" not in metadata_cache.read_text(encoding="utf-8")


def test_oauth_manual_callback_input_parser_accepts_hermes_paste_shapes() -> None:
    from aegis.providers.auth import _parse_oauth_callback_input

    assert _parse_oauth_callback_input(
        "http://127.0.0.1:12345/callback?code=abc&state=st"
    ) == ("abc", "st", None)
    assert _parse_oauth_callback_input(
        "https://mcp.example.com/callback?code=abc&state=st"
    ) == ("abc", "st", None)
    assert _parse_oauth_callback_input("?code=abc&state=st") == ("abc", "st", None)
    assert _parse_oauth_callback_input("code=abc&state=st") == ("abc", "st", None)
    assert _parse_oauth_callback_input("raw-code") == ("raw-code", None, None)
    assert _parse_oauth_callback_input(
        "code-from-provider#state-from-provider",
        code_contains_state=True,
    ) == ("code-from-provider", "state-from-provider", None)

    code, state, error = _parse_oauth_callback_input(
        "error=access_denied&error_description=nope"
    )
    assert code is None
    assert state is None
    assert error == "OAuth authorization failed: access_denied: nope"

    assert _parse_oauth_callback_input("skip") == (None, None, "OAuth login skipped")


def test_mcp_login_static_oauth_uses_configured_client_without_registration(
    isolated_home,
    monkeypatch,
    capsys,
) -> None:
    """Configured MCP OAuth clients should login without discovery or registration."""
    from aegis.cli.main import main
    from aegis.mcp.oauth_manager import reset_mcp_oauth_manager_for_tests
    from aegis.providers import auth as auth_mod
    from aegis.providers.auth import AuthStore

    reset_mcp_oauth_manager_for_tests()
    opened_urls: list[str] = []
    monkeypatch.setattr(auth_mod.webbrowser, "open", lambda url: opened_urls.append(url) or True)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "static-login-code")

    server_name = "login-static"
    with _LoginOAuthServer() as http:
        _save_static_mcp_server(
            server_name,
            http.url("/mcp"),
            http.token_url,
            http.authorize_url,
        )

        assert main(["mcp", "login", server_name, "--manual"]) == 0

        out = capsys.readouterr().out
        assert "OAuth login complete" in out
        assert http.discovery_events() == []
        assert http.registration_events() == []
        assert len(http.token_events()) == 1

        token_form = http.token_events()[0]["form"]
        assert token_form["grant_type"] == "authorization_code"
        assert token_form["code"] == "static-login-code"
        assert token_form["client_id"] == "login-static-client"
        assert token_form["client_secret"] == "login-static-secret"
        assert token_form["redirect_uri"].startswith("http://127.0.0.1:")
        assert token_form["redirect_uri"].endswith("/callback")
        assert token_form["code_verifier"]

        assert opened_urls
        authorize = urlparse(opened_urls[-1])
        authorize_params = parse_qs(authorize.query)
        assert f"{authorize.scheme}://{authorize.netloc}{authorize.path}" == http.authorize_url
        assert authorize_params["client_id"] == ["login-static-client"]
        assert authorize_params["redirect_uri"] == [token_form["redirect_uri"]]
        assert authorize_params["code_challenge_method"] == ["S256"]
        assert authorize_params["scope"] == ["read write"]

    creds = AuthStore(Path(isolated_home) / "auth.json").load(f"mcp:{server_name}")
    assert creds is not None
    assert creds["access_token"] == "mcp-login-access"
    assert creds["refresh_token"] == "mcp-login-refresh"
