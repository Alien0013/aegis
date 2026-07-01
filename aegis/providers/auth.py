"""Authentication strategies: API key and OAuth 2.0 (PKCE) — both first-class.

Token storage lives in ``~/.aegis/auth.json`` (chmod 0600). OAuth supports two
acquisition flows:

* **localhost callback** (automatic) — spins up an ephemeral local web server and
  captures the ``?code=`` redirect. Works when the OAuth client allows a localhost
  redirect URI.
* **manual paste** — opens the browser, the user copies the authorization code and
  pastes it back. Works for clients that only allow a fixed console redirect
  (e.g. the Claude / Anthropic public client).

OAuth client configuration is data (``OAuthConfig``) and fully overridable per
provider, so you can wire any IdP without touching code.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import os
import secrets
import shutil
import subprocess
import threading
import time
import urllib.parse
import webbrowser
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import httpx

from .. import config as cfg
from ..util import atomic_write, read_text


# --------------------------------------------------------------------------- #
# What a transport needs to authenticate one request.
# --------------------------------------------------------------------------- #
@dataclass
class AuthHeaders:
    headers: dict[str, str] = field(default_factory=dict)


class AuthProvider(ABC):
    @abstractmethod
    def headers(self) -> dict[str, str]:
        """Return request headers (may transparently refresh tokens)."""

    @abstractmethod
    def available(self) -> bool:
        """True if this strategy currently has usable credentials."""

    @abstractmethod
    def describe(self) -> str: ...


@dataclass
class AuthRemovalResult:
    provider: str
    removed: bool = False
    removed_direct: bool = False
    removed_pool_entries: int = 0
    suppressed_sources: list[str] = field(default_factory=list)
    hints: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# API key
# --------------------------------------------------------------------------- #
class ApiKeyAuth(AuthProvider):
    """Resolves a key from the environment (.env is loaded into os.environ)."""

    def __init__(self, env_vars: list[str], scheme: str = "bearer", extra: dict[str, str] | None = None,
                 *, provider_name: str | None = None, config=None):
        # scheme: "bearer" -> Authorization: Bearer; "anthropic" -> x-api-key; "none" -> no auth
        self.env_vars = env_vars
        self.scheme = scheme
        self.extra = extra or {}
        self._idx = 0  # fallback cursor (used only when no shared pool is available)
        self.provider_name = provider_name
        self.config = config

    def _credential_pool(self):
        """The shared, state-persisting CredentialPool for this provider (strategies + billing
        cooldowns + subagent sharing), or None to fall back to the simple env comma-split."""
        if not self.provider_name:
            return None
        try:
            from ..credentials import pool_for
            return pool_for(self.provider_name, self.env_vars, self.config)
        except Exception:  # noqa: BLE001
            return None

    def _pool(self) -> list[str]:
        """Simple fallback pool: the first present env var, split on commas."""
        for var in self.env_vars:
            v = os.environ.get(var)
            if v:
                return [k.strip() for k in v.split(",") if k.strip()]
        return []

    def _key(self) -> str | None:
        pool = self._credential_pool()
        if pool is not None:
            return pool.current()
        simple = self._pool()
        return simple[self._idx % len(simple)] if simple else None

    def rotate(self) -> bool:
        """Advance to the next key in the pool (called on 429/401). True if rotated."""
        pool = self._credential_pool()
        if pool is not None:
            return pool.rotate()
        simple = self._pool()
        if len(simple) <= 1:
            return False
        self._idx = (self._idx + 1) % len(simple)
        return True

    def report(self, kind: str, error_context=None) -> bool:
        """Apply credential-pool failure policy for a classified error kind
        (billing -> cooldown+rotate; rate_limit/auth -> rotate).

        Returns True when a different credential is now active and the caller can
        safely retry the same provider once before escalating to a fallback.
        """
        pool = self._credential_pool()
        if pool is not None:
            return bool(pool.report(kind, error_context=error_context))
        elif kind in ("billing", "rate_limit", "auth"):
            return self.rotate()
        return False

    def acquire_lease(self) -> str | None:
        """Reserve a soft credential slot for callers that can release it."""
        pool = self._credential_pool()
        if pool is not None:
            return pool.acquire_lease()
        return self._key()

    def release_lease(self, key: str | None) -> None:
        pool = self._credential_pool()
        if pool is not None:
            pool.release_lease(key)

    def available(self) -> bool:
        return self.scheme == "none" or self._key() is not None

    def headers(self) -> dict[str, str]:
        h = dict(self.extra)
        if self.scheme == "none":
            return h
        key = self._key()
        if not key:
            raise AuthError(
                f"No API key found. Set one of: {', '.join(self.env_vars)} "
                f"(e.g. `aegis config set {self.env_vars[0]} <key>`)."
            )
        if self.scheme == "anthropic":
            h["x-api-key"] = key
        else:
            h["Authorization"] = f"Bearer {key}"
        pool = self._credential_pool()
        if pool is not None:
            try:
                pool.record_use(key)
            except Exception:  # noqa: BLE001
                pass
        return h

    def describe(self) -> str:
        if self.scheme == "none":
            return "no-auth (local)"
        return f"api-key ({'set' if self._key() else 'MISSING'}: {self.env_vars[0]})"


class CodexCliAuth(AuthProvider):
    """Authentication owned by the local Codex CLI.

    Codex app-server reads the same cached login as the Codex CLI/IDE
    extension, usually ``~/.codex/auth.json`` or the OS credential store. AEGIS
    therefore does not inject bearer headers here.
    """

    def __init__(self, command: str = "codex"):
        self.command = command

    def headers(self) -> dict[str, str]:
        return {}

    def available(self) -> bool:
        if shutil.which(self.command) is None:
            return False
        try:
            proc = subprocess.run(
                [self.command, "login", "status"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except Exception:
            return False
        status = (proc.stdout + "\n" + proc.stderr).lower()
        return proc.returncode == 0 and "logged in" in status

    def describe(self) -> str:
        if shutil.which(self.command) is None:
            return "codex-cli (missing; install @openai/codex)"
        if self.available():
            return "codex-cli (ChatGPT login ready)"
        return "codex-cli (not logged in; run `codex login`)"


# Codex's Cloudflare front whitelists a few first-party originators; a request
# without an allowed `originator` is served 403. Pin the codex-rs CLI identity.
CODEX_ORIGINATOR_HEADERS = {
    "originator": "codex_cli_rs",
    "User-Agent": "codex_cli_rs/0.0.0 (AEGIS)",
}
CODEX_RATE_LIMITED_CODE = "codex_rate_limited"
_TERMINAL_OAUTH_REASONS = {
    "token_invalidated",
    "token_revoked",
    "invalid_token",
    "invalid_grant",
    "unauthorized_client",
    "refresh_token_reused",
}
_BORROWED_REFERENCE_SOURCES = {
    "borrowed",
    "external",
    "external:oauth",
    "file",
    "file:oauth",
    "reference",
    "reference-only",
    "reference_only",
}
_CLAUDE_CODE_SOURCES = {
    "claude_code",
    "claude-code",
    "claude_cli",
    "claude-cli",
}
_CODEX_CLI_SOURCES = {
    "codex_cli",
    "codex-cli",
    "codex_auth_json",
    "codex-auth-json",
}
_CODEX_DEVICE_CODE_SOURCES = {"device_code", "manual:device_code"}
_NOUS_DEVICE_CODE_SOURCES = {"device_code", "manual:device_code"}
_XAI_OAUTH_SOURCES = {"loopback_pkce", "loopback-pkce", "xai_pkce", "manual:xai_pkce"}
_QWEN_CLI_SOURCES = {"qwen-cli", "qwen_cli"}
_MINIMAX_OAUTH_SOURCES = {"oauth", "minimax_oauth", "manual:minimax_oauth"}
_SECRET_AUTH_FIELDS = {
    "access_token",
    "accessToken",
    "refresh_token",
    "refreshToken",
    "id_token",
    "idToken",
    "api_key",
    "apiKey",
    "agent_key",
    "agentKey",
    "authorization",
    "bearer_token",
    "token",
    "tokens",
}
_OAUTH_STATUS_FIELDS = {
    "last_status",
    "last_error_reason",
    "last_error_message",
    "last_error_code",
    "last_error_reset_at",
    "status",
    "error_reason",
    "error",
    "error_code",
    "quarantined",
}
_AUTH_RESERVED_TOP_LEVEL_KEYS = {"credential_pool", "providers", "suppressed_sources"}


def _codex_auth_path() -> Path:
    return Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex")) / "auth.json"


def _qwen_cli_auth_path() -> Path:
    return Path.home() / ".qwen" / "oauth_creds.json"


def _secret_fingerprint(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    if not text:
        return None
    digest = hashlib.sha256(text.encode("utf-8", errors="surrogatepass")).hexdigest()
    return f"sha256:{digest[:16]}"


def _credential_fingerprint(creds: dict) -> str | None:
    for key in ("access_token", "accessToken", "refresh_token", "refreshToken", "api_key", "token"):
        fp = _secret_fingerprint(creds.get(key))
        if fp:
            return fp
    existing = creds.get("secret_fingerprint")
    if isinstance(existing, str) and existing.startswith("sha256:"):
        return existing
    return None


def _normalize_source(value: object) -> str:
    return str(value or "").strip().lower()


def _external_reference_path(creds: dict) -> Path | None:
    for key in ("external_token_path", "token_path", "auth_file", "external_file", "path"):
        raw = creds.get(key)
        if raw:
            return Path(os.path.expandvars(str(raw))).expanduser()
    source = str(creds.get("source") or "").strip()
    for prefix in ("file:", "path:"):
        if source.startswith(prefix):
            return Path(os.path.expandvars(source[len(prefix):])).expanduser()
    return None


def _credential_source(creds: dict | None) -> str:
    if not isinstance(creds, dict):
        return ""
    return str(creds.get("source") or creds.get("secret_source") or "").strip()


def _is_borrowed_oauth_reference(provider: str, creds: dict | None) -> bool:
    if not isinstance(creds, dict):
        return False
    source = _normalize_source(_credential_source(creds))
    if creds.get("borrowed") is True or creds.get("reference_only") is True or creds.get("external") is True:
        return True
    if _external_reference_path(creds) is not None:
        return True
    if source in _BORROWED_REFERENCE_SOURCES or source.startswith(("external:", "reference:", "borrowed:")):
        return True
    if provider == "anthropic" and source in _CLAUDE_CODE_SOURCES:
        return True
    if provider == "openai-codex" and source in _CODEX_CLI_SOURCES:
        return True
    if provider == "qwen-oauth" and source in _QWEN_CLI_SOURCES:
        return True
    return False


def _auth_suppressed_sources(payload: dict, provider: str) -> dict[str, dict]:
    suppressed = payload.get("suppressed_sources")
    if not isinstance(suppressed, dict):
        return {}
    raw = suppressed.get(provider)
    if isinstance(raw, list):
        return {str(source): {} for source in raw}
    if not isinstance(raw, dict):
        return {}
    return {
        str(source): dict(meta) if isinstance(meta, dict) else {}
        for source, meta in raw.items()
    }


def _is_auth_source_suppressed(payload: dict, provider: str, source: object) -> bool:
    source_text = str(source or "").strip()
    return bool(source_text and source_text in _auth_suppressed_sources(payload, provider))


def _auth_sources_for_suppression(provider: str, creds: dict | None) -> list[str]:
    if not isinstance(creds, dict):
        return []
    sources: list[str] = []

    def add(source: object) -> None:
        source_text = str(source or "").strip()
        if source_text and source_text not in sources:
            sources.append(source_text)

    add(_credential_source(creds))
    step = _find_auth_source_removal_step(provider, creds)
    if step is not None and step.suppress:
        for source in step.sources_to_suppress(provider, creds):
            add(source)
    return sources


def _is_auth_credential_suppressed(payload: dict, provider: str, creds: dict | None) -> bool:
    return any(
        _is_auth_source_suppressed(payload, provider, source)
        for source in _auth_sources_for_suppression(provider, creds)
    )


def _suppress_auth_source(payload: dict, provider: str, source: str, *, reason: str) -> bool:
    source = str(source or "").strip()
    if not source:
        return False
    suppressed = payload.setdefault("suppressed_sources", {})
    if not isinstance(suppressed, dict):
        suppressed = {}
        payload["suppressed_sources"] = suppressed
    provider_sources = suppressed.setdefault(provider, {})
    if isinstance(provider_sources, list):
        provider_sources = {str(item): {} for item in provider_sources}
        suppressed[provider] = provider_sources
    if not isinstance(provider_sources, dict):
        provider_sources = {}
        suppressed[provider] = provider_sources
    if source in provider_sources:
        return False
    provider_sources[source] = {"suppressed_at": time.time(), "reason": reason}
    return True


def _unsuppress_auth_provider(payload: dict, provider: str) -> bool:
    suppressed = payload.get("suppressed_sources")
    if not isinstance(suppressed, dict) or provider not in suppressed:
        return False
    suppressed.pop(provider, None)
    if not suppressed:
        payload.pop("suppressed_sources", None)
    return True


def _borrowed_reference_label(provider: str, creds: dict | None) -> str:
    source = _normalize_source(_credential_source(creds))
    if provider == "anthropic" and source in _CLAUDE_CODE_SOURCES:
        return "Claude Code"
    if provider == "openai-codex" and source in _CODEX_CLI_SOURCES:
        return "Codex CLI"
    if provider == "qwen-oauth" and source in _QWEN_CLI_SOURCES:
        return "Qwen CLI"
    path = _external_reference_path(creds or {})
    if path is not None:
        return f"external file {path}"
    return "external credential source"


def _borrowed_refresh_hint(provider: str, creds: dict | None) -> str:
    label = _borrowed_reference_label(provider, creds)
    if label == "Claude Code":
        return "Refresh it with Claude Code, then rerun `aegis auth import-claude` if needed."
    if label == "Codex CLI":
        return "Refresh it with `codex login`, then retry or rerun `aegis auth login openai-codex`."
    if label == "Qwen CLI":
        return "Refresh it with the Qwen CLI, then retry or rerun `aegis auth login qwen-oauth`."
    return f"Refresh it in {label}, then retry."


def _borrowed_removal_hints(provider: str, creds: dict) -> list[str]:
    label = _borrowed_reference_label(provider, creds)
    path = _external_reference_path(creds)
    if label == "Claude Code":
        path_text = str(path) if path is not None else "~/.claude/.credentials.json"
        return [
            "Suppressed Claude Code OAuth reference; AEGIS will not reuse it until you re-import/login.",
            f"Claude Code credentials remain at {path_text}.",
        ]
    if label == "Codex CLI":
        path_text = str(path) if path is not None else str(_codex_auth_path())
        return [
            "Suppressed Codex CLI OAuth reference; AEGIS will not reuse it until you login again.",
            f"Codex CLI credentials remain at {path_text}.",
        ]
    if label == "Qwen CLI":
        path_text = str(path) if path is not None else str(_qwen_cli_auth_path())
        return [
            "Suppressed Qwen CLI OAuth reference; AEGIS will not reuse it until you login again.",
            f"Qwen CLI credentials remain at {path_text}.",
        ]
    return [
        f"Suppressed {label} reference; AEGIS will not reuse it until you login again.",
    ]


@dataclass(frozen=True)
class AuthSourceRemovalStep:
    provider: str
    source_id: str
    description: str
    suppress: bool = True
    match_fn: Callable[[str, str, dict], bool] | None = None
    hint_fn: Callable[[str, dict], list[str]] | None = None
    extra_sources_fn: Callable[[str, dict], list[str]] | None = None

    def matches(self, provider: str, creds: dict) -> bool:
        source = _normalize_source(_credential_source(creds))
        if self.provider != "*" and self.provider != provider:
            return False
        if self.match_fn is not None:
            return self.match_fn(provider, source, creds)
        return source == self.source_id

    def hints(self, provider: str, creds: dict) -> list[str]:
        if self.hint_fn is None:
            return []
        return self.hint_fn(provider, creds)

    def sources_to_suppress(self, provider: str, creds: dict) -> list[str]:
        source = _credential_source(creds)
        out = [source] if source else []
        if self.extra_sources_fn is not None:
            out.extend(self.extra_sources_fn(provider, creds))
        return list(dict.fromkeys(str(item).strip() for item in out if str(item).strip()))


def _env_source_hints(provider: str, creds: dict) -> list[str]:
    source = _credential_source(creds)
    env_name = source.split(":", 1)[1] if ":" in source else source
    hints = [
        f"Suppressed {source}; AEGIS will ignore it until you add/login again.",
    ]
    if env_name and os.environ.get(env_name):
        hints.append(
            f"{env_name} is still set in the process environment; unset it there if you want it gone outside AEGIS."
        )
    return hints


def _config_source_hints(_provider: str, creds: dict) -> list[str]:
    source = _credential_source(creds) or "config source"
    return [
        f"Suppressed {source}; AEGIS will ignore it until you add/login again.",
        "The underlying config value is unchanged; edit config.yaml if you want to remove it from disk.",
    ]


def _manual_source_hints(_provider: str, _creds: dict) -> list[str]:
    return []


def _auth_source_external_match(provider: str, _source: str, creds: dict) -> bool:
    return _is_borrowed_oauth_reference(provider, creds)


def _auth_source_manual_match(_provider: str, source: str, _creds: dict) -> bool:
    return source == "manual" or source.startswith("manual:")


def _auth_source_env_match(_provider: str, source: str, _creds: dict) -> bool:
    return source.startswith("env:")


def _auth_source_config_match(_provider: str, source: str, _creds: dict) -> bool:
    return source.startswith("config:") or source == "model_config"


def _auth_source_has_oauth_material(creds: dict) -> bool:
    tokens = creds.get("tokens")
    token_payload = tokens if isinstance(tokens, dict) else {}
    secret_keys = (
        "access_token",
        "accessToken",
        "refresh_token",
        "refreshToken",
        "api_key",
        "apiKey",
        "agent_key",
    )
    for payload in (creds, token_payload):
        for key in secret_keys:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return True
    return False


def _auth_source_provider_singleton_match(aliases: set[str]) -> Callable[[str, str, dict], bool]:
    normalized = {_normalize_source(alias) for alias in aliases}

    def _match(_provider: str, source: str, creds: dict) -> bool:
        return source in normalized or (not source and _auth_source_has_oauth_material(creds))

    return _match


def _auth_source_extra(source: str) -> Callable[[str, dict], list[str]]:
    def _extra(_provider: str, _creds: dict) -> list[str]:
        return [source]

    return _extra


def _provider_singleton_hints(provider: str, creds: dict) -> list[str]:
    source = _credential_source(creds) or {
        "nous": "device_code",
        "openai-codex": "device_code",
        "xai-oauth": "loopback_pkce",
        "qwen-oauth": "qwen-cli",
        "minimax-oauth": "oauth",
    }.get(provider, "oauth")
    return [
        f"Suppressed {provider} {source} source; AEGIS will ignore it until you add/login again.",
    ]


_AUTH_SOURCE_REMOVAL_STEPS: tuple[AuthSourceRemovalStep, ...] = (
    AuthSourceRemovalStep(
        provider="nous",
        source_id="device_code",
        description="Nous device-code OAuth singleton",
        match_fn=_auth_source_provider_singleton_match(_NOUS_DEVICE_CODE_SOURCES),
        hint_fn=_provider_singleton_hints,
        extra_sources_fn=_auth_source_extra("device_code"),
    ),
    AuthSourceRemovalStep(
        provider="openai-codex",
        source_id="device_code",
        description="OpenAI Codex device-code OAuth singleton",
        match_fn=_auth_source_provider_singleton_match(_CODEX_DEVICE_CODE_SOURCES),
        hint_fn=_provider_singleton_hints,
        extra_sources_fn=_auth_source_extra("device_code"),
    ),
    AuthSourceRemovalStep(
        provider="xai-oauth",
        source_id="loopback_pkce",
        description="xAI OAuth loopback PKCE singleton",
        match_fn=_auth_source_provider_singleton_match(_XAI_OAUTH_SOURCES),
        hint_fn=_provider_singleton_hints,
        extra_sources_fn=_auth_source_extra("loopback_pkce"),
    ),
    AuthSourceRemovalStep(
        provider="qwen-oauth",
        source_id="qwen-cli",
        description="Qwen CLI OAuth credential reference",
        match_fn=_auth_source_provider_singleton_match(_QWEN_CLI_SOURCES),
        hint_fn=_borrowed_removal_hints,
        extra_sources_fn=_auth_source_extra("qwen-cli"),
    ),
    AuthSourceRemovalStep(
        provider="minimax-oauth",
        source_id="oauth",
        description="MiniMax OAuth singleton",
        match_fn=_auth_source_provider_singleton_match(_MINIMAX_OAUTH_SOURCES),
        hint_fn=_provider_singleton_hints,
        extra_sources_fn=_auth_source_extra("oauth"),
    ),
    AuthSourceRemovalStep(
        provider="openai-codex",
        source_id="codex-cli",
        description="Codex CLI auth.json reference",
        hint_fn=_borrowed_removal_hints,
    ),
    AuthSourceRemovalStep(
        provider="anthropic",
        source_id="claude_code",
        description="Claude Code credential reference",
        match_fn=lambda provider, source, creds: source in _CLAUDE_CODE_SOURCES,
        hint_fn=_borrowed_removal_hints,
    ),
    AuthSourceRemovalStep(
        provider="*",
        source_id="env:",
        description="Environment-sourced credential reference",
        match_fn=_auth_source_env_match,
        hint_fn=_env_source_hints,
    ),
    AuthSourceRemovalStep(
        provider="*",
        source_id="config:",
        description="Config-sourced credential reference",
        match_fn=_auth_source_config_match,
        hint_fn=_config_source_hints,
    ),
    AuthSourceRemovalStep(
        provider="*",
        source_id="external",
        description="Borrowed/external OAuth credential reference",
        match_fn=_auth_source_external_match,
        hint_fn=_borrowed_removal_hints,
    ),
    AuthSourceRemovalStep(
        provider="*",
        source_id="manual",
        description="AEGIS-owned manual credential",
        suppress=False,
        match_fn=_auth_source_manual_match,
        hint_fn=_manual_source_hints,
    ),
)


def _find_auth_source_removal_step(provider: str, creds: dict) -> AuthSourceRemovalStep | None:
    for step in _AUTH_SOURCE_REMOVAL_STEPS:
        if step.matches(provider, creds):
            return step
    return None


def auth_source_removal_registry() -> list[dict[str, str | bool]]:
    """Return a stable, non-secret description of registered removal handlers."""
    return [
        {
            "provider": step.provider,
            "source_id": step.source_id,
            "description": step.description,
            "suppress": step.suppress,
        }
        for step in _AUTH_SOURCE_REMOVAL_STEPS
    ]


def _append_unique(items: list[str], value: str) -> None:
    if value and value not in items:
        items.append(value)


def _sanitize_auth_creds_for_disk(provider: str, creds: dict) -> dict:
    clean = {k: v for k, v in dict(creds).items() if not str(k).startswith("_")}
    if not _is_borrowed_oauth_reference(provider, clean):
        return clean
    fingerprint = _credential_fingerprint(clean)
    for key in _SECRET_AUTH_FIELDS:
        clean.pop(key, None)
    if fingerprint:
        clean["secret_fingerprint"] = fingerprint
    clean["reference_only"] = True
    return clean


def _sanitize_auth_payload_for_disk(payload: dict) -> dict:
    clean = dict(payload)
    pool = clean.get("credential_pool")
    if isinstance(pool, dict):
        clean_pool: dict = {}
        for provider, entries in pool.items():
            if isinstance(entries, list):
                clean_pool[provider] = [
                    _sanitize_auth_creds_for_disk(str(provider), entry)
                    if isinstance(entry, dict) else entry
                    for entry in entries
                ]
            else:
                clean_pool[provider] = entries
        clean["credential_pool"] = clean_pool
    for provider, creds in list(clean.items()):
        if provider in _AUTH_RESERVED_TOP_LEVEL_KEYS or not isinstance(creds, dict):
            continue
        clean[provider] = _sanitize_auth_creds_for_disk(str(provider), creds)
    return clean


def _write_auth_payload(path: Path, payload: dict) -> None:
    atomic_write(path, json.dumps(_sanitize_auth_payload_for_disk(payload), indent=2))
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _read_json_file(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    return data if isinstance(data, dict) else None


def _coerce_epoch_seconds(value: object) -> float | None:
    parsed = _parse_reset_at(value)
    return parsed if parsed and parsed > 0 else None


def _oauth_creds_from_json_payload(payload: dict, *, source: str, auth_file: Path) -> dict | None:
    body = payload.get("claudeAiOauth")
    if isinstance(body, dict):
        source = "claude_code"
    else:
        tokens = payload.get("tokens")
        body = tokens if isinstance(tokens, dict) else payload
    access = body.get("access_token") or body.get("accessToken") or body.get("api_key")
    if not access:
        return None
    expires_at = (
        body.get("expires_at")
        or body.get("expiresAt")
        or body.get("expiry_date")
        or body.get("expiryDate")
    )
    creds = {
        "access_token": access,
        "refresh_token": body.get("refresh_token") or body.get("refreshToken"),
        "token_type": body.get("token_type") or body.get("tokenType") or "Bearer",
        "expires_at": _coerce_epoch_seconds(expires_at),
        "scope": body.get("scope") or body.get("scopes"),
        "source": source,
        "external_token_path": str(auth_file),
        "reference_only": True,
    }
    return {k: v for k, v in creds.items() if v not in (None, "")}


def _claude_code_auth_paths(creds: dict) -> list[Path]:
    explicit = _external_reference_path(creds)
    if explicit is not None:
        return [explicit]
    return [
        Path.home() / ".claude" / ".credentials.json",
        Path.home() / ".config" / "claude" / ".credentials.json",
    ]


def _external_oauth_tokens(provider: str, creds: dict) -> dict | None:
    source = _normalize_source(creds.get("source"))
    if provider == "anthropic" and source in _CLAUDE_CODE_SOURCES:
        for path in _claude_code_auth_paths(creds):
            payload = _read_json_file(path)
            if payload:
                resolved = _oauth_creds_from_json_payload(payload, source="claude_code", auth_file=path)
                if resolved:
                    return resolved
        return None
    if provider == "openai-codex" and source in _CODEX_CLI_SOURCES:
        path = _external_reference_path(creds) or _codex_auth_path()
        payload = _read_json_file(path)
        return _oauth_creds_from_json_payload(payload, source="codex-cli", auth_file=path) if payload else None
    if provider == "qwen-oauth" and source in _QWEN_CLI_SOURCES:
        path = _external_reference_path(creds) or _qwen_cli_auth_path()
        payload = _read_json_file(path)
        return _oauth_creds_from_json_payload(payload, source="qwen-cli", auth_file=path) if payload else None
    path = _external_reference_path(creds)
    if path is None:
        return None
    payload = _read_json_file(path)
    if not payload:
        return None
    return _oauth_creds_from_json_payload(payload, source=str(creds.get("source") or "external:oauth"), auth_file=path)


def _reference_matches(original: dict, candidate: dict) -> bool:
    original_id = original.get("id")
    if original_id and candidate.get("id") == original_id:
        return True
    original_source = original.get("source")
    if original_source and candidate.get("source") == original_source:
        original_path = str(original.get("external_token_path") or original.get("auth_file") or "")
        candidate_path = str(candidate.get("external_token_path") or candidate.get("auth_file") or "")
        return not original_path or not candidate_path or original_path == candidate_path
    return False


def _persist_external_reference_metadata(
    store: "AuthStore",
    provider: str,
    original: dict,
    runtime: dict,
) -> None:
    data = store._all()
    metadata = _sanitize_auth_creds_for_disk(provider, runtime)
    changed = False
    direct = data.get(provider)
    if isinstance(direct, dict) and _reference_matches(original, direct):
        if direct != metadata:
            data[provider] = metadata
            changed = True
    pool = data.get("credential_pool")
    entries = pool.get(provider) if isinstance(pool, dict) else None
    if isinstance(entries, list):
        for index, entry in enumerate(entries):
            if isinstance(entry, dict) and _reference_matches(original, entry):
                if entry != metadata:
                    entries[index] = metadata
                    changed = True
                break
    if changed:
        _write_auth_payload(store.path, data)


def _resync_external_oauth_creds(store: "AuthStore", provider: str, creds: dict | None) -> dict | None:
    if not isinstance(creds, dict) or not _is_borrowed_oauth_reference(provider, creds):
        return creds
    external = _external_oauth_tokens(provider, creds)
    if not external:
        return creds
    old_fingerprint = _credential_fingerprint(creds)
    runtime = {**creds, **external}
    new_fingerprint = _credential_fingerprint(runtime)
    if new_fingerprint and (not old_fingerprint or old_fingerprint != new_fingerprint):
        for key in _OAUTH_STATUS_FIELDS:
            runtime.pop(key, None)
        runtime["secret_fingerprint"] = new_fingerprint
    runtime["_borrowed_reference"] = True
    runtime["_source"] = runtime.get("source")
    if creds.get("id") is not None:
        runtime["_entry_id"] = creds.get("id")
    _persist_external_reference_metadata(store, provider, creds, runtime)
    return runtime


class CodexBackendAuth(AuthProvider):
    """Direct ``chatgpt.com/backend-api/codex`` auth using the local ``codex login``
    token (``~/.codex/auth.json``).

    Unlike the app-server transport, this calls the Responses API directly with the
    Cloudflare-required ``originator``/User-Agent, so it does NOT spawn the Codex
    runtime and therefore creates no Codex threads/rollouts; combined with the
    transport's ``store: false`` nothing is written to Codex/ChatGPT memory.
    """

    def _read(self) -> dict:
        try:
            return json.loads(_codex_auth_path().read_text())
        except Exception:
            return {}

    def _access_token(self, data: dict) -> str:
        tokens = data.get("tokens") or {}
        return tokens.get("access_token") or data.get("access_token") or ""

    def headers(self) -> dict[str, str]:
        data = self._read()
        access = self._access_token(data)
        if not access:
            raise AuthError("Not logged in to Codex. Run `codex login` (ChatGPT), then retry.")
        h = {"Authorization": f"Bearer {access}", **CODEX_ORIGINATOR_HEADERS}
        account = (data.get("tokens") or {}).get("account_id") or _jwt_account_id(access)
        if account:
            h["ChatGPT-Account-ID"] = account
        return h

    def available(self) -> bool:
        return bool(self._access_token(self._read()))

    def describe(self) -> str:
        return "codex-backend (ChatGPT login ready)" if self.available() else "codex-backend (run `codex login`)"


# --------------------------------------------------------------------------- #
# OAuth 2.0 with PKCE
# --------------------------------------------------------------------------- #
@dataclass
class OAuthConfig:
    provider: str
    client_id: str
    authorize_url: str
    token_url: str
    scopes: list[str] = field(default_factory=list)
    required_api_scopes: list[str] = field(default_factory=list)
    client_secret: str | None = None        # required by some IdPs (e.g. Google installed apps)
    # redirect handling
    redirect_uri: str | None = None        # if None -> localhost callback
    use_localhost_callback: bool = True
    localhost_port: int = 0                 # 0 -> ephemeral; fixed for providers that require it
    callback_host: str = "127.0.0.1"       # "localhost" for providers that register that host
    callback_path: str = "/callback"
    # token request encoding
    token_request_json: bool = False        # True -> JSON body, else form-encoded
    # extra params on the authorize URL
    extra_authorize_params: dict[str, str] = field(default_factory=dict)
    # headers added to *API* requests when authenticating via this OAuth token
    api_extra_headers: dict[str, str] = field(default_factory=dict)
    # some providers return "code#state" in the manual flow
    code_contains_state: bool = False


_OAUTH_SKIP_TOKENS = {"skip", "cancel", "s", "n", "no", "q", "quit"}


def _parse_oauth_callback_input(
    raw: str,
    *,
    code_contains_state: bool = False,
) -> tuple[str | None, str | None, str | None]:
    value = raw.strip()
    if not value:
        return None, None, "empty authorization response"
    if value.lower() in _OAUTH_SKIP_TOKENS:
        return None, None, "OAuth login skipped"
    if code_contains_state and "#" in value and not value.startswith(("http://", "https://")):
        code, _, state = value.partition("#")
        return code.strip() or None, state.strip() or None, None

    callback_like = False
    query = value
    if value.startswith(("http://", "https://")):
        parsed = urllib.parse.urlparse(value)
        query = parsed.query or parsed.fragment
        callback_like = True
    elif value.startswith("?"):
        query = value[1:]
        callback_like = True
    elif any(part in value for part in ("code=", "error=", "state=")):
        query = value[1:] if value.startswith("?") else value
        callback_like = True

    if not callback_like:
        return value, None, None

    params = urllib.parse.parse_qs(query, keep_blank_values=True)
    code = params.get("code", [None])[0]
    state = params.get("state", [None])[0]
    error = params.get("error", [None])[0]
    error_description = params.get("error_description", [None])[0]
    if error:
        detail = f"{error}: {error_description}" if error_description else str(error)
        return None, state, f"OAuth authorization failed: {detail}"
    if not code:
        return None, state, "OAuth callback did not contain an authorization code"
    return code, state, None


class OAuthAuth(AuthProvider):
    def __init__(self, oauth: OAuthConfig, store: "AuthStore"):
        self.oauth = oauth
        self.store = store

    # -- credential state ---------------------------------------------------
    def _creds(self) -> dict | None:
        c = _direct_oauth_creds(self.store, self.oauth.provider)
        if c and _oauth_creds_usable(c) and not self.missing_required_scopes(c):
            return c
        pooled = _pooled_oauth_creds(self.store, self.oauth.provider)
        if pooled and not self.missing_required_scopes(pooled):
            return pooled
        return None

    def _rate_limit_status(self) -> dict | None:
        return _oauth_rate_limit_status(self.store, self.oauth.provider)

    def available(self) -> bool:
        return self._creds() is not None or self._rate_limit_status() is not None

    def describe(self) -> str:
        rate_limit = self._rate_limit_status()
        if rate_limit:
            reset_at = rate_limit.get("reset_at")
            retry = ""
            if isinstance(reset_at, (int, float)) and reset_at > time.time():
                retry = f", retry after {max(0, int(reset_at - time.time()))}s"
            return f"oauth ({self.oauth.provider}: rate limited{retry}; credentials valid)"
        c = self.store.load(self.oauth.provider)
        runtime = _resync_external_oauth_creds(self.store, self.oauth.provider, c)
        if not runtime:
            pooled = _pooled_oauth_creds(self.store, self.oauth.provider)
            if pooled:
                if _is_borrowed_oauth_reference(self.oauth.provider, pooled):
                    label = _borrowed_reference_label(self.oauth.provider, pooled)
                    return f"oauth ({self.oauth.provider}: logged in via {label} pool reference)"
                return f"oauth ({self.oauth.provider}: logged in via pool)"
            return f"oauth ({self.oauth.provider}: not logged in)"
        if runtime.get("quarantined"):
            return f"oauth ({self.oauth.provider}: QUARANTINED — re-login)"
        missing = self.missing_required_scopes(runtime)
        if missing:
            return f"oauth ({self.oauth.provider}: logged in, missing scopes: {', '.join(missing)})"
        if _is_borrowed_oauth_reference(self.oauth.provider, runtime):
            label = _borrowed_reference_label(self.oauth.provider, runtime)
            return f"oauth ({self.oauth.provider}: logged in via {label} reference)"
        return f"oauth ({self.oauth.provider}: logged in)"

    def missing_required_scopes(self, creds: dict | None = None) -> list[str]:
        required = set(self.oauth.required_api_scopes or [])
        if not required:
            return []
        granted = self._granted_scopes(
            creds or _direct_oauth_creds(self.store, self.oauth.provider) or {}
        )
        return sorted(required - granted)

    @staticmethod
    def _granted_scopes(creds: dict) -> set[str]:
        scopes: set[str] = set()
        raw = creds.get("scope")
        if isinstance(raw, str):
            scopes.update(s for s in raw.replace(",", " ").split() if s)
        elif isinstance(raw, list):
            scopes.update(str(s) for s in raw if s)
        token = creds.get("access_token")
        if isinstance(token, str):
            scopes.update(_jwt_scopes(token))
        return scopes

    # -- request headers ----------------------------------------------------
    def headers(self) -> dict[str, str]:
        creds = self._creds()
        if not creds:
            rate_limit = self._rate_limit_status()
            if rate_limit:
                reset_at = rate_limit.get("reset_at")
                if isinstance(reset_at, (int, float)) and reset_at > time.time():
                    remaining = max(0, int(reset_at - time.time()))
                    message = (
                        f"{self.oauth.provider} OAuth quota exhausted (429); retry after "
                        f"{remaining}s. Credentials are still valid."
                    )
                else:
                    message = (
                        f"{self.oauth.provider} OAuth quota exhausted (429). Credentials "
                        "are still valid; retry after the usage limit resets."
                    )
                raise AuthError(
                    message,
                    provider=self.oauth.provider,
                    code=CODEX_RATE_LIMITED_CODE if self.oauth.provider == "openai-codex" else "oauth_rate_limited",
                    relogin_required=False,
                    reset_at=reset_at if isinstance(reset_at, (int, float)) else None,
                )
            missing = self.missing_required_scopes()
            if missing:
                raise AuthError(
                    f"{self.oauth.provider} OAuth token is missing required API scope(s): "
                    f"{', '.join(missing)}. Use an API key or re-login with a client that can "
                    "request model scopes."
                )
            raise AuthError(
                f"Not logged in to {self.oauth.provider} via OAuth. "
                f"Run `aegis auth login {self.oauth.provider}`."
            )
        if self._expired(creds, skew=300):
            if _is_borrowed_oauth_reference(self.oauth.provider, creds):
                creds = _resync_external_oauth_creds(self.store, self.oauth.provider, creds) or creds
                if self._expired(creds, skew=0):
                    raise AuthError(
                        f"{self.oauth.provider} OAuth credential is externally managed and expired. "
                        f"{_borrowed_refresh_hint(self.oauth.provider, creds)}",
                        provider=self.oauth.provider,
                        code="external_oauth_expired",
                        relogin_required=False,
                    )
            else:
                creds = self._refresh(creds)
        h = {"Authorization": f"{creds.get('token_type', 'Bearer')} {creds['access_token']}"}
        if self.oauth.provider == "openai-codex":
            account_id = _jwt_account_id(creds["access_token"])
            if account_id:
                h["ChatGPT-Account-ID"] = account_id
        h.update(self.oauth.api_extra_headers)
        return h

    def report(self, kind: str, error_context=None) -> bool:
        """React to provider failures for OAuth-backed providers.

        Recoverable auth failures force a token refresh and retry the same
        provider once. Rate-limit/billing failures persist an exhausted status
        so subsequent turns can avoid reusing the same OAuth credential until
        reset. Terminal auth failures are quarantined and require re-login.
        """
        kind = str(kind or "").lower()
        ctx = _oauth_error_context(error_context)
        creds = self._creds() or _direct_oauth_creds(self.store, self.oauth.provider) or _pooled_oauth_creds(
            self.store,
            self.oauth.provider,
        )
        if kind in {"auth", "auth_permanent"}:
            if kind == "auth_permanent" or _oauth_terminal_error(kind, ctx):
                _update_oauth_status(
                    self.store,
                    self.oauth.provider,
                    creds,
                    status="dead",
                    kind=kind,
                    error_context=ctx,
                    quarantine=True,
                )
                return False
            if not creds or not creds.get("refresh_token"):
                return False
            try:
                self._refresh(creds)
                return True
            except AuthError:
                return False
        if kind in {"rate_limit", "billing"}:
            _update_oauth_status(
                self.store,
                self.oauth.provider,
                creds,
                status="exhausted",
                kind=kind,
                error_context=ctx,
            )
            return False
        return False

    @staticmethod
    def _expired(creds: dict, skew: int = 0) -> bool:
        exp = creds.get("expires_at")
        if not exp:
            return False
        return time.time() >= (float(exp) - skew)

    # -- PKCE helpers -------------------------------------------------------
    @staticmethod
    def _pkce() -> tuple[str, str]:
        verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()
        ).rstrip(b"=").decode()
        return verifier, challenge

    def _authorize_url(self, challenge: str, state: str, redirect_uri: str) -> str:
        # extra params lead (claude.ai expects `code=true` first), then the standard set —
        # matching the Claude CLI authorize URL exactly.
        params = {
            **self.oauth.extra_authorize_params,
            "client_id": self.oauth.client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": " ".join(self.oauth.scopes),
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
        }
        return f"{self.oauth.authorize_url}?{urllib.parse.urlencode(params)}"

    # -- login flows --------------------------------------------------------
    def login(self, manual: bool = False) -> dict:
        if self.oauth.use_localhost_callback and not manual:
            try:
                return self._login_localhost()
            except OSError:
                pass  # fall back to manual
        return self._login_manual()

    def _login_localhost(self) -> dict:
        verifier, challenge = self._pkce()
        state = secrets.token_urlsafe(16)
        holder: dict = {}

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                q = urllib.parse.urlparse(self.path).query
                params = urllib.parse.parse_qs(q)
                holder["code"] = params.get("code", [None])[0]
                holder["state"] = params.get("state", [None])[0]
                holder["error"] = params.get("error", [None])[0]
                holder["error_description"] = params.get("error_description", [None])[0]
                self.send_response(400 if holder.get("error") else 200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                if holder.get("error"):
                    self.wfile.write(
                        b"<html><body><h2>AEGIS: login failed.</h2>"
                        b"You can close this tab and return to the terminal.</body></html>"
                    )
                else:
                    self.wfile.write(
                        b"<html><body><h2>AEGIS: login complete.</h2>"
                        b"You can close this tab and return to the terminal.</body></html>"
                    )

            def log_message(self, *_):  # silence
                pass

        server = http.server.HTTPServer((self.oauth.callback_host, self.oauth.localhost_port), Handler)
        port = server.server_address[1]
        redirect_uri = self.oauth.redirect_uri or f"http://{self.oauth.callback_host}:{port}{self.oauth.callback_path}"
        url = self._authorize_url(challenge, state, redirect_uri)
        print(f"\nOpening browser for {self.oauth.provider} login…\nIf it doesn't open, visit:\n{url}\n")
        try:
            webbrowser.open(url)
        except Exception:
            pass
        t = threading.Thread(target=server.handle_request, daemon=True)
        t.start()
        t.join(timeout=300)
        server.server_close()
        if holder.get("error"):
            detail = holder["error"]
            if holder.get("error_description"):
                detail = f"{detail}: {holder['error_description']}"
            raise AuthError(f"OAuth authorization failed: {detail}")
        code = holder.get("code")
        if not code:
            raise AuthError("OAuth timed out or was cancelled (no code received).")
        if holder.get("state") != state:
            raise AuthError("OAuth state mismatch — possible CSRF; aborting.")
        return self._exchange(code, verifier, redirect_uri)

    def _login_manual(self) -> dict:
        verifier, challenge = self._pkce()
        state = secrets.token_urlsafe(16)
        redirect_uri = self.oauth.redirect_uri or "urn:ietf:wg:oauth:2.0:oob"
        url = self._authorize_url(challenge, state, redirect_uri)
        print(
            f"\nLog in to {self.oauth.provider}:\n  1. Open this URL in your browser:\n\n{url}\n\n"
            f"  2. After authorizing, copy the code shown and paste it below.\n"
        )
        try:
            webbrowser.open(url)
        except Exception:
            pass
        raw = input("Authorization code: ").strip()
        code, returned_state, error = _parse_oauth_callback_input(
            raw,
            code_contains_state=self.oauth.code_contains_state,
        )
        if error:
            raise AuthError(error)
        if not code:
            raise AuthError("OAuth timed out or was cancelled (no code received).")
        if returned_state and returned_state != state:
            raise AuthError("OAuth state mismatch — possible CSRF; aborting.")
        return self._exchange(code, verifier, redirect_uri, state=state)

    # -- token exchange / refresh ------------------------------------------
    def _post_token(self, payload: dict) -> dict:
        headers = {"Accept": "application/json"}
        with httpx.Client(timeout=60) as client:
            if self.oauth.token_request_json:
                headers["Content-Type"] = "application/json"
                r = client.post(self.oauth.token_url, json=payload, headers=headers)
            else:
                r = client.post(self.oauth.token_url, data=payload, headers=headers)
        if r.status_code >= 400:
            raise AuthError(f"Token endpoint {r.status_code}: {r.text[:300]}")
        return r.json()

    def _store_tokens(self, data: dict) -> dict:
        creds = {
            "access_token": data["access_token"],
            "refresh_token": data.get("refresh_token"),
            "token_type": data.get("token_type", "Bearer"),
            "expires_at": (time.time() + float(data["expires_in"])) if data.get("expires_in") else None,
            "scope": data.get("scope"),
            "quarantined": False,
        }
        self.store.save(self.oauth.provider, creds)
        return creds

    def _exchange(self, code: str, verifier: str, redirect_uri: str, state: str | None = None) -> dict:
        payload = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": self.oauth.client_id,
            "code_verifier": verifier,
        }
        if self.oauth.client_secret:
            payload["client_secret"] = self.oauth.client_secret
        if state:
            payload["state"] = state
        return self._store_tokens(self._post_token(payload))

    def _refresh(self, creds: dict) -> dict:
        if _is_borrowed_oauth_reference(self.oauth.provider, creds):
            synced = _resync_external_oauth_creds(self.store, self.oauth.provider, creds) or creds
            if not self._expired(synced, skew=0):
                return synced
            raise AuthError(
                f"{self.oauth.provider} OAuth credential is externally managed and expired. "
                f"{_borrowed_refresh_hint(self.oauth.provider, synced)}",
                provider=self.oauth.provider,
                code="external_oauth_expired",
                relogin_required=False,
            )
        rt = creds.get("refresh_token")
        if not rt:
            self.store.quarantine(self.oauth.provider)
            raise AuthError(f"{self.oauth.provider} token expired and no refresh token. Re-login.")
        try:
            refresh_payload = {
                "grant_type": "refresh_token",
                "refresh_token": rt,
                "client_id": self.oauth.client_id,
            }
            if self.oauth.client_secret:
                refresh_payload["client_secret"] = self.oauth.client_secret
            data = self._post_token(refresh_payload)
        except AuthError:
            self.store.quarantine(self.oauth.provider)
            raise
        # carry forward refresh token if provider didn't return a new one
        data.setdefault("refresh_token", rt)
        return self._store_tokens(data)


# --------------------------------------------------------------------------- #
# Token storage
# --------------------------------------------------------------------------- #
class AuthStore:
    """auth.json keyed by provider name; chmod 0600; atomic writes."""

    def __init__(self, path: Path | None = None):
        self.path = path or cfg.auth_path()

    def _all(self) -> dict:
        raw = read_text(self.path)
        if not raw.strip():
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    def load(self, provider: str) -> dict | None:
        data = self._all()
        creds = data.get(provider)
        if isinstance(creds, dict) and _is_auth_credential_suppressed(data, provider, creds):
            return None
        return creds

    def save(self, provider: str, creds: dict) -> None:
        data = self._all()
        _unsuppress_auth_provider(data, provider)
        data[provider] = creds
        _write_auth_payload(self.path, data)

    def delete(self, provider: str) -> AuthRemovalResult:
        data = self._all()
        result = AuthRemovalResult(provider=provider)
        removed_credentials: list[dict] = []
        direct = data.pop(provider, None)
        if isinstance(direct, dict):
            result.removed = True
            result.removed_direct = True
            removed_credentials.append(direct)
        provider_states = data.get("providers")
        if isinstance(provider_states, dict):
            singleton = provider_states.pop(provider, None)
            if isinstance(singleton, dict):
                result.removed = True
                result.removed_direct = True
                removed_credentials.append(singleton)
            if not provider_states:
                data.pop("providers", None)

        pool = data.get("credential_pool")
        entries = pool.get(provider) if isinstance(pool, dict) else None
        if isinstance(entries, list):
            removed_pool_entries = [entry for entry in entries if isinstance(entry, dict)]
            if removed_pool_entries or entries:
                result.removed = True
                result.removed_pool_entries = len(removed_pool_entries)
                removed_credentials.extend(removed_pool_entries)
                pool.pop(provider, None)
                if not pool:
                    data.pop("credential_pool", None)

        for creds in removed_credentials:
            step = _find_auth_source_removal_step(provider, creds)
            if step is None:
                continue
            if step.suppress:
                for source in step.sources_to_suppress(provider, creds):
                    if _suppress_auth_source(data, provider, source, reason="removed"):
                        _append_unique(result.suppressed_sources, source)
            for hint in step.hints(provider, creds):
                _append_unique(result.hints, hint)

        if result.removed or result.suppressed_sources:
            _write_auth_payload(self.path, data)
        return result

    def quarantine(self, provider: str) -> None:
        data = self._all()
        if provider in data:
            data[provider]["quarantined"] = True
            _write_auth_payload(self.path, data)

    def list_logins(self) -> list[str]:
        data = self._all()
        return [
            p for p, c in data.items()
            if p not in _AUTH_RESERVED_TOP_LEVEL_KEYS
            and isinstance(c, dict)
            and not c.get("quarantined")
            and not _is_auth_credential_suppressed(data, p, c)
        ]


def _parse_reset_at(value) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        numeric = float(value)
        if numeric <= 0:
            return None
        return numeric / 1000.0 if numeric > 1_000_000_000_000 else numeric
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            numeric = float(raw)
        except ValueError:
            numeric = None
        if numeric is not None:
            return numeric / 1000.0 if numeric > 1_000_000_000_000 else numeric
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def _oauth_error_context(error_context) -> dict:
    if error_context is None:
        return {}
    if isinstance(error_context, dict):
        out: dict = {}

        def merge(node: dict) -> None:
            for key, value in node.items():
                if key == "error" and isinstance(value, dict):
                    merge(value)
                    continue
                out.setdefault(str(key), value)

        merge(error_context)
        return out
    out = {}
    for attr in ("status", "status_code", "code", "reason", "message", "body", "reset_at"):
        value = getattr(error_context, attr, None)
        if value is not None:
            out[attr] = value
    text = str(error_context).strip()
    if text:
        out.setdefault("message", text)
    return out


def _oauth_context_text(ctx: dict, *keys: str, creds: dict | None = None) -> str:
    for key in keys:
        value = ctx.get(key)
        if value is None or isinstance(value, (dict, list, tuple)):
            continue
        text = " ".join(str(value).split()).strip()
        if not text:
            continue
        if creds:
            for secret_key in ("access_token", "refresh_token"):
                secret = creds.get(secret_key)
                if secret:
                    text = text.replace(str(secret), "[credential]")
        return text[:500]
    return ""


def _oauth_status_code(kind: str, ctx: dict) -> int | None:
    for key in ("status_code", "status", "code"):
        value = ctx.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    if kind == "rate_limit":
        return 429
    if kind == "billing":
        return 402
    if kind.startswith("auth"):
        return 401
    return None


def _oauth_reset_at_from_context(ctx: dict) -> float | None:
    for key in ("reset_at", "retry_at", "x-ratelimit-reset", "x-ratelimit-reset-requests"):
        reset_at = _parse_reset_at(ctx.get(key))
        if reset_at is not None:
            return reset_at
    retry_after = ctx.get("retry_after") or ctx.get("retry-after")
    if retry_after not in (None, ""):
        try:
            seconds = float(retry_after)
        except (TypeError, ValueError):
            return _parse_reset_at(retry_after)
        return time.time() + max(0.0, seconds)
    return None


def _oauth_terminal_error(kind: str, ctx: dict) -> bool:
    values = [kind]
    for key in ("reason", "code", "type", "error", "error_code", "message", "body"):
        value = ctx.get(key)
        if value is not None and not isinstance(value, (dict, list, tuple)):
            values.append(str(value))
    haystack = " ".join(values).lower().replace("-", "_")
    return any(reason in haystack for reason in _TERMINAL_OAUTH_REASONS)


def _save_auth_payload(store: "AuthStore", payload: dict) -> None:
    _write_auth_payload(store.path, payload)


def _update_oauth_status(
    store: "AuthStore",
    provider: str,
    creds: dict | None,
    *,
    status: str,
    kind: str,
    error_context: dict,
    quarantine: bool = False,
) -> None:
    data = store._all()
    target_token = creds.get("access_token") if isinstance(creds, dict) else None
    now = time.time()
    fields = {
        "last_status": status,
        "last_error_reason": _oauth_context_text(
            error_context,
            "reason",
            "code",
            "type",
            "error_code",
            creds=creds,
        ) or status,
        "last_error_message": _oauth_context_text(
            error_context,
            "message",
            "body",
            "error_description",
            creds=creds,
        ),
        "last_error_code": _oauth_status_code(kind, error_context),
        "last_refresh": now,
    }
    if status == "exhausted":
        fields["last_error_reset_at"] = _oauth_reset_at_from_context(error_context) or (now + 3600)
    fields = {k: v for k, v in fields.items() if v not in (None, "")}

    def matches(entry: dict) -> bool:
        if creds and _is_borrowed_oauth_reference(provider, creds):
            entry_id = creds.get("_entry_id") or creds.get("id")
            if entry_id and entry.get("id") == entry_id:
                return True
            source = creds.get("_source") or creds.get("source")
            if source and entry.get("source") == source:
                return True
        return not target_token or entry.get("access_token") == target_token

    updated = False
    direct = data.get(provider)
    if isinstance(direct, dict) and matches(direct):
        direct.update(fields)
        if quarantine:
            direct["quarantined"] = True
        data[provider] = direct
        updated = True
    pool = data.get("credential_pool")
    entries = pool.get(provider) if isinstance(pool, dict) else None
    if isinstance(entries, list):
        for entry in entries:
            if isinstance(entry, dict) and matches(entry):
                entry.update(fields)
                if quarantine:
                    entry["quarantined"] = True
                updated = True
                break
    if not updated and isinstance(direct, dict):
        direct.update(fields)
        if quarantine:
            direct["quarantined"] = True
        data[provider] = direct
        updated = True
    if updated:
        _save_auth_payload(store, data)


def _oauth_creds_rate_limited(creds: dict) -> bool:
    code = creds.get("last_error_code") or creds.get("error_code")
    reason = str(creds.get("last_error_reason") or creds.get("error_reason") or "").lower()
    message = str(creds.get("last_error_message") or creds.get("error") or "").lower()
    status = str(creds.get("last_status") or creds.get("status") or "").lower()
    if status not in {"exhausted", "rate_limited", "rate-limited", "quota_exhausted", "limited"}:
        status = ""
    return (
        code == 429
        or "rate_limit" in reason
        or "usage_limit" in reason
        or "quota" in reason
        or "rate limit" in message
        or "usage limit" in message
        or "quota" in message
        or bool(status and ("exhaust" in status or "limit" in status))
    )


def _oauth_rate_limit_metadata(creds: dict) -> dict | None:
    token = creds.get("access_token")
    if not isinstance(token, str) or not token.strip():
        return None
    if not _oauth_creds_rate_limited(creds):
        return None
    reset_at = _parse_reset_at(creds.get("last_error_reset_at") or creds.get("reset_at"))
    if reset_at is not None and reset_at <= time.time():
        return None
    return {
        "label": creds.get("label") or creds.get("email") or creds.get("source"),
        "last_refresh": creds.get("last_refresh"),
        "reset_at": reset_at,
        "reason": creds.get("last_error_reason") or creds.get("error_reason"),
        "message": creds.get("last_error_message") or creds.get("error"),
    }


def _oauth_creds_usable(creds: dict | None) -> bool:
    if not isinstance(creds, dict):
        return False
    if creds.get("quarantined"):
        return False
    status = str(creds.get("last_status") or creds.get("status") or "").lower()
    if status == "dead":
        return False
    token = creds.get("access_token")
    if not isinstance(token, str) or not token.strip():
        return False
    return _oauth_rate_limit_metadata(creds) is None


def _direct_oauth_creds(store: AuthStore, provider: str) -> dict | None:
    direct = store.load(provider)
    if not isinstance(direct, dict):
        return None
    return _resync_external_oauth_creds(store, provider, direct)


def _pooled_oauth_entries(store: AuthStore, provider: str) -> list[dict]:
    try:
        payload = store._all()
    except Exception:  # noqa: BLE001
        return []
    pool = payload.get("credential_pool")
    if not isinstance(pool, dict):
        return []
    entries = pool.get(provider)
    if not isinstance(entries, list):
        return []
    return [
        entry for entry in entries
        if isinstance(entry, dict)
        and not _is_auth_credential_suppressed(payload, provider, entry)
    ]


def _entry_freshness(entry: dict) -> float:
    for key in ("last_refresh", "updated_at", "created_at"):
        parsed = _parse_reset_at(entry.get(key))
        if parsed is not None:
            return parsed
    try:
        return float(entry.get("priority") or 0)
    except (TypeError, ValueError):
        return 0.0


def _pooled_oauth_creds(store: AuthStore, provider: str) -> dict | None:
    candidates = [
        runtime for entry in _pooled_oauth_entries(store, provider)
        if (runtime := _resync_external_oauth_creds(store, provider, entry))
        and _oauth_creds_usable(runtime)
    ]
    if not candidates:
        return None
    best = max(candidates, key=_entry_freshness)
    creds = dict(best)
    creds.setdefault("token_type", "Bearer")
    return creds


def _oauth_rate_limit_status(store: AuthStore, provider: str) -> dict | None:
    direct = _direct_oauth_creds(store, provider)
    if isinstance(direct, dict):
        status = _oauth_rate_limit_metadata(direct)
        if status:
            return status
    for entry in _pooled_oauth_entries(store, provider):
        runtime = _resync_external_oauth_creds(store, provider, entry)
        status = _oauth_rate_limit_metadata(runtime) if runtime else None
        if status:
            return status
    return None


def import_claude_cli_login(store: "AuthStore | None" = None) -> tuple[bool, str]:
    """Reuse an existing Claude Code / Claude CLI login on this host
    instead of running our own claude.ai OAuth. Reads the Claude CLI credential file and
    stores the token for the `anthropic` provider; refresh then works via the normal flow."""
    import pathlib
    store = store or AuthStore()
    candidates = [
        pathlib.Path.home() / ".claude" / ".credentials.json",
        pathlib.Path.home() / ".config" / "claude" / ".credentials.json",
    ]
    src = next((p for p in candidates if p.exists()), None)
    if src is None:
        return False, "no Claude CLI login found — run Claude Code (`claude`) and log in first"
    try:
        data = json.loads(src.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return False, f"could not read {src}: {e}"
    oauth = data.get("claudeAiOauth") or {}
    token = oauth.get("accessToken")
    if not token:
        return False, f"{src} has no Claude OAuth token"
    exp = oauth.get("expiresAt", 0)
    store.save("anthropic", {
        "source": "claude_code",
        "external_token_path": str(src),
        "reference_only": True,
        "label": "Claude Code",
        "secret_fingerprint": _secret_fingerprint(token),
        "expires_at": exp / 1000 if exp and exp > 1e12 else (exp or None),  # ms -> s
        "scopes": oauth.get("scopes", []),
        "token_type": "Bearer",
    })
    sub = oauth.get("subscriptionType")
    return True, f"reused Claude CLI login{f' ({sub})' if sub else ''} for the anthropic provider"


class AuthError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        provider: str | None = None,
        code: str | None = None,
        relogin_required: bool = True,
        reset_at: float | None = None,
    ):
        self.provider = provider
        self.code = code
        self.relogin_required = relogin_required
        self.reset_at = reset_at
        super().__init__(message)


def _jwt_scopes(token: str) -> set[str]:
    """Best-effort, non-validating JWT payload decode for OAuth scope diagnostics."""
    data = _jwt_payload(token)
    raw = data.get("scp") or data.get("scope") or []
    if isinstance(raw, str):
        return {s for s in raw.replace(",", " ").split() if s}
    if isinstance(raw, list):
        return {str(s) for s in raw if s}
    return set()


def _jwt_account_id(token: str) -> str | None:
    """Best-effort ChatGPT account id extraction for Codex backend auth."""
    data = _jwt_payload(token)
    direct = data.get("chatgpt_account_id") or data.get("account_id")
    if isinstance(direct, str) and direct:
        return direct
    nested = data.get("https://api.openai.com/auth")
    if isinstance(nested, dict):
        for key in ("chatgpt_account_id", "account_id"):
            value = nested.get(key)
            if isinstance(value, str) and value:
                return value
    return None


def _jwt_payload(token: str) -> dict:
    """Best-effort, non-validating JWT payload decode."""
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    payload = parts[1]
    payload += "=" * (-len(payload) % 4)
    try:
        data = json.loads(base64.urlsafe_b64decode(payload.encode()).decode("utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    return data if isinstance(data, dict) else {}
