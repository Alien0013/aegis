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
from dataclasses import dataclass, field
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


# --------------------------------------------------------------------------- #
# API key
# --------------------------------------------------------------------------- #
class ApiKeyAuth(AuthProvider):
    """Resolves a key from the environment (.env is loaded into os.environ)."""

    def __init__(self, env_vars: list[str], scheme: str = "bearer", extra: dict[str, str] | None = None):
        # scheme: "bearer" -> Authorization: Bearer; "anthropic" -> x-api-key; "none" -> no auth
        self.env_vars = env_vars
        self.scheme = scheme
        self.extra = extra or {}
        self._idx = 0  # credential-pool cursor

    def _pool(self) -> list[str]:
        """A credential pool: the first present env var, split on commas."""
        for var in self.env_vars:
            v = os.environ.get(var)
            if v:
                return [k.strip() for k in v.split(",") if k.strip()]
        return []

    def _key(self) -> str | None:
        pool = self._pool()
        return pool[self._idx % len(pool)] if pool else None

    def rotate(self) -> bool:
        """Advance to the next key in the pool (called on 429/401). True if rotated."""
        pool = self._pool()
        if len(pool) <= 1:
            return False
        self._idx = (self._idx + 1) % len(pool)
        return True

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


class OAuthAuth(AuthProvider):
    def __init__(self, oauth: OAuthConfig, store: "AuthStore"):
        self.oauth = oauth
        self.store = store

    # -- credential state ---------------------------------------------------
    def _creds(self) -> dict | None:
        c = self.store.load(self.oauth.provider)
        if c and not c.get("quarantined") and not self.missing_required_scopes(c):
            return c
        return None

    def available(self) -> bool:
        return self._creds() is not None

    def describe(self) -> str:
        c = self.store.load(self.oauth.provider)
        if not c:
            return f"oauth ({self.oauth.provider}: not logged in)"
        if c.get("quarantined"):
            return f"oauth ({self.oauth.provider}: QUARANTINED — re-login)"
        missing = self.missing_required_scopes(c)
        if missing:
            return f"oauth ({self.oauth.provider}: logged in, missing scopes: {', '.join(missing)})"
        return f"oauth ({self.oauth.provider}: logged in)"

    def missing_required_scopes(self, creds: dict | None = None) -> list[str]:
        required = set(self.oauth.required_api_scopes or [])
        if not required:
            return []
        granted = self._granted_scopes(creds or self.store.load(self.oauth.provider) or {})
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
            creds = self._refresh(creds)
        h = {"Authorization": f"{creds.get('token_type', 'Bearer')} {creds['access_token']}"}
        if self.oauth.provider == "openai-codex":
            account_id = _jwt_account_id(creds["access_token"])
            if account_id:
                h["chatgpt-account-id"] = account_id
        h.update(self.oauth.api_extra_headers)
        return h

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
        params = {
            "client_id": self.oauth.client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": " ".join(self.oauth.scopes),
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
            **self.oauth.extra_authorize_params,
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
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
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
        code = raw
        if self.oauth.code_contains_state and "#" in raw:
            code, _, _st = raw.partition("#")
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
        return self._all().get(provider)

    def save(self, provider: str, creds: dict) -> None:
        data = self._all()
        data[provider] = creds
        atomic_write(self.path, json.dumps(data, indent=2))
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def delete(self, provider: str) -> None:
        data = self._all()
        if provider in data:
            del data[provider]
            atomic_write(self.path, json.dumps(data, indent=2))

    def quarantine(self, provider: str) -> None:
        data = self._all()
        if provider in data:
            data[provider]["quarantined"] = True
            atomic_write(self.path, json.dumps(data, indent=2))

    def list_logins(self) -> list[str]:
        return [p for p, c in self._all().items() if not c.get("quarantined")]


class AuthError(RuntimeError):
    pass


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
