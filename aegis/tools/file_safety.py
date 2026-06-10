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


def _aegis_home() -> Path:
    from .. import config as cfg
    return cfg.get_home()


def is_sensitive(path) -> str:
    """'' when fine; else a short reason why this path needs explicit approval."""
    try:
        p = Path(os.path.realpath(str(Path(path).expanduser())))
    except (OSError, ValueError):
        return ""
    home = Path.home()
    for d in _HOME_DIRS:
        target = home / d
        if p == target or str(p).startswith(str(target) + os.sep):
            return f"credentials/keys location (~/{d})"
    if p.parent == home and p.name in _HOME_FILES:
        return f"shell/login configuration (~/{p.name})"
    ah = _aegis_home()
    inside = str(p).startswith(str(ah) + os.sep)
    if inside:
        rel = p.relative_to(ah)
        first = rel.parts[0] if rel.parts else ""
        # the agent's own config/secrets/auth are off-limits; its workspace is not
        if first not in ("workspace", "tool_outputs", "logs") :
            return f"agent-internal state ({ah.name}/{first})"
    return ""


def authorize_write(path, ctx) -> str:
    """'' to proceed, else an error message for the tool result."""
    cfg_obj = getattr(ctx, "config", None)
    allow = [str(Path(a).expanduser()) for a in
             ((cfg_obj.get("tools.sensitive_write_allow", []) if cfg_obj else []) or [])]
    real = os.path.realpath(str(Path(str(path)).expanduser()))
    if any(real == a or real.startswith(a + os.sep) for a in allow):
        return ""
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
