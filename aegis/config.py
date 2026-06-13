"""Configuration, paths, secrets, and workspace context files.

Layout of the runtime home (``$AEGIS_HOME`` or ``~/.aegis``)::

    config.yaml      main configuration (non-secret)
    .env             secrets (API keys); KEY=VALUE, injected into os.environ
    auth.json        OAuth tokens (chmod 0600)
    state.db         sessions (SQLite)
    SOUL.md          persona / tone (home root, matching ~/.hermes/SOUL.md)
    AGENTS.md        global operating rules (home root)
    personalities/   named persona files
    memories/        MEMORY.md, USER.md (the user profile), history.jsonl
    skills/          user/managed SKILL.md packages
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
    """Where identity/rule files (SOUL.md, AGENTS.md, personalities/) live: the home
    ROOT, matching the reference layout (``~/.aegis/SOUL.md``). A legacy nested
    ``workspace/`` from older installs is migrated up to the root by
    :func:`migrate_workspace_to_root`, called on home resolution."""
    home = ensure_dir(get_home())
    migrate_workspace_to_root(home)
    return home


# Identity/rule files relocated from the old workspace/ subdir to the home root.
_ROOT_WORKSPACE_FILES = ("SOUL.md", "AGENTS.md", ".aegis.md", "CLAUDE.md",
                         ".cursorrules", "README.md")
_migrated_homes: set[str] = set()


def migrate_workspace_to_root(home: Path) -> None:
    """One-time: lift identity/rule files and personalities/ out of a legacy
    ``<home>/workspace/`` into the home root, then park the old dir. USER.md is left
    for the memory layer to fold into memories/USER.md. Idempotent + cheap (guarded
    by an in-process set and the absence of the legacy dir)."""
    key = str(home)
    if key in _migrated_homes:
        return
    _migrated_homes.add(key)
    legacy = home / "workspace"
    if not legacy.is_dir():
        return
    try:
        for name in _ROOT_WORKSPACE_FILES:
            src, dst = legacy / name, home / name
            if src.exists() and not dst.exists():
                src.rename(dst)
        pers_src, pers_dst = legacy / "personalities", home / "personalities"
        if pers_src.is_dir() and not pers_dst.exists():
            pers_src.rename(pers_dst)
        # Park the husk ONLY when nothing live remains. A still-present USER.md must be
        # left for the memory layer to fold into memories/USER.md first — renaming the
        # dir out from under it would strand the user's profile. (Re-checked each call;
        # the husk gets parked on a later run once USER.md -> USER.md.migrated.)
        remaining = [p.name for p in legacy.iterdir()]
        if not remaining or all(n == "USER.md.migrated" for n in remaining):
            if not (home / "workspace.migrated").exists():
                legacy.rename(home / "workspace.migrated")
            else:
                import shutil
                shutil.rmtree(legacy, ignore_errors=True)
        else:
            _migrated_homes.discard(key)      # USER.md still live — re-attempt next call
    except OSError:
        _migrated_homes.discard(key)          # unwritable now — retry next call


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
        "default": "claude-sonnet-4-6",
        "base_url": None,
        "api_mode": None,
        "context_length": None,
    },
    "agent": {
        "max_iterations": 50,
        "stream": True,
        "subagent_concurrency": 4,   # max child agents run in parallel by spawn_subagent
        "max_spawn_depth": 1,        # flat by default; role=orchestrator can opt into deeper trees
        "context_engine": "default", # context-management strategy (plugins can register others)
        "reasoning_effort": "off",   # off|minimal|low|medium|high|xhigh
        "subdir_hints": True,        # inject a subdir's rule files when the agent first works there
        "compression": {"preserve_first": 3, "preserve_last": 20, "max_tool_tokens": 600,
                        # in-loop compaction fires when history fills this fraction of the
                        # model's window (Hermes-aligned default 0.50)
                        "threshold": 0.50,
                        # tail protected by a TOKEN budget = this fraction of the model's
                        # window (scales with the model; preserve_last is the legacy fallback)
                        "tail_fraction": 0.25,
                        # gateway-only safety net (runs before the agent, between turns):
                        # force a compaction when a session that accumulated between turns
                        # crosses this fraction of the window OR this many messages
                        "gateway_hygiene_threshold": 0.85,
                        "hard_message_limit": 400,
                        # when the window fills, roll into a fresh child session (parent kept
                        # intact, lineage chained) instead of editing history in place
                        "split_sessions": True},
    },
    "memory": {
        "enabled": True,
        "user_profile_enabled": True,
        "provider": "",              # "" builtin only; or "mem0" | "jsonl"
        "memory_char_limit": 2200,   # whole-store budget for MEMORY.md (Hermes default)
        "user_char_limit": 1375,     # whole-store budget for USER.md (Hermes default)
        "refresh": "session",        # session/message = rebuild when memory files change
                                     #   (facts apply next message; one cache miss per write)
                                     # frozen/never = keep prompt fixed until explicit rebuild
    },
    "tools": {
        "exec_mode": "auto",         # deny | allowlist | ask | smart | auto | full
                                     # 'auto' auto-approves tools (hardline blocklist + deny_groups
                                     # still apply); set 'ask' to prompt on dangerous tools.
        "deny_groups": [],           # e.g. ["runtime", "automation"]
        "allowlist": [],             # shell command prefixes auto-approved
        "toolsets": ["core", "mcp"], # enabled toolsets (add "browser","computer" to opt in)
        "terminal_backend": "local", # local | docker | ssh | singularity | modal | daytona
        "subagent_terminal_backend": "", # "" inherits terminal_backend; else backend for subagents
        "terminal_lifetime_seconds": 300, # idle task environments are cleaned after this long
        "docker_image": "python:3.12-slim",
        "singularity_image": "docker://python:3.12-slim",
        "modal_pip": [],             # extra pip packages for the modal sandbox image
        "allow_local_fallback": False,  # if a sandbox backend is down, refuse (fail closed)
        "max_result_tokens": 4000,   # spill tool outputs larger than this to disk (0 = never)
        "loop_warn_after": 3,        # warn after N identical tool failures/results in a turn
        "loop_block_after": 5,       # hard-block an identical failing call after N repeats
        "todo_nudge_after": 15,      # remind to update the todo list after N tool uses
        "sensitive_write_allow": [], # absolute paths exempt from file-write safety gating
        "defer_schemas": True,       # ship rarely-used tools name-only; tool_search loads them
        "deferred": [                # schemas withheld until tool_search activates them
            "generate_image", "cloud_image", "cloud_browser", "dependency_audit",
            "transcribe", "speak", "computer", "download", "github", "mixture_of_agents",
        ],
    },
    "auxiliary": {                   # small/cheap model for compaction, vision, smart-approval
        "provider": "",              # "" = reuse main provider
        "model": "",
        "compaction": {},            # optional purpose overrides: {provider, model, context_length}
        "session_summary": {},
        "trajectory_compression": {},
    },
    "security": {
        "scan_enabled": True,        # Tirith-style pre-execution command scanning
        "allow_private_urls": False, # SSRF: allow fetches to private/internal IPs (metadata still blocked)
    },
    "checkpoints": {
        "enabled": True,             # auto-checkpoint each turn's edit batch (/rollback, /diff)
    },
    "hooks": {},                     # event -> [shell commands]: session_start, pre_tool, ...
    "skills": {
        "paths": [],                 # extra skill dirs
        "autogen": True,
    },
    "curator": {                     # background maintenance of agent-created skills
        "enabled": True,
        "interval_hours": 168,       # minimum time between automatic runs (7 days)
        "min_idle_hours": 2,         # only run after the agent has been idle this long
        "stale_after_days": 30,      # active -> stale
        "archive_after_days": 90,    # stale -> archived (Hermes-aligned; never deleted)
        "backup": {
            "enabled": True,
            "keep": 5,               # tar.gz snapshots of skills/ retained before each run
        },
    },
    "cron": {
        "approval": "deny",          # headless approval for scheduled jobs: deny (safe) | approve (auto-run)
    },
    "delegation": {
        "subagent_auto_approve": False,  # Hermes parity: child approval prompts auto-deny unless opted in
    },
    "trajectory": {
        "enabled": False,            # auto-record each turn during agent runs
        "path": "trajectories.jsonl", # output file (relative to AEGIS_HOME unless absolute)
        "format": "jsonl",           # jsonl(native) | openai | hf | sharegpt
        "include_tool_results": True,
        "include_reasoning": False,  # include model reasoning/thinking
        "compress": False,           # prune/summarize large tool outputs before writing
    },
    "display": {
        "reasoning": "summary",      # off | summary | live
        "status_footer": True,
        "tool_progress": "compact",  # compact | detailed
        "theme": "system",
    },
    "responses": {
        "state": {
            "enabled": False,        # opt-in provider-native response chaining
            "store": False,          # keep OpenAI Responses stateless/ZDR-compatible by default
            "send_previous": True,
            "truncate_previous_input": True,
            "preserve_items": True,  # keep provider output item metadata for replay/debugging
        },
        "compaction": {
            "enabled": False,        # provider-native compaction hook where available
            "compact_threshold": 0.85,  # ratio shorthand; sent as a token threshold
            "compact_threshold_tokens": None,
        },
    },
    "tracing": {
        "enabled": True,
        "path": "traces.db",
        "sample_rate": 1.0,
    },
    "evals": {
        "path": "evals",
        "default_grader": "exact_or_contains",
    },
    "plugins": {
        "manifests": True,
        "enabled": [],
        "disabled": [],
        "allowlist": [],             # optional strict loader allowlist; enable/disable uses disabled list
        "registry": [],
    },
    "learn": {
        "auto": True,                # auto-review sessions on exit to propose memory/skill candidates
        "background": True,           # forked self-improvement review after substantial turns (on by default)
        "memory_every": 10,           # run a memory review every N turns (Hermes-aligned)
        "flush_min_turns": 6,          # run a final memory review on session end after N user turns
        "skill_every_iters": 10,      # run a skill review when a turn used >= N tool iterations (Hermes-aligned)
        "auto_apply": True,           # auto-write reviewed MEMORY (low risk); False = queue candidates
        "auto_apply_skills": True,    # auto-write reviewed SKILLS too (full autonomy; False = human-gated)
    },
    "mcp": {
        "enabled": True,
        "servers": {},               # name -> {command,args,env} | {url,headers}
        "catalog": [],               # [{name, command|url, args, description, tool_filter}]
    },
    "browser": {
        "headless": True,
    },
    "web": {
        "search_backend": "auto",    # auto | duckduckgo | brave | tavily | serper
        "allow_domains": [],         # if non-empty, web_fetch is allowlist-only (suffix match)
        "deny_domains": [],          # never fetch these domains (always wins)
    },
    "context_references": {
        "enabled": True,              # expand @file/@folder/@diff/@staged/@git/@url in prompts
        "max_chars": 50_000,          # total attached context per user prompt
        "max_file_chars": 20_000,
        "max_git_chars": 20_000,
        "max_url_chars": 20_000,
        "max_folder_entries": 200,
        "include_warnings": True,
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
        "show_learning": True,                # append a 'remembered/learned' footer to replies
        "busy_mode": "queue",                 # message-while-busy: queue | steer | interrupt
        "admins": [],                         # user ids/@handles with full command access
                                              #   (empty = single-user; everyone is admin)
        "user_commands": [],                  # extra slash commands non-admins may run
        "profiles": {},                       # per-platform agent overlay, e.g.
                                              #   telegram: {personality: tg, model: ..., provider: ...}
    },
    "credential_pools": {},
    "dashboard": {
        "frontend": "static",         # static | packaged
        "cockpit": True,
    },
    "goals": {
        "max_turns": 20,        # /goal auto-continuation budget before pausing
    },
    "lsp": {
        "on_edit": True,        # report NEW diagnostics after write_file/edit_file
        "auto_install": True,   # install missing language servers into <home>/lsp
        "servers": {},          # extension -> command override (".py": "pylsp")
    },
    "onboarding": {
        "profile_build": "ask",   # offer to build a user profile on the first message (ask | off)
        "seen": {},               # first-touch hints already shown (see firstrun.py)
        "tips": True,             # contextual feature-discovery tips (one-time each)
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

    The user profile is NOT a workspace file — it lives in memories/USER.md,
    owned by MemoryManager/the memory tool. (A legacy workspace/USER.md from old
    installs is auto-migrated there once and parked as USER.md.migrated.)

    Project-local files in ``cwd`` take precedence over the global workspace.
    """

    RULE_FILES = (".aegis.md", "AGENTS.md", "CLAUDE.md", ".cursorrules")

    def __init__(self, cwd: Path | None = None):
        self.cwd = cwd or Path.cwd()

    def soul(self) -> str:
        return read_text(workspace_dir() / "SOUL.md").strip()

    def rules(self) -> str:
        """Merge global workspace rules + project rules (project appended last)."""
        blocks: list[str] = []
        for name in self.RULE_FILES:
            g = read_text(workspace_dir() / name).strip()
            if g:
                blocks.append(f"<!-- global:{name} -->\n{g}")
                break
        # Project rules: layer rule files walking from the broadest ancestor down
        # to cwd, so monorepo root guidance and package-local guidance both apply.
        d = self.cwd.resolve()
        home = Path.home().resolve()
        project_blocks: list[tuple[Path, str, str]] = []
        for _ in range(40):
            for name in self.RULE_FILES:
                p = read_text(d / name).strip()
                if p:
                    project_blocks.append((d, name, p))
                    break
            if d == d.parent or d == home:
                break
            d = d.parent
        for path, name, body in reversed(project_blocks):
            blocks.append(f"<!-- project:{name} ({path}) -->\n{body}")
        return "\n\n".join(blocks).strip()
