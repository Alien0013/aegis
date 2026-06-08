"""Configuration, paths, secrets, and workspace context files.

Layout of the runtime home (``$AEGIS_HOME`` or ``~/.aegis``)::

    config.yaml      main configuration (non-secret)
    .env             secrets (API keys); KEY=VALUE, injected into os.environ
    auth.json        OAuth tokens (chmod 0600)
    state.db         sessions (SQLite)
    memories/        MEMORY.md, USER.md, history.jsonl
    skills/          user/managed SKILL.md packages
    workspace/       SOUL.md, AGENTS.md, USER.md (identity + rules)
    logs/

Precedence for settings: CLI flags > config.yaml > .env/env vars > defaults.
``get_home()`` is resolved dynamically (never cached at import) so ``--profile``
and ``$AEGIS_HOME`` switches take effect.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from .util import atomic_write, ensure_dir, read_text

# --- module-level profile override (set by CLI) ----------------------------
_PROFILE: str | None = None


def set_profile(profile: str | None) -> None:
    global _PROFILE
    _PROFILE = profile


def get_home() -> Path:
    """Resolve the runtime home dynamically. Honors $AEGIS_HOME and active profile."""
    base = os.environ.get("AEGIS_HOME")
    home = Path(base).expanduser() if base else Path.home() / ".aegis"
    if _PROFILE:
        home = home / "profiles" / _PROFILE
    return home


def sub(*parts: str) -> Path:
    return get_home().joinpath(*parts)


def memories_dir() -> Path:
    return ensure_dir(sub("memories"))


def skills_dir() -> Path:
    return ensure_dir(sub("skills"))


def workspace_dir() -> Path:
    return ensure_dir(sub("workspace"))


def sessions_db() -> Path:
    ensure_dir(get_home())
    return sub("state.db")


def logs_dir() -> Path:
    return ensure_dir(sub("logs"))


def auth_path() -> Path:
    return sub("auth.json")


def config_path() -> Path:
    return sub("config.yaml")


def env_path() -> Path:
    return sub(".env")


# --- .env handling ----------------------------------------------------------
def load_env() -> dict[str, str]:
    """Parse .env and inject into os.environ (without clobbering existing keys)."""
    path = env_path()
    parsed: dict[str, str] = {}
    if not path.exists():
        return parsed
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        parsed[key] = val
        os.environ.setdefault(key, val)
    return parsed


def set_env_var(key: str, value: str) -> None:
    """Persist a secret into .env (creating/updating the line) and the live env."""
    path = env_path()
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    out, found = [], False
    for line in lines:
        if line.strip().startswith(f"{key}=") or line.strip().startswith(f"{key} ="):
            out.append(f"{key}={value}")
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f"{key}={value}")
    atomic_write(path, "\n".join(out) + "\n")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    os.environ[key] = value


# --- config.yaml ------------------------------------------------------------
DEFAULT_CONFIG: dict[str, Any] = {
    "model": {
        "provider": "anthropic",
        "default": "claude-sonnet-4-5",
        "base_url": None,
        "api_mode": None,
        "context_length": None,
    },
    "agent": {
        "max_iterations": 50,
        "stream": True,
        "reasoning_effort": "off",   # off|minimal|low|medium|high|xhigh
        "compression": {"preserve_first": 3, "preserve_last": 20},
    },
    "memory": {
        "enabled": True,
        "user_profile_enabled": True,
        "provider": "",              # "" builtin only; or "mem0" | "jsonl"
    },
    "tools": {
        "exec_mode": "ask",          # deny | allowlist | ask | smart | auto | full
        "deny_groups": [],           # e.g. ["runtime", "automation"]
        "allowlist": [],             # shell command prefixes auto-approved
        "toolsets": ["core", "mcp"], # enabled toolsets (add "browser","computer" to opt in)
        "terminal_backend": "local", # local | docker | ssh | singularity | modal
        "docker_image": "python:3.12-slim",
        "singularity_image": "docker://python:3.12-slim",
        "modal_pip": [],             # extra pip packages for the modal sandbox image
        "allow_local_fallback": False,  # if a sandbox backend is down, refuse (fail closed)
    },
    "auxiliary": {                   # small/cheap model for compaction, vision, smart-approval
        "provider": "",              # "" = reuse main provider
        "model": "",
    },
    "security": {
        "scan_enabled": True,        # Tirith-style pre-execution command scanning
    },
    "checkpoints": {
        "enabled": False,            # snapshot files before edits for /rollback
    },
    "hooks": {},                     # event -> [shell commands]: session_start, pre_tool, ...
    "skills": {
        "paths": [],                 # extra skill dirs
        "autogen": True,
    },
    "trajectory": {
        "enabled": False,            # record trajectories during agent runs
        "path": "trajectories.jsonl", # output file (relative to AEGIS_HOME)
        "format": "jsonl",           # jsonl | hf_dataset | openai_finetune
        "realtime": True,            # record each turn as it happens
        "include_tool_results": True,
        "include_reasoning": False,  # include model reasoning/thinking
        "compress": False,           # auto-compress on export
    },
    "learn": {
        "auto": False,               # auto-review sessions on exit to propose memory/skill candidates
        "background": False,          # review periodically mid-session (off-thread) — opt-in
        "background_every": 0,        # review every N assistant turns when background is on (0 = off)
        "auto_apply": False,          # auto-promote memory candidates (skills always need review)
    },
    "mcp": {
        "enabled": True,
        "servers": {},               # name -> {command,args,env} | {url,headers}
    },
    "browser": {
        "headless": True,
    },
    "web": {
        "search_backend": "auto",    # auto | duckduckgo | brave | tavily | serper
    },
    "server": {
        "host": "127.0.0.1",
        "port": 8790,
        "api_key": None,             # optional bearer required by `aegis serve`
        "dashboard_host": "127.0.0.1",
        "dashboard_port": 9119,
        "dashboard_token": None,      # optional bearer/query token for `aegis dashboard`
    },
    "gateway": {
        "channels": [],
        "group_sessions_per_user": True,
        "session_mode": "per_channel_peer",  # main | per_channel | per_channel_peer | per_peer
        "require_mention": False,             # in group chats, only respond when mentioned
        "mention_triggers": ["@aegis"],
        "cron_interval": 60,
    },
    "fallback_providers": [],         # [{provider, model}]
    "custom_providers": [],           # [{name, base_url, api_mode, context_length, env_var}]
    "routing": [],                    # [{match: regex, provider, model}] per-prompt routing
}


def _deep_merge(base: dict, override: dict) -> dict:
    import copy
    out = copy.deepcopy(base)   # never share nested refs with DEFAULT_CONFIG
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


# Keys that are secrets — set() routes these to .env instead of config.yaml.
SECRET_SUFFIXES = ("_api_key", "_token", "_secret", "_key")


class Config:
    """Dict-backed config with dotted-path access and secret-aware ``set``."""

    def __init__(self, data: dict[str, Any]):
        self.data = data

    @classmethod
    def load(cls, profile: str | None = None) -> "Config":
        if profile is not None:
            set_profile(profile)
        load_env()
        raw = read_text(config_path())
        user = yaml.safe_load(raw) if raw.strip() else {}
        if not isinstance(user, dict):
            user = {}
        return cls(_deep_merge(DEFAULT_CONFIG, user))

    def save(self) -> None:
        # Only persist keys that differ from defaults would be ideal; for clarity
        # we persist the full effective config.
        atomic_write(config_path(), yaml.safe_dump(self.data, sort_keys=False))

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, dotted: str, value: Any) -> str:
        """Set a value. Secret-looking keys go to .env; the rest to config.yaml.

        Returns a short string describing where it was stored.
        """
        low = dotted.lower()
        # Heuristic: an UPPER_SNAKE key or a secret-suffixed key is an env secret.
        if dotted.isupper() or any(low.endswith(s) for s in SECRET_SUFFIXES):
            set_env_var(dotted if dotted.isupper() else dotted.upper(), str(value))
            return f".env ({dotted.upper() if not dotted.isupper() else dotted})"
        node = self.data
        parts = dotted.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = _coerce(value)
        self.save()
        return f"config.yaml ({dotted})"


def _coerce(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    low = value.lower()
    if low in ("true", "false"):
        return low == "true"
    if low in ("null", "none", ""):
        return None
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value


# --- Workspace context files -----------------------------------------------
class Workspace:
    """Identity + rules files, loaded hierarchically.

    SOUL.md   -> persona / tone (context tier)
    AGENTS.md / .aegis.md / CLAUDE.md -> operational rules (project + global)
    USER.md   -> user profile (volatile tier)

    Project-local files in ``cwd`` take precedence over the global workspace.
    """

    RULE_FILES = (".aegis.md", "AGENTS.md", "CLAUDE.md")

    def __init__(self, cwd: Path | None = None):
        self.cwd = cwd or Path.cwd()

    def soul(self) -> str:
        return read_text(workspace_dir() / "SOUL.md").strip()

    def user_profile(self) -> str:
        return read_text(workspace_dir() / "USER.md").strip()

    def rules(self) -> str:
        """Merge global workspace rules + project rules (project appended last)."""
        blocks: list[str] = []
        for name in self.RULE_FILES:
            g = read_text(workspace_dir() / name).strip()
            if g:
                blocks.append(f"<!-- global:{name} -->\n{g}")
                break
        for name in self.RULE_FILES:
            p = read_text(self.cwd / name).strip()
            if p:
                blocks.append(f"<!-- project:{name} ({self.cwd}) -->\n{p}")
                break
        return "\n\n".join(blocks).strip()
