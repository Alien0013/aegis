"""File-write safety: sensitive paths the file tools must not touch silently.

The bash hardline blocklist never covered the FILE tools — write_file/edit_file
would happily rewrite ``~/.ssh/authorized_keys`` or the agent's own ``.env``.
Writes to these paths now require explicit approval (the ``approver`` callback,
i.e. a human), regardless of exec_mode. ``tools.sensitive_write_allow`` in
config whitelists specific paths for automation."""

from __future__ import annotations

import os
from pathlib import Path

_HOME_DIRS = (".ssh", ".aws", ".gnupg", ".kube", ".docker/config.json", ".config/gh")
_HOME_FILES = (".bashrc", ".zshrc", ".profile", ".bash_profile", ".netrc",
               ".git-credentials")
_PROJECT_ENV_FILES = {
    ".env",
    ".env.local",
    ".env.development",
    ".env.production",
    ".env.test",
    ".env.staging",
    ".envrc",
}
_AEGIS_SECRET_FILES = {
    ".env",
    "auth.json",
    "auth.lock",
    "config.yaml",
    "webhook_subscriptions.json",
}
_SYSTEM_WRITE_PREFIXES = (
    "/etc",
    "/boot",
    "/usr/lib/systemd",
    "/private/etc",
    "/private/var/db",
    "/private/var/root",
)
_SYSTEM_WRITE_EXACT = {
    "/var/run/docker.sock",
    "/run/docker.sock",
}
_BLOCKED_DEVICE_PATHS = frozenset({
    "/dev/zero",
    "/dev/random",
    "/dev/urandom",
    "/dev/full",
    "/dev/stdin",
    "/dev/tty",
    "/dev/console",
    "/dev/stdout",
    "/dev/stderr",
    "/dev/fd/0",
    "/dev/fd/1",
    "/dev/fd/2",
})


def _aegis_home() -> Path:
    from .. import config as cfg
    return _real(cfg.get_home()) or cfg.get_home()


def _real(path) -> Path | None:
    try:
        return Path(os.path.realpath(str(Path(path).expanduser())))
    except (OSError, ValueError):
        return None


def _inside_or_same(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return path == root


def _allowed_by_list(path: Path, entries) -> bool:
    for raw in entries or []:
        target = _real(raw)
        if target is not None and _inside_or_same(path, target):
            return True
    return False


def _configured_safe_root() -> Path | None:
    raw = os.environ.get("AEGIS_WRITE_SAFE_ROOT") or ""
    raw = raw.strip()
    if not raw:
        return None
    return _real(raw)


def _safe_root_denial(path: Path) -> str:
    root = _configured_safe_root()
    if root is None or _inside_or_same(path, root):
        return ""
    return str(root)


def _is_blocked_device_path(path: str) -> bool:
    normalized = os.path.expanduser(path)
    if normalized in _BLOCKED_DEVICE_PATHS:
        return True
    if normalized.startswith("/proc/") and normalized.endswith(("/fd/0", "/fd/1", "/fd/2")):
        return True
    if normalized.startswith("/proc/") and normalized.endswith(("/environ", "/cmdline", "/maps")):
        return True
    return False


def is_blocked_read_path(path) -> bool:
    """True when a read would target a blocking device or sensitive proc file."""
    normalized = os.path.expanduser(str(path))
    if _is_blocked_device_path(normalized):
        return True
    try:
        resolved = os.path.realpath(normalized)
    except (OSError, ValueError):
        return False
    return resolved != normalized and _is_blocked_device_path(resolved)


def is_sensitive(path) -> str:
    """'' when fine; else a short reason why this path needs explicit approval."""
    p = _real(path)
    if p is None:
        return ""
    p_str = str(p)
    if p_str in _SYSTEM_WRITE_EXACT:
        return f"system control path ({p_str})"
    for prefix in _SYSTEM_WRITE_PREFIXES:
        root = Path(prefix)
        if p == root or _inside_or_same(p, root):
            return f"sensitive system path ({prefix})"
    home = Path.home()
    for d in _HOME_DIRS:
        target = home / d
        if _inside_or_same(p, target):
            return f"credentials/keys location (~/{d})"
    if p.parent == home and p.name in _HOME_FILES:
        return f"shell/login configuration (~/{p.name})"
    ah = _aegis_home()
    if _inside_or_same(p, ah) and p != ah:
        rel = p.relative_to(ah)
        first = rel.parts[0] if rel.parts else ""
        # the agent's own config/secrets/auth are off-limits; its workspace is not
        if first not in ("workspace", "tool_outputs", "logs") :
            return f"agent-internal state ({ah.name}/{first})"
    return ""


def authorize_write(path, ctx) -> str:
    """'' to proceed, else an error message for the tool result."""
    cfg_obj = getattr(ctx, "config", None)
    p = _real(path)
    if p is None:
        return ""
    allow = (cfg_obj.get("tools.sensitive_write_allow", []) if cfg_obj else []) or []
    if _allowed_by_list(p, allow):
        return ""
    safe_root = _safe_root_denial(p)
    if safe_root:
        return (
            f"blocked: {path} is outside the configured write safe root ({safe_root}). "
            "Unset AEGIS_WRITE_SAFE_ROOT or choose a path under it."
        )
    reason = is_sensitive(path)
    if not reason:
        return ""
    approver = getattr(ctx, "approver", None)
    if approver is not None:
        try:
            if approver(f"write to SENSITIVE path {path} ({reason})?"):
                return ""
        except Exception:  # noqa: BLE001
            pass
        return (f"blocked: {path} is a sensitive path ({reason}) and approval was denied.")
    return (f"blocked: {path} is a sensitive path ({reason}). A human must approve this — "
            "or add the exact path to tools.sensitive_write_allow in config.")


def read_denial(path) -> str:
    """'' when fine; else a defense-in-depth reason this file should not be read."""
    p = _real(path)
    if p is None:
        return ""
    if is_blocked_read_path(path):
        return "device/proc path that can block indefinitely or expose process internals"
    if p.name in _PROJECT_ENV_FILES:
        return ("secret-bearing environment file; read .env.example instead if you need "
                "the variable shape")
    ah = _aegis_home()
    if _inside_or_same(p, ah) and p != ah:
        rel = p.relative_to(ah)
        if rel.parts and rel.parts[0] in {"mcp-tokens", "pairing"}:
            return f"agent credential/control directory ({ah.name}/{rel.parts[0]})"
        if rel.parts and rel.parts[0] == "auth":
            return f"agent credential directory ({ah.name}/auth)"
        if len(rel.parts) == 1 and rel.parts[0] in _AEGIS_SECRET_FILES:
            return f"agent credential/control file ({ah.name}/{rel.parts[0]})"
    return ""


def authorize_read(path, ctx) -> str:
    """'' to proceed, else an error message for read_file."""
    cfg_obj = getattr(ctx, "config", None)
    p = _real(path)
    if p is None:
        return ""
    allow = (cfg_obj.get("tools.sensitive_read_allow", []) if cfg_obj else []) or []
    if _allowed_by_list(p, allow):
        return ""
    reason = read_denial(path)
    if not reason:
        return ""
    return (f"blocked: {path} is a sensitive path ({reason}). This is defense-in-depth, "
            "not a security boundary; use a non-secret example file or add the exact path "
            "to tools.sensitive_read_allow if the user explicitly approves.")
