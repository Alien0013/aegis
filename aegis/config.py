"""Configuration, paths, secrets, and workspace context files.

Layout of the runtime home (``$AEGIS_HOME`` or ``~/.aegis``)::

    config.yaml      main configuration (non-secret)
    .env             secrets (API keys); KEY=VALUE, injected into os.environ
    auth.json        OAuth tokens (chmod 0600)
    state.db         sessions (SQLite)
    SOUL.md          persona / tone (home root, matching ~/.aegis/SOUL.md)
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

import contextvars
import os
from pathlib import Path
from typing import Any

import yaml

from .util import atomic_write, ensure_dir, read_text

DEFAULT_CONTEXT_FILE_MAX_CHARS = 20_000

# --- module-level profile override (set by CLI) ----------------------------
_PROFILE: str | None = None


def set_profile(profile: str | None) -> None:
    global _PROFILE
    _PROFILE = profile


def active_profile_path() -> Path:
    return _base_home() / "active_profile"


def read_active_profile() -> str:
    """Sticky default profile from ``active_profile``; ``""`` means default."""
    try:
        return _clean_profile(active_profile_path().read_text(encoding="utf-8").strip())
    except (FileNotFoundError, OSError, UnicodeDecodeError, ValueError):
        return ""


def set_active_profile(profile: str | None) -> None:
    """Persist the sticky default profile. ``None``/``default`` clears it."""
    profile = _clean_profile(profile)
    path = active_profile_path()
    ensure_dir(path.parent)
    if not profile:
        path.unlink(missing_ok=True)
        return
    atomic_write(path, profile + "\n")


def current_profile() -> str:
    """Active config profile name, or ``""`` for the default home."""
    if _PROFILE is not None:
        return _clean_profile(_PROFILE)
    return read_active_profile()


def _base_home() -> Path:
    base = os.environ.get("AEGIS_HOME")
    return Path(base).expanduser() if base else Path.home() / ".aegis"


def _clean_profile(profile: str | None) -> str:
    profile = (profile or "").strip()
    if profile in {"", "default"}:
        return ""
    import re
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", profile) or profile in {".", ".."}:
        raise ValueError(f"invalid profile name: {profile!r}")
    return profile


def profile_home(profile: str | None = None) -> Path:
    """Runtime home for ``profile`` without mutating the active process profile."""
    profile = _clean_profile(profile)
    home = _base_home()
    return home / "profiles" / profile if profile else home


def profile_name(profile: str | None = None) -> str:
    """Normalized persisted profile name; ``""`` means the default profile."""
    return _clean_profile(profile)


def available_profiles() -> list[str]:
    """Profiles with an existing runtime home, default profile first.

    This is intentionally read-only: it does not create profile directories or
    databases while tools are only trying to locate an existing session.
    """
    base = _base_home()
    profiles: list[str] = [""]
    named_root = base / "profiles"
    if named_root.is_dir():
        for child in sorted(named_root.iterdir(), key=lambda p: p.name):
            if not child.is_dir():
                continue
            try:
                name = _clean_profile(child.name)
            except ValueError:
                continue
            if name:
                profiles.append(name)
    return profiles


def get_home() -> Path:
    """Resolve the runtime home dynamically. Honors $AEGIS_HOME and active profile."""
    return profile_home(current_profile())


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


def sessions_db(profile: str | None = None) -> Path:
    home = profile_home(profile) if profile is not None else get_home()
    ensure_dir(home)
    return home / "state.db"


def logs_dir() -> Path:
    return ensure_dir(sub("logs"))


def auth_path() -> Path:
    return sub("auth.json")


def config_path() -> Path:
    return sub("config.yaml")


def env_path() -> Path:
    return sub(".env")


def parse_config_file(path: Path | None = None) -> tuple[dict[str, Any], list[str]]:
    """Read a YAML config file as user overrides plus validation errors.

    Runtime config loading stays forgiving so recovery commands like
    ``aegis config check`` can still start, but config-facing commands can use
    the returned errors to surface a broken file instead of silently treating it
    as empty.
    """
    target = path or config_path()
    try:
        raw = target.read_text(encoding="utf-8")
    except (FileNotFoundError, IsADirectoryError):
        return {}, []
    except (OSError, UnicodeDecodeError) as exc:
        return {}, [str(exc)]
    try:
        data = yaml.safe_load(raw) if raw.strip() else {}
    except yaml.YAMLError as exc:
        return {}, [str(exc)]
    if data is None:
        return {}, []
    if not isinstance(data, dict):
        return {}, ["config root must be a YAML mapping"]
    return data, []


def validate_config_file(path: Path | None = None) -> list[str]:
    """Return user-facing validation errors for ``config.yaml``."""
    _, errors = parse_config_file(path)
    return errors


def _standalone_yaml_comment_lines(text: str) -> list[str]:
    """Return standalone YAML comments worth carrying across generated saves."""
    comments: list[str] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped.startswith("#"):
            continue
        line = raw.rstrip()
        if line not in seen:
            comments.append(line)
            seen.add(line)
    return comments


def _dump_config_delta(
    delta: dict[str, Any],
    path: Path | None = None,
    *,
    comment_source: str | None = None,
) -> str:
    """Serialize config overrides while preserving user-authored comment notes."""
    target = path or config_path()
    body = yaml.safe_dump(delta, sort_keys=False, allow_unicode=True) if delta else ""
    existing = read_text(target) if comment_source is None else comment_source
    comments = _standalone_yaml_comment_lines(existing)
    if not comments:
        return body
    prefix = "\n".join(comments).rstrip() + "\n"
    return prefix + (body if body else "")


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
    "context_file_max_chars": DEFAULT_CONTEXT_FILE_MAX_CHARS,
    "timezone": "",
    "model": {
        "provider": "anthropic",
        "default": "claude-sonnet-4-6",
        "base_url": None,
        "api_mode": None,
        "context_length": None,
    },
    "agent": {
        "max_iterations": 50,
        "ultracode_max_iterations": 250,   # /ultracode raises the step budget so the loop can finish
        "stream": True,
        "subagent_concurrency": 4,   # max child agents run in parallel by spawn_subagent
        "max_spawn_depth": 1,        # flat by default; role=orchestrator can opt into deeper trees
        "context_engine": "default", # context-management strategy (plugins can register others)
        "reasoning_effort": "medium",   # off|minimal|low|medium|high|xhigh
        "service_tier": "",          # ""/normal or priority (Hermes fast/priority mode)
        "subdir_hints": True,        # inject a subdir's rule files when the agent first works there
        "compression": {"preserve_first": 3, "preserve_last": 20, "max_tool_tokens": 600,
                        # in-loop compaction fires when history fills this fraction of the
                        # model's window (default 0.50)
                        "threshold": 0.50,
                        # tail protected by a TOKEN budget = this fraction of the model's
                        # window (scales with the model; preserve_last is the legacy fallback)
                        "tail_fraction": 0.25,
                        # gateway-only safety net (runs before the agent, between turns):
                        # force a compaction when a session that accumulated between turns
                        # crosses this fraction of the window OR this many messages
                        "gateway_hygiene_threshold": 0.85,
                        "hard_message_limit": 400,
                        # Opt-in: when true, failed summarization aborts
                        # compaction instead of inserting a deterministic fallback summary.
                        "abort_on_summary_failure": False,
                        # when the window fills, roll into a fresh child session (parent kept
                        # intact, lineage chained) instead of editing history in place
                        "split_sessions": True},
    },
    "memory": {
        "enabled": True,
        "user_profile_enabled": True,
        "provider": "",              # "" builtin only; or "mem0" | "jsonl"
        "memory_char_limit": 2200,   # whole-store budget for MEMORY.md
        "user_char_limit": 1375,     # whole-store budget for USER.md
        "refresh": "frozen",         # frozen/never = keep prompt fixed until explicit rebuild
                                     #   (/new, compaction, process restart)
                                     # session/message = rebuild when memory files change
                                     #   (facts apply next message; one cache miss per write)
    },
    "tools": {
        "exec_mode": "auto",         # deny | allowlist | ask | smart | auto | full
                                     # 'auto' auto-approves tools (hardline blocklist + deny_groups
                                     # still apply); set 'ask' to prompt on dangerous tools.
        "deny_groups": [],           # e.g. ["runtime", "automation"]
        "allowlist": [],             # shell command prefixes auto-approved
        "toolsets": ["core", "mcp", "browser", "computer", "lsp", "web"],
                                     # on by default: browser (Playwright), computer (OS
                                     # screen/keyboard/mouse via pyautogui), lsp, web_extract.
                                     # Each degrades gracefully if its host deps are absent.
        "terminal_backend": "local", # local | docker | ssh | singularity | modal | daytona
        "subagent_terminal_backend": "", # "" inherits terminal_backend; else backend for subagents
        "terminal_lifetime_seconds": 300, # idle task environments are cleaned after this long
        "docker_image": "python:3.12-slim",
        "singularity_image": "docker://python:3.12-slim",
        "modal_pip": [],             # extra pip packages for the modal sandbox image
        "allow_local_fallback": False,  # if a sandbox backend is down, refuse (fail closed)
        "max_result_tokens": 4000,   # spill a single tool output larger than this (tokens; 0 = never)
        "max_turn_result_tokens": 50000, # spill largest outputs when a tool batch exceeds this
        "turn_result_preview_chars": 1500, # inline preview for aggregate-budget spills
        "max_output_chars": 30000,   # direct tool output character cap before model-side spill
        "file_read_max_chars": 100000, # refuse single read_file results larger than this
        "file_read_max_lines": 2000, # clamp read_file limit to avoid accidental huge reads
        "file_read_max_line_length": 2000, # truncate individual lines in read_file output
        "loop_warn_after": 3,        # warn after N identical tool failures/results in a turn
        "loop_same_tool_warn_after": 3, # warn when one tool keeps failing with varied args
        "loop_block_after": 5,       # hard-block an identical failing call after N repeats
        "todo_nudge_after": 15,      # remind to update the todo list after N tool uses
        "sensitive_write_allow": [], # absolute paths exempt from file-write safety gating
        "sensitive_read_allow": [],  # absolute paths exempt from secret-file read gating
        "defer_schemas": True,       # ship rarely-used tools name-only; tool_search loads them
        "deferred": [                # schemas withheld until tool_search activates them
            "generate_image", "cloud_image", "cloud_browser", "dependency_audit",
            "transcribe", "speak", "download", "github", "mixture_of_agents",
        ],
    },
    "auxiliary": {                   # small/cheap model for internal side-tasks
        "provider": "",              # "" = reuse main provider
        "model": "",
        # Per-task slots (each may set provider/model/base_url/api_key/context_length/timeout;
        # empty = inherit auxiliary.* then the main provider). Resolved by build_aux_provider.
        "compaction": {},            # context compression summaries
        "session_summary": {},       # session title/summary
        "trajectory_compression": {},
        "curator": {},               # phase-2 skill consolidation review
        "architect": {},             # /architect planning model (Aider-style; set a strong model here)
        "vision": {},                # vision_analyze image understanding
        "web_extract": {},           # web_extract page summarization
        "approval": {},              # smart command-approval classifier
        "skills_hub": {},            # skill-install scan / summarization
        "mcp": {},                   # MCP tool-selection / summarization
        "kanban_decomposer": {},     # kanban task decomposition
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
        "disabled": [],
        "allowlist": [],             # optional strict skill allowlist
        "bundles": {},               # name -> [skill, ...] for preload/slash stacks
        "template_vars": True,        # expand ${AEGIS_SKILL_DIR}/${AEGIS_SESSION_ID} in loaded skills
        "inline_shell": False,        # opt-in: expand !`cmd` snippets inside loaded skills
        "inline_shell_timeout": 10,
        "auto_load": True,            # pre-turn: attach relevant skill bodies automatically
        "auto_load_limit": 3,         # max matching skills to inject for one turn
        "auto_load_min_score": 6,     # deterministic relevance score threshold
        "auto_load_max_chars": 24000, # total chars of skill bodies attached to the prompt
    },
    "curator": {                     # background maintenance of agent-created skills
        "enabled": True,
        "interval_hours": 168,       # minimum time between automatic runs (7 days)
        "min_idle_hours": 2,         # only run after the agent has been idle this long
        "stale_after_days": 30,      # active -> stale
        "archive_after_days": 90,    # stale -> archived (never deleted)
        "llm_review": True,          # phase-2 aux-model consolidation pass (uses auxiliary.curator)
        "verify_with_evals": False,  # keep llm_review skill edits only if they don't regress the benchmark (self_improve)
        "prune_empty_sessions": True,    # session lifecycle: drop empty 'ghost' sessions during maintenance
        "session_retention_days": 7,     # only prune empty sessions untouched this long
        "prune_spent_cron": True,        # cron lifecycle: retire fired one-shots + jobs past max_runs
        "backup": {
            "enabled": True,
            "keep": 5,               # tar.gz snapshots of skills/ retained before each run
        },
    },
    "cron": {
        "approval": "deny",          # headless approval for scheduled jobs: deny (safe) | approve (auto-run)
        "skip_memory": True,         # AEGIS-style: scheduled jobs use prompt/script/skills,
                                     # not injected personal memory, unless explicitly opted in
    },
    "webhook": {
        "idempotency_ttl_seconds": 3600,  # provider retry/delivery IDs dedupe within this window
        "idempotency_cache_max": 10000,   # bounded in-process delivery-id cache
        "allow_unsigned_loopback": True,  # local dev/test webhooks may omit HMAC unless disabled
        "rate_limit_per_minute": 60,      # fixed-window limit per hook/client (0 disables)
    },
    "delegation": {
        "subagent_auto_approve": False,  # child approval prompts auto-deny unless opted in
        "max_async_children": 3,          # background subagents running at once; excess dispatches reject
        "retain_completed_background_tasks": 50,  # completed background records kept for status views
    },
    "kanban": {
        "workers": 1,                    # parallel lane workers for `kanban run`
        "dispatch_stale_timeout_seconds": 14400,  # reclaim a silent in_progress task after this (4h)
    },
    "budget": {                          # cost & latency governor (all opt-in)
        "enabled": False,
        "daily_usd": 0,                  # hard cap on spend per rolling day (0 = no cap)
        "session_usd": 0,                # hard cap on spend per session (0 = no cap)
        "enforce": "warn",               # off | warn | block (block refuses a turn over cap)
        "auto_downshift": False,         # route simple turns to a cheaper model
        "cheap_model": "",               # model for downshifted (simple) turns
    },
    "embeddings": {                  # semantic code index (code_search tool); OpenAI-compatible
        "base_url": "",              # "" = OpenAI; or any /embeddings endpoint (OpenRouter, local)
        "model": "",                 # "" = text-embedding-3-small
        "api_key": "",               # "" = env EMBEDDINGS_API_KEY / OPENAI_API_KEY
        "chunk_lines": 60,           # source lines per indexed chunk
    },
    "trajectory": {
        "enabled": True,             # auto-record each turn during agent runs
        "path": "trajectories.jsonl", # output file (relative to AEGIS_HOME unless absolute)
        "format": "jsonl",           # jsonl(native) | openai | hf | sharegpt
        "include_tool_results": True,
        "include_reasoning": False,  # include model reasoning/thinking
        "compress": True,            # prune/summarize large tool outputs before writing
    },
    "display": {
        "reasoning": "summary",      # off | summary | live
        "status_footer": True,
        "tool_progress": "compact",  # compact | detailed
        "tool_progress_grouping": "accumulate",  # accumulate | separate
        "memory_notifications": "on",  # off | on | verbose
        "theme": "system",
        "platforms": {},              # per-platform display overrides
    },
    "prompt_caching": {
        "cache_ttl": "5m",           # "5m" (default) or "1h" — TTL for Anthropic cache breakpoints
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
            "compact_threshold": 0.50,  # ratio shorthand; sent as a token threshold
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
    "bench": {
        "path": "benchmarks",         # dir of <task>/task.yaml end-to-end benchmark tasks
    },
    "spec": {
        "dir": ".aegis/specs",        # workspace-relative home for /spec requirements→design→tasks
    },
    "ambient": {
        "test_command": "",           # `aegis watch` test cmd ("" = auto-detect pytest/npm/cargo/go)
        "interval": 1.5,              # seconds between save polls
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
        "memory_every": 10,           # run a memory review every N turns
        "flush_min_turns": 6,          # run a final memory review on session end after N user turns
        "skill_every_iters": 15,      # run a skill review/creation nudge when a turn used >= N tool iterations
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
        "cdp_url": "",                  # Optional persistent Chromium CDP endpoint; /browser connect uses BROWSER_CDP_URL
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
        "remove_tokens": True,        # remove consumed @refs from the active prompt text
        "allow_outside_cwd": False,   # keep prompt attachments inside the active workspace
    },
    "workspace": {
        "context_file_max_chars": None,  # optional alias for context_file_max_chars
    },
    "server": {
        "host": "127.0.0.1",
        "port": 8790,
        "api_key": None,             # optional bearer required by `aegis serve`
        "dashboard_host": "127.0.0.1",
        "dashboard_port": 9119,
        "dashboard_token": None,      # optional bearer/query token for `aegis dashboard`
        "stale_run_health_seconds": 21600,          # detailed health flags running runs older than this
        "stale_resume_pending_health_seconds": 86400,  # detailed health flags old restart-resume markers
    },
    "gateway": {
        "channels": [],
        "message_timestamps": {
            "enabled": False,          # opt-in timestamp prefixes in model-visible gateway context
        },
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
    # Per-provider key pools: {anthropic: {keys: [...], strategy: fill_first|round_robin|
    #   least_used|random, cooldown_hours: 24}}. Keys here merge with the comma-split env var.
    # On 402/quota a key is benched for cooldown_hours; on 429/401 the pool rotates. Shared
    # process-wide so subagents reuse rotation state. See `aegis auth pool`.
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
    "custom_providers": [],           # [{name, base_url, api_mode, context_length, env_var, models:[...]}]
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


def _flat_config_values(data: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in (data or {}).items():
        path = f"{prefix}{key}"
        out[path] = value
        if isinstance(value, dict):
            out.update(_flat_config_values(value, path + "."))
    return out


def config_type_errors(data: dict[str, Any]) -> list[str]:
    """Return type mismatches for known config keys.

    Unknown keys are ignored so plugin/custom-provider config can remain
    forward-compatible. The input may be a partial override file or a fully
    merged runtime config.
    """
    if not isinstance(data, dict):
        return ["config root must be a YAML mapping"]
    defaults = _flat_config_values(DEFAULT_CONFIG)
    current = _flat_config_values(_deep_merge(DEFAULT_CONFIG, data))
    errors: list[str] = []
    for key, expected in defaults.items():
        if key not in current:
            continue
        value = current[key]
        if isinstance(expected, bool):
            ok = isinstance(value, bool)
            want = "boolean"
        elif isinstance(expected, int) and not isinstance(expected, bool):
            ok = isinstance(value, int) and not isinstance(value, bool)
            want = "integer"
        elif isinstance(expected, float):
            ok = isinstance(value, (int, float)) and not isinstance(value, bool)
            want = "number"
        elif isinstance(expected, str):
            ok = isinstance(value, str)
            want = "string"
        elif isinstance(expected, list):
            ok = isinstance(value, list)
            want = "list"
        else:
            continue
        if not ok:
            errors.append(f"{key}: expected {want}, got {type(value).__name__}")
    return errors


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
        user, _errors = parse_config_file()
        return cls(_deep_merge(DEFAULT_CONFIG, user))

    def save(self) -> None:
        # Persist only the delta from defaults (user overrides + custom keys), not
        # the full merged tree. Writing the whole tree would freeze every default
        # into config.yaml, silently masking future upgrades that change defaults.
        delta = _config_delta(self.data, DEFAULT_CONFIG)
        atomic_write(config_path(), _dump_config_delta(delta))

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in dotted.split("."):
            if isinstance(node, dict):
                if part not in node:
                    return default
                node = node[part]
            elif isinstance(node, list):
                try:
                    node = node[int(part)]
                except (TypeError, ValueError, IndexError):
                    return default
            else:
                return default
        return node

    def set(self, dotted: str, value: Any) -> str:
        """Set a value. Env-style secret keys go to .env; config paths stay in YAML.

        Returns a short string describing where it was stored.
        """
        low = dotted.lower()
        # Heuristic: UPPER_SNAKE or bare secret-suffixed keys are env secrets.
        # Dotted paths like server.api_key are real config settings and must
        # remain addressable through config.yaml.
        if "." not in dotted and (dotted.isupper() or any(low.endswith(s) for s in SECRET_SUFFIXES)):
            set_env_var(dotted if dotted.isupper() else dotted.upper(), str(value))
            return f".env ({dotted.upper() if not dotted.isupper() else dotted})"
        _set_nested(self.data, dotted, _coerce(value))
        self.save()
        return f"config.yaml ({dotted})"

    def reset(self, dotted: str) -> str:
        """Reset a config key/section to its bundled default, or remove a custom key."""
        import copy

        dotted = (dotted or "").strip()
        if not dotted:
            raise ValueError("usage: aegis config reset <key|section|all>")
        if dotted in {"all", "*"}:
            self.data = copy.deepcopy(DEFAULT_CONFIG)
            self.save()
            return "all"
        default_value = _get_nested_value(DEFAULT_CONFIG, dotted, _MISSING)
        if default_value is _MISSING:
            if not _delete_nested(self.data, dotted):
                raise ValueError(f"unknown config key: {dotted}")
            self.save()
            return dotted
        _set_nested(self.data, dotted, copy.deepcopy(default_value))
        self.save()
        return dotted


def _list_index(items: list[Any], segment: str, dotted: str) -> int:
    try:
        index = int(segment)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"cannot navigate list in {dotted!r}: segment {segment!r} is not an index"
        ) from exc
    try:
        items[index]
    except IndexError as exc:
        raise ValueError(
            f"cannot navigate list in {dotted!r}: index {index} is out of range"
        ) from exc
    return index


def _set_nested(config: dict[str, Any], dotted: str, value: Any) -> None:
    """Set dotted paths without replacing existing list nodes."""
    parts = dotted.split(".")
    node: Any = config
    for part in parts[:-1]:
        if isinstance(node, list):
            node = node[_list_index(node, part, dotted)]
        elif isinstance(node, dict):
            existing = node.get(part)
            if part not in node or not isinstance(existing, (dict, list)):
                node[part] = {}
            node = node[part]
        else:
            raise ValueError(f"cannot navigate {type(node).__name__} in {dotted!r}")
    last = parts[-1]
    if isinstance(node, list):
        node[_list_index(node, last, dotted)] = value
    elif isinstance(node, dict):
        node[last] = value
    else:
        raise ValueError(f"cannot set {dotted!r} on {type(node).__name__}")


_MISSING = object()


def _get_nested_value(data: Any, dotted: str, default: Any = _MISSING) -> Any:
    node = data
    for part in dotted.split("."):
        if isinstance(node, dict):
            if part not in node:
                return default
            node = node[part]
        elif isinstance(node, list):
            try:
                node = node[int(part)]
            except (TypeError, ValueError, IndexError):
                return default
        else:
            return default
    return node


def _delete_nested(config: dict[str, Any], dotted: str) -> bool:
    parts = dotted.split(".")
    node: Any = config
    for part in parts[:-1]:
        if isinstance(node, dict):
            if part not in node:
                return False
            node = node[part]
        elif isinstance(node, list):
            try:
                node = node[int(part)]
            except (TypeError, ValueError, IndexError):
                return False
        else:
            return False
    last = parts[-1]
    if isinstance(node, dict):
        if last not in node:
            return False
        del node[last]
        return True
    if isinstance(node, list):
        try:
            del node[int(last)]
            return True
        except (TypeError, ValueError, IndexError):
            return False
    return False


def _config_delta(data: dict, defaults: dict) -> dict:
    """Return only the entries of ``data`` that differ from ``defaults`` (recursing
    into nested dicts). Keys not present in defaults are kept verbatim. This is what
    gets written to config.yaml so the file holds overrides only."""
    out: dict[str, Any] = {}
    for key, value in data.items():
        if key not in defaults:
            out[key] = value
        elif isinstance(value, dict) and isinstance(defaults[key], dict):
            sub = _config_delta(value, defaults[key])
            if sub:
                out[key] = sub
        elif value != defaults[key]:
            out[key] = value
    return out


def _coerce(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    low = value.lower()
    if low in ("true", "false"):
        return low == "true"
    if low in ("null", "none"):
        return None
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value


_context_file_warnings: contextvars.ContextVar[list[str] | None] = (
    contextvars.ContextVar("aegis_context_file_warnings", default=None)
)


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def context_file_max_chars(config: Config | None = None, explicit: Any = None) -> int:
    """Return the configured workspace-context file cap.

    Hermes exposes this as the top-level ``context_file_max_chars`` key. AEGIS also
    accepts ``workspace.context_file_max_chars`` so profile-local config can keep
    workspace settings grouped while remaining compatible with the Hermes name.
    """
    value = explicit
    if value is None and config is not None:
        value = config.get("workspace.context_file_max_chars")
        if value in (None, ""):
            value = config.get("context_file_max_chars")
    if value in (None, ""):
        value = DEFAULT_CONTEXT_FILE_MAX_CHARS
    return _positive_int(value, DEFAULT_CONTEXT_FILE_MAX_CHARS)


def _record_context_file_warning(message: str) -> None:
    warnings = _context_file_warnings.get()
    if warnings is None:
        warnings = []
        _context_file_warnings.set(warnings)
    warnings.append(message)


def drain_context_file_warnings() -> list[str]:
    """Return and clear workspace context-file warnings for the current context."""
    warnings = list(_context_file_warnings.get() or [])
    _context_file_warnings.set(None)
    return warnings


def _truncate_context_file(body: str, label: str, max_chars: int) -> str:
    if len(body) <= max_chars:
        return body
    head_chars = max(1, int(max_chars * 0.70))
    tail_chars = max(1, int(max_chars * 0.20))
    if head_chars + tail_chars >= len(body):
        return body
    warning = (
        f"Context file {label} TRUNCATED: {len(body)} chars exceeds limit of "
        f"{max_chars} - increase context_file_max_chars or trim the file."
    )
    _record_context_file_warning(warning)
    marker = (
        f"\n\n[..., truncated {label}: kept {head_chars}+{tail_chars} of "
        f"{len(body)} chars. Use file tools to read the full file.]\n\n"
    )
    return body[:head_chars].rstrip() + marker + body[-tail_chars:].lstrip()


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

    def __init__(self, cwd: Path | None = None, *, context_file_max_chars: Any = None):
        self.cwd = cwd or Path.cwd()
        self.context_file_max_chars = context_file_max_chars

    def _context_text(self, path: Path, label: str) -> str:
        body = read_text(path).strip()
        if not body:
            return ""
        try:
            from .security_scan import scan_text_findings
            findings = scan_text_findings(body)
        except Exception:  # noqa: BLE001
            findings = []
        if findings:
            reason = findings[0].split(":", 1)[0]
            return (f"[BLOCKED: {label} contained potential prompt injection "
                    f"({reason}). Content not loaded.]")
        return _truncate_context_file(
            body,
            label,
            context_file_max_chars(explicit=self.context_file_max_chars),
        )

    def soul(self) -> str:
        return self._context_text(workspace_dir() / "SOUL.md", "SOUL.md")

    def rules(self) -> str:
        """Merge global workspace rules + project rules (project appended last)."""
        blocks: list[str] = []
        for name in self.RULE_FILES:
            g = self._context_text(workspace_dir() / name, f"global:{name}")
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
                p = self._context_text(d / name, f"project:{name} ({d})")
                if p:
                    project_blocks.append((d, name, p))
                    break
            if d == d.parent or d == home:
                break
            d = d.parent
        for path, name, body in reversed(project_blocks):
            blocks.append(f"<!-- project:{name} ({path}) -->\n{body}")
        return "\n\n".join(blocks).strip()
