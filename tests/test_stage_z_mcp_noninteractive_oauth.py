"""Stage Z MCP noninteractive OAuth startup contracts."""

from __future__ import annotations

import copy
import time


def _config_for_oauth_mcp(server_name: str = "remote"):
    from aegis.config import Config, DEFAULT_CONFIG

    config = Config(copy.deepcopy(DEFAULT_CONFIG))
    config.data.setdefault("mcp", {})["servers"] = {
        server_name: {
            "url": "https://mcp.example.test/mcp",
            "auth": "oauth",
            "oauth": {
                "client_id": "mcp-client",
                "client_secret": "mcp-secret",
                "token_url": "https://auth.example.test/token",
            },
        }
    }
    return config


def test_connect_all_marks_oauth_without_cached_tokens_auth_needed_without_http(
    monkeypatch,
    capsys,
) -> None:
    """No-token OAuth MCP startup should disable the server before I/O.

    This is the AEGIS synchronous equivalent of Hermes' noninteractive OAuth
    gate: automatic startup must not try to launch a browser, build a dynamic
    OAuth flow, or touch the MCP endpoint when no login material is cached.
    """
    from aegis.mcp import client as mcp_client

    def fail_oauth_manager():
        raise AssertionError("OAuth manager should not be built without tokens")

    def fail_http_client(*_args, **_kwargs):
        raise AssertionError("HTTP should not be touched without tokens")

    monkeypatch.setattr(mcp_client, "get_mcp_oauth_manager", fail_oauth_manager)
    monkeypatch.setattr(mcp_client.httpx, "Client", fail_http_client)

    manager = mcp_client.build_manager(_config_for_oauth_mcp())
    assert len(manager.clients) == 1
    client = manager.clients[0]
    assert client.oauth_required is True
    assert client.oauth is None

    tools = manager.connect_all()

    out = capsys.readouterr().out
    assert tools == []
    assert client.state == "auth_needed"
    assert client.auth_needed is True
    assert client.auth_refresh_needed is True
    assert client.disabled is True
    assert client.disabled_reason == "auth_needed"
    assert "aegis mcp login remote" in client.last_error
    assert "aegis mcp login remote" in out


def test_cached_oauth_material_keeps_startup_on_normal_oauth_path(
    monkeypatch,
) -> None:
    """Cached token material should still construct the managed OAuth provider."""
    from aegis.mcp import client as mcp_client
    from aegis.providers.auth import AuthStore

    AuthStore().save("mcp:remote", {
        "access_token": "cached-access",
        "refresh_token": "cached-refresh",
        "token_type": "Bearer",
        "expires_at": time.time() + 3600,
    })

    calls: list[tuple[str, str]] = []

    class FakeOAuth:
        oauth = type("OAuth", (), {"provider": "mcp:remote"})()

        def available(self) -> bool:
            return True

        def headers(self) -> dict[str, str]:
            return {"Authorization": "Bearer cached-access"}

    class FakeManager:
        def get_or_build_auth(self, name, url, spec):  # noqa: ANN001
            calls.append((name, url))
            return FakeOAuth()

    monkeypatch.setattr(mcp_client, "get_mcp_oauth_manager", lambda: FakeManager())

    manager = mcp_client.build_manager(_config_for_oauth_mcp())

    assert calls == [("remote", "https://mcp.example.test/mcp")]
    assert len(manager.clients) == 1
    client = manager.clients[0]
    assert client.oauth_required is True
    assert client.oauth is not None
    assert client.needs_oauth_login_before_startup() is False
