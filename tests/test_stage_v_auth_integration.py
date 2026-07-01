from __future__ import annotations

import json
import time
from datetime import datetime, timezone

import pytest


@pytest.fixture(autouse=True)
def _isolated_home(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "home"))
    from aegis import credentials

    credentials.reset()
    yield
    credentials.reset()


def test_api_key_auth_reports_rate_limit_context_to_pool(monkeypatch):
    from aegis import credentials
    from aegis.config import Config
    from aegis.providers.auth import ApiKeyAuth

    monkeypatch.setenv("XAI_API_KEY", "sk-stage-v-alpha-0001,sk-stage-v-beta-0002")
    config = Config.load()
    auth = ApiKeyAuth(["XAI_API_KEY"], provider_name="xai", config=config)
    reset_at = datetime(2035, 1, 1, tzinfo=timezone.utc).isoformat()

    assert auth.report("rate_limit", {"status_code": 429, "reason": "rate_limit", "reset_at": reset_at})

    pool = credentials.pool_for("xai", ["XAI_API_KEY"], config)
    first = next(row for row in pool.entries() if row["id"].startswith("sk-sta"))
    assert first["status"] == "exhausted"
    assert first["status_code"] == 429
    assert first["reason"] == "rate_limit"
    assert "sk-stage-v-alpha-0001" not in credentials._state_path().read_text(encoding="utf-8")
    assert "sk-stage-v-beta-0002" in auth.headers()["Authorization"]


def test_oauth_report_auth_refreshes_and_retries_same_provider(monkeypatch, tmp_path):
    from aegis.providers.auth import AuthStore, OAuthAuth, OAuthConfig

    store = AuthStore(tmp_path / "auth.json")
    store.save("test-oauth", {
        "access_token": "old-access",
        "refresh_token": "refresh-token",
        "token_type": "Bearer",
        "expires_at": 4_102_444_800,
        "quarantined": False,
    })
    auth = OAuthAuth(OAuthConfig(
        provider="test-oauth",
        client_id="client",
        authorize_url="https://auth.test/authorize",
        token_url="https://auth.test/token",
    ), store)
    seen_payloads = []

    def fake_post(payload):
        seen_payloads.append(payload)
        return {
            "access_token": "new-access",
            "refresh_token": "refresh-token-2",
            "token_type": "Bearer",
            "expires_in": 3600,
        }

    monkeypatch.setattr(auth, "_post_token", fake_post)

    assert auth.report("auth", {"status_code": 401, "reason": "expired"}) is True
    assert seen_payloads[0]["grant_type"] == "refresh_token"
    assert store.load("test-oauth")["access_token"] == "new-access"
    assert auth.headers()["Authorization"] == "Bearer new-access"


def test_oauth_report_rate_limit_persists_exhausted_without_relogin(tmp_path):
    from aegis.providers.auth import AuthError, AuthStore, OAuthAuth, OAuthConfig

    reset_at = datetime(2035, 1, 1, tzinfo=timezone.utc).timestamp()
    store = AuthStore(tmp_path / "auth.json")
    store.save("test-oauth", {
        "access_token": "oauth-access-secret",
        "refresh_token": "refresh-token",
        "token_type": "Bearer",
        "expires_at": 4_102_444_800,
        "quarantined": False,
    })
    auth = OAuthAuth(OAuthConfig(
        provider="test-oauth",
        client_id="client",
        authorize_url="https://auth.test/authorize",
        token_url="https://auth.test/token",
    ), store)

    assert auth.report(
        "rate_limit",
        {"status_code": 429, "message": "quota exhausted for oauth-access-secret", "reset_at": reset_at},
    ) is False

    saved = store.load("test-oauth")
    assert saved["last_status"] == "exhausted"
    assert saved["last_error_code"] == 429
    assert saved["last_error_reset_at"] == reset_at
    assert "oauth-access-secret" not in saved["last_error_message"]
    assert auth.available() is True
    with pytest.raises(AuthError) as err:
        auth.headers()
    assert err.value.relogin_required is False
    assert err.value.reset_at == reset_at


def test_oauth_terminal_auth_quarantines_local_entry(tmp_path):
    from aegis.providers.auth import AuthStore, OAuthAuth, OAuthConfig

    store = AuthStore(tmp_path / "auth.json")
    store.save("test-oauth", {
        "access_token": "oauth-access-secret",
        "refresh_token": "refresh-token",
        "token_type": "Bearer",
        "expires_at": 4_102_444_800,
        "quarantined": False,
    })
    auth = OAuthAuth(OAuthConfig(
        provider="test-oauth",
        client_id="client",
        authorize_url="https://auth.test/authorize",
        token_url="https://auth.test/token",
    ), store)

    assert auth.report("auth", {"status_code": 401, "error": "invalid_grant"}) is False

    saved = store.load("test-oauth")
    assert saved["last_status"] == "dead"
    assert saved["quarantined"] is True
    assert auth.available() is False


def _write_external_oauth(path, access_token: str, *, refresh_token: str = "refresh-token", expires_at: float | None = None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at": expires_at if expires_at is not None else time.time() + 3600,
        "token_type": "Bearer",
    }), encoding="utf-8")


def _oauth_auth(store, provider: str = "test-oauth"):
    from aegis.providers.auth import OAuthAuth, OAuthConfig

    return OAuthAuth(OAuthConfig(
        provider=provider,
        client_id="client",
        authorize_url="https://auth.test/authorize",
        token_url="https://auth.test/token",
    ), store)


def test_borrowed_oauth_reference_resyncs_external_token_without_persisting_secret(tmp_path):
    from aegis.providers.auth import AuthStore

    external = tmp_path / "external" / "oauth.json"
    _write_external_oauth(external, "old-access")
    store = AuthStore(tmp_path / "auth.json")
    store.save("test-oauth", {
        "source": "external:oauth",
        "external_token_path": str(external),
        "label": "borrowed",
    })
    auth = _oauth_auth(store)

    assert auth.headers()["Authorization"] == "Bearer old-access"
    _write_external_oauth(external, "new-access", refresh_token="refresh-token-2")
    assert auth.headers()["Authorization"] == "Bearer new-access"

    auth_text = (tmp_path / "auth.json").read_text(encoding="utf-8")
    assert "old-access" not in auth_text
    assert "new-access" not in auth_text
    saved = store.load("test-oauth")
    assert saved["source"] == "external:oauth"
    assert saved["reference_only"] is True
    assert saved["secret_fingerprint"].startswith("sha256:")


def test_borrowed_oauth_reference_clears_exhausted_status_when_external_token_rotates(tmp_path):
    from aegis.providers.auth import AuthStore

    external = tmp_path / "external" / "oauth.json"
    _write_external_oauth(external, "old-access")
    store = AuthStore(tmp_path / "auth.json")
    store.save("test-oauth", {
        "source": "external:oauth",
        "external_token_path": str(external),
        "access_token": "old-access",
        "last_status": "exhausted",
        "last_error_code": 429,
        "last_error_reset_at": time.time() + 3600,
    })
    _write_external_oauth(external, "fresh-access", refresh_token="fresh-refresh")

    auth = _oauth_auth(store)
    assert auth.headers()["Authorization"] == "Bearer fresh-access"

    saved = store.load("test-oauth")
    assert saved["reference_only"] is True
    assert "access_token" not in saved
    assert "last_status" not in saved
    assert "last_error_reset_at" not in saved


def test_borrowed_oauth_reference_does_not_refresh_or_write_external_file(monkeypatch, tmp_path):
    from aegis.providers.auth import AuthError, AuthStore

    external = tmp_path / "external" / "oauth.json"
    _write_external_oauth(
        external,
        "expired-access",
        refresh_token="external-refresh",
        expires_at=time.time() - 60,
    )
    before = external.read_text(encoding="utf-8")
    store = AuthStore(tmp_path / "auth.json")
    store.save("test-oauth", {
        "source": "external:oauth",
        "external_token_path": str(external),
    })
    auth = _oauth_auth(store)
    monkeypatch.setattr(auth, "_post_token", lambda payload: pytest.fail("borrowed token was refreshed"))

    with pytest.raises(AuthError) as err:
        auth.headers()

    assert err.value.code == "external_oauth_expired"
    assert err.value.relogin_required is False
    assert external.read_text(encoding="utf-8") == before
    assert "expired-access" not in (tmp_path / "auth.json").read_text(encoding="utf-8")


def test_anthropic_claude_code_reference_preserves_oauth_headers_without_clobbering_file(monkeypatch, tmp_path):
    from aegis.providers.auth import AuthStore, OAuthAuth, OAuthConfig

    home = tmp_path / "home"
    claude_file = home / ".claude" / ".credentials.json"
    claude_file.parent.mkdir(parents=True)
    claude_file.write_text(json.dumps({
        "claudeAiOauth": {
            "accessToken": "claude-access",
            "refreshToken": "claude-refresh",
            "expiresAt": int((time.time() + 3600) * 1000),
            "scopes": ["user:inference"],
        }
    }), encoding="utf-8")
    before = claude_file.read_text(encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    store = AuthStore(tmp_path / "auth.json")
    store.save("anthropic", {"source": "claude_code", "label": "Claude Code"})

    auth = OAuthAuth(OAuthConfig(
        provider="anthropic",
        client_id="client",
        authorize_url="https://claude.ai/oauth/authorize",
        token_url="https://console.anthropic.com/v1/oauth/token",
        api_extra_headers={"anthropic-beta": "oauth-2025-04-20"},
    ), store)

    headers = auth.headers()
    assert headers["Authorization"] == "Bearer claude-access"
    assert headers["anthropic-beta"] == "oauth-2025-04-20"
    assert claude_file.read_text(encoding="utf-8") == before
    auth_text = (tmp_path / "auth.json").read_text(encoding="utf-8")
    assert "claude-access" not in auth_text
    assert "claude-refresh" not in auth_text


def test_openai_codex_cli_reference_resyncs_from_codex_auth_json(monkeypatch, tmp_path):
    from aegis.providers.auth import AuthStore, OAuthAuth, OAuthConfig

    codex_home = tmp_path / "codex"
    codex_auth = codex_home / "auth.json"
    codex_auth.parent.mkdir(parents=True)
    codex_auth.write_text(json.dumps({
        "tokens": {"access_token": "codex-old", "refresh_token": "codex-refresh-old"}
    }), encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    store = AuthStore(tmp_path / "auth.json")
    store.save("openai-codex", {"source": "codex-cli", "label": "Codex CLI"})
    auth = OAuthAuth(OAuthConfig(
        provider="openai-codex",
        client_id="client",
        authorize_url="https://auth.openai.com/oauth/authorize",
        token_url="https://auth.openai.com/oauth/token",
        api_extra_headers={"originator": "codex_cli_rs"},
    ), store)

    assert auth.headers()["Authorization"] == "Bearer codex-old"
    codex_auth.write_text(json.dumps({
        "tokens": {"access_token": "codex-new", "refresh_token": "codex-refresh-new"}
    }), encoding="utf-8")
    headers = auth.headers()

    assert headers["Authorization"] == "Bearer codex-new"
    assert headers["originator"] == "codex_cli_rs"
    aegis_auth = (tmp_path / "auth.json").read_text(encoding="utf-8")
    assert "codex-old" not in aegis_auth
    assert "codex-new" not in aegis_auth


def test_qwen_cli_reference_resyncs_from_qwen_oauth_file_without_persisting_secret(monkeypatch, tmp_path):
    from aegis.providers.auth import AuthStore

    home = tmp_path / "home"
    qwen_auth = home / ".qwen" / "oauth_creds.json"
    qwen_auth.parent.mkdir(parents=True)
    qwen_auth.write_text(json.dumps({
        "access_token": "qwen-old",
        "refresh_token": "qwen-refresh-old",
        "expiry_date": int((time.time() + 3600) * 1000),
    }), encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))

    store = AuthStore(tmp_path / "auth.json")
    store.save("qwen-oauth", {"source": "qwen-cli", "label": "Qwen CLI"})
    auth = _oauth_auth(store, provider="qwen-oauth")

    assert auth.headers()["Authorization"] == "Bearer qwen-old"
    qwen_auth.write_text(json.dumps({
        "access_token": "qwen-new",
        "refresh_token": "qwen-refresh-new",
        "expiry_date": int((time.time() + 3600) * 1000),
    }), encoding="utf-8")

    assert auth.headers()["Authorization"] == "Bearer qwen-new"
    auth_text = store.path.read_text(encoding="utf-8")
    assert "qwen-old" not in auth_text
    assert "qwen-new" not in auth_text
    saved = store.load("qwen-oauth")
    assert saved["source"] == "qwen-cli"
    assert saved["reference_only"] is True
    assert saved["secret_fingerprint"].startswith("sha256:")

    before_delete = qwen_auth.read_text(encoding="utf-8")
    result = store.delete("qwen-oauth")

    assert result.removed is True
    assert result.suppressed_sources == ["qwen-cli"]
    assert any("Qwen CLI credentials remain" in hint for hint in result.hints)
    assert qwen_auth.read_text(encoding="utf-8") == before_delete
    payload = json.loads(store.path.read_text(encoding="utf-8"))
    assert "qwen-oauth" not in payload
    assert "qwen-cli" in payload["suppressed_sources"]["qwen-oauth"]


def test_auth_store_delete_removes_aegis_references_without_clobbering_codex_cli(monkeypatch, tmp_path):
    from aegis.providers.auth import AuthStore

    codex_home = tmp_path / "codex"
    codex_auth = codex_home / "auth.json"
    codex_auth.parent.mkdir(parents=True)
    codex_auth.write_text(json.dumps({
        "tokens": {"access_token": "codex-external", "refresh_token": "codex-refresh"}
    }), encoding="utf-8")
    before = codex_auth.read_text(encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    store = AuthStore(tmp_path / "auth.json")
    store.path.write_text(json.dumps({
        "openai-codex": {"source": "codex-cli", "label": "Codex CLI"},
        "credential_pool": {
            "openai-codex": [
                {"id": "borrowed", "source": "codex-cli", "label": "Codex CLI"},
                {"id": "manual", "source": "manual:device_code", "access_token": "pool-owned"},
            ]
        },
    }), encoding="utf-8")

    result = store.delete("openai-codex")

    assert result.removed is True
    assert result.removed_direct is True
    assert result.removed_pool_entries == 2
    assert set(result.suppressed_sources) == {"codex-cli", "manual:device_code", "device_code"}
    assert any("Codex CLI credentials remain" in hint for hint in result.hints)
    assert codex_auth.read_text(encoding="utf-8") == before
    payload = json.loads(store.path.read_text(encoding="utf-8"))
    assert "openai-codex" not in payload
    assert "openai-codex" not in payload.get("credential_pool", {})
    assert "codex-cli" in payload["suppressed_sources"]["openai-codex"]
    assert "device_code" in payload["suppressed_sources"]["openai-codex"]
    assert "manual:device_code" in payload["suppressed_sources"]["openai-codex"]
    assert "pool-owned" not in store.path.read_text(encoding="utf-8")

    auth = _oauth_auth(store, provider="openai-codex")
    assert auth.available() is False


def test_auth_store_delete_clears_nested_provider_singleton_and_suppresses_source(tmp_path):
    from aegis.providers.auth import AuthStore

    store = AuthStore(tmp_path / "auth.json")
    store.path.write_text(json.dumps({
        "providers": {
            "xai-oauth": {
                "tokens": {"access_token": "xai-singleton", "refresh_token": "xai-refresh"},
            },
        },
        "credential_pool": {
            "xai-oauth": [
                {"id": "xai", "source": "loopback_pkce", "access_token": "xai-pool"},
            ],
        },
    }), encoding="utf-8")

    result = store.delete("xai-oauth")

    assert result.removed is True
    assert result.removed_direct is True
    assert result.removed_pool_entries == 1
    assert result.suppressed_sources == ["loopback_pkce"]
    payload = json.loads(store.path.read_text(encoding="utf-8"))
    assert "providers" not in payload
    assert "xai-oauth" not in payload.get("credential_pool", {})
    assert "loopback_pkce" in payload["suppressed_sources"]["xai-oauth"]
    auth_text = store.path.read_text(encoding="utf-8")
    assert "xai-singleton" not in auth_text
    assert "xai-refresh" not in auth_text
    assert "xai-pool" not in auth_text


@pytest.mark.parametrize(
    ("provider", "creds", "expected_sources"),
    [
        (
            "nous",
            {"access_token": "nous-secret", "refresh_token": "nous-refresh"},
            {"device_code"},
        ),
        (
            "minimax-oauth",
            {"tokens": {"access_token": "minimax-secret", "refresh_token": "minimax-refresh"}},
            {"oauth"},
        ),
        (
            "openai-codex",
            {"source": "manual:device_code", "access_token": "codex-secret"},
            {"manual:device_code", "device_code"},
        ),
        (
            "xai-oauth",
            {"source": "manual:xai_pkce", "access_token": "xai-secret"},
            {"manual:xai_pkce", "loopback_pkce"},
        ),
    ],
)
def test_auth_store_delete_suppresses_provider_specific_singleton_sources(
    tmp_path,
    provider,
    creds,
    expected_sources,
):
    from aegis.providers.auth import AuthStore

    store = AuthStore(tmp_path / "auth.json")
    store.path.write_text(json.dumps({provider: creds}), encoding="utf-8")

    result = store.delete(provider)

    assert result.removed is True
    assert set(result.suppressed_sources) == expected_sources
    payload = json.loads(store.path.read_text(encoding="utf-8"))
    assert set(payload["suppressed_sources"][provider]) == expected_sources
    assert "secret" not in store.path.read_text(encoding="utf-8")


def test_provider_specific_singleton_suppression_blocks_source_less_reseed(tmp_path):
    from aegis.providers.auth import AuthStore

    store = AuthStore(tmp_path / "auth.json")
    store.path.write_text(json.dumps({
        "xai-oauth": {"access_token": "xai-reseeded", "refresh_token": "xai-refresh"},
        "suppressed_sources": {"xai-oauth": {"loopback_pkce": {"reason": "removed"}}},
    }), encoding="utf-8")

    assert store.load("xai-oauth") is None
    assert _oauth_auth(store, provider="xai-oauth").available() is False


def test_auth_store_delete_uses_source_removal_registry_for_external_env_and_config(monkeypatch, tmp_path):
    from aegis.providers.auth import AuthStore, auth_source_removal_registry

    registry = auth_source_removal_registry()
    provider_steps = {(row["provider"], row["source_id"]): row for row in registry}
    assert ("nous", "device_code") in provider_steps
    assert ("openai-codex", "device_code") in provider_steps
    assert ("xai-oauth", "loopback_pkce") in provider_steps
    assert ("qwen-oauth", "qwen-cli") in provider_steps
    assert ("minimax-oauth", "oauth") in provider_steps
    assert any(row["source_id"] == "env:" for row in registry)
    assert any(row["source_id"] == "config:" for row in registry)
    assert any(row["source_id"] == "external" for row in registry)
    assert any(row["source_id"] == "manual" and row["suppress"] is False for row in registry)

    external = tmp_path / "external" / "oauth.json"
    external.parent.mkdir(parents=True)
    external.write_text(json.dumps({"access_token": "external-secret"}), encoding="utf-8")
    monkeypatch.setenv("TEST_OAUTH_TOKEN", "env-secret")

    store = AuthStore(tmp_path / "auth.json")
    store.path.write_text(
        json.dumps(
            {
                "credential_pool": {
                    "test-oauth": [
                        {
                            "id": "external",
                            "source": "external:oauth",
                            "external_token_path": str(external),
                            "access_token": "external-secret",
                        },
                        {
                            "id": "env",
                            "source": "env:TEST_OAUTH_TOKEN",
                            "access_token": "env-secret",
                        },
                        {
                            "id": "config",
                            "source": "config:custom.provider.api_key",
                            "access_token": "config-secret",
                        },
                        {
                            "id": "manual",
                            "source": "manual:device_code",
                            "access_token": "manual-secret",
                        },
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    result = store.delete("test-oauth")

    assert result.removed is True
    assert result.removed_pool_entries == 4
    assert set(result.suppressed_sources) == {
        "external:oauth",
        "env:TEST_OAUTH_TOKEN",
        "config:custom.provider.api_key",
    }
    assert all("manual" not in source for source in result.suppressed_sources)
    assert any("external file" in hint for hint in result.hints)
    assert any("TEST_OAUTH_TOKEN is still set" in hint for hint in result.hints)
    assert any("underlying config value is unchanged" in hint for hint in result.hints)

    payload = json.loads(store.path.read_text(encoding="utf-8"))
    assert set(payload["suppressed_sources"]["test-oauth"]) == set(result.suppressed_sources)
    auth_text = store.path.read_text(encoding="utf-8")
    assert "external-secret" not in auth_text
    assert "env-secret" not in auth_text
    assert "config-secret" not in auth_text
    assert "manual-secret" not in auth_text


def test_auth_store_save_clears_borrowed_source_suppression(monkeypatch, tmp_path):
    from aegis.providers.auth import AuthStore

    codex_home = tmp_path / "codex"
    codex_auth = codex_home / "auth.json"
    codex_auth.parent.mkdir(parents=True)
    codex_auth.write_text(json.dumps({
        "tokens": {"access_token": "codex-external", "refresh_token": "codex-refresh"}
    }), encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    store = AuthStore(tmp_path / "auth.json")
    store.path.write_text(json.dumps({
        "suppressed_sources": {"openai-codex": {"codex-cli": {"reason": "removed"}}}
    }), encoding="utf-8")

    store.save("openai-codex", {"source": "codex-cli", "label": "Codex CLI"})

    payload = json.loads(store.path.read_text(encoding="utf-8"))
    assert "openai-codex" not in payload.get("suppressed_sources", {})
    assert _oauth_auth(store, provider="openai-codex").headers()["Authorization"] == "Bearer codex-external"


def test_suppressed_borrowed_pool_entry_is_not_selected(monkeypatch, tmp_path):
    from aegis.providers.auth import AuthStore

    codex_home = tmp_path / "codex"
    codex_auth = codex_home / "auth.json"
    codex_auth.parent.mkdir(parents=True)
    codex_auth.write_text(json.dumps({
        "tokens": {"access_token": "codex-external", "refresh_token": "codex-refresh"}
    }), encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    store = AuthStore(tmp_path / "auth.json")
    store.path.write_text(json.dumps({
        "credential_pool": {
            "openai-codex": [
                {"id": "borrowed", "source": "codex-cli", "label": "Codex CLI"}
            ]
        },
        "suppressed_sources": {"openai-codex": {"codex-cli": {"reason": "removed"}}},
    }), encoding="utf-8")

    auth = _oauth_auth(store, provider="openai-codex")
    assert auth.available() is False
    assert "not logged in" in auth.describe()
