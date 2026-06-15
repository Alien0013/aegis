"""SKILL.md skills engine (agentskills.io-compatible).

A skill is a directory with a ``SKILL.md`` whose YAML frontmatter declares at
least ``name`` and ``description``. Progressive disclosure:

  Level 0  metadata summary (name + description)  -> always in the system prompt
  Level 1  full SKILL.md body                     -> loaded on `skill view <name>`
  Level 2  references/* and scripts/*             -> read on demand by the agent

Discovery precedence (higher tier shadows same-named lower tier):
  1 workspace (cwd/.aegis/skills, cwd/skills)
  2 personal  (~/.aegis/skills)
  3 extra paths (config.skills.paths)
  4 bundled   (package builtin_skills/)
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import config as cfg
from .util import read_text

SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
WORD_RE = re.compile(r"[a-z0-9]+")
SUPPORT_DIRS = ("references", "templates", "scripts", "assets")
EXCLUDED_DISCOVERY_DIRS = {
    ".git", ".hg", ".svn", ".venv", "venv", "env", ".env",
    "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".tox", ".nox", "site-packages", "dist-packages", "build", "dist",
}
TOKEN_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "can", "do", "for",
    "from", "has", "have", "how", "i", "in", "is", "it", "me", "my", "of",
    "on", "or", "our", "please", "task", "that", "the", "this", "to", "use",
    "using", "we", "when", "with", "you", "your",
}


def validate_skill_name(name: str) -> str:
    value = str(name or "").strip()
    if not SKILL_NAME_RE.match(value):
        raise ValueError("skill name must be lowercase-with-hyphens")
    return value


def normalize_skill_name(name: str) -> str:
    return validate_skill_name(str(name or "").strip().lower())


def _skill_command_name(name: str) -> str:
    slug = str(name or "").strip().lower().replace("_", "-").replace(" ", "-")
    slug = re.sub(r"[^a-z0-9-]", "", slug)
    return re.sub(r"-{2,}", "-", slug).strip("-")


def _tokenize(text: str) -> set[str]:
    out: set[str] = set()
    for token in WORD_RE.findall(str(text or "").lower()):
        if len(token) <= 2 or token in TOKEN_STOPWORDS:
            continue
        out.add(_stem_token(token))
    return out


def _stem_token(token: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if len(token) > len(suffix) + 3 and token.endswith(suffix):
            return token[: -len(suffix)]
    return token


def _as_str_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in re.split(r"[,\s]+", value) if part.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(part).strip() for part in value if str(part).strip()]
    return []


def resolve_skill_relative_path(skill_dir: Path, rel: str) -> Path:
    candidate = Path(str(rel or ""))
    if candidate.is_absolute():
        raise ValueError("path must be relative to the skill directory")
    if not candidate.parts or any(part in ("", ".", "..") for part in candidate.parts):
        raise ValueError("path must not contain empty, '.', or '..' components")
    target = skill_dir / candidate
    try:
        target.resolve().relative_to(skill_dir.resolve())
    except (OSError, ValueError) as exc:
        raise ValueError("path escapes the skill directory") from exc
    return target


@dataclass
class Skill:
    name: str
    description: str
    path: Path                       # the SKILL.md file
    metadata: dict = field(default_factory=dict)
    requires: dict = field(default_factory=dict)
    allowed_tools: list[str] | None = None
    toolsets: list[str] = field(default_factory=list)
    tier: int = 4

    @property
    def dir(self) -> Path:
        return self.path.parent

    def metadata_summary(self) -> str:
        return f"- **{self.name}**: {self.description}"

    def full_body(self) -> str:
        raw = read_text(self.path)
        # strip frontmatter
        if raw.startswith("---"):
            _, _, body = raw.partition("---\n")[2].partition("\n---")
            return body.strip()
        return raw.strip()

    def reference(self, rel: str) -> str:
        return read_text(resolve_skill_relative_path(self.dir, rel))

    def satisfied(self) -> tuple[bool, str]:
        """Check requires.env / requires.bins / requires.os gating."""
        import shutil
        import sys

        for env in self.requires.get("env", []) or []:
            if not os.environ.get(env):
                return False, f"missing env {env}"
        for binp in self.requires.get("bins", []) or []:
            if not shutil.which(binp):
                return False, f"missing binary {binp}"
        oses = self.requires.get("os", []) or []
        if oses:
            plat = {"linux": "linux", "darwin": "macos", "win32": "windows"}.get(sys.platform, sys.platform)
            if plat not in oses:
                return False, f"os {plat} not in {oses}"
        return True, ""


def _skill_toolsets(fm: dict) -> list[str]:
    requires = fm.get("requires", {}) or {}
    metadata = fm.get("metadata", {}) or {}
    return (
        _as_str_list(fm.get("toolsets"))
        or _as_str_list(requires.get("toolsets"))
        or _as_str_list(metadata.get("toolsets"))
        or _as_str_list(metadata.get("toolset"))
    )


def _skill_allowed_tools(fm: dict) -> list[str] | None:
    value = fm.get("allowed-tools")
    if value is None:
        value = fm.get("allowed_tools")
    tools = _as_str_list(value)
    return tools or None


def _parse_frontmatter(raw: str) -> dict:
    if not raw.startswith("---"):
        return {}
    end = raw.find("\n---", 3)
    if end == -1:
        return {}
    block = raw[3:end].strip()
    try:
        data = yaml.safe_load(block)
        if isinstance(data, dict) and data.get("name") and data.get("description"):
            return data
    except yaml.YAMLError:
        data = None
    # Robust fallback: pull name/description by line even if YAML is malformed
    # (e.g. an unquoted description containing a colon — common in hub skills).
    import re as _re
    fm = data if isinstance(data, dict) else {}
    for key in ("name", "description", "version"):
        if not fm.get(key):
            m = _re.search(rf"^{key}:\s*(.+)$", block, _re.M)
            if m:
                fm[key] = m.group(1).strip().strip('"').strip("'")
    return fm


def _bundled_dir() -> Path:
    return Path(__file__).parent / "builtin_skills"


class SkillsLoader:
    def __init__(self, config: cfg.Config, cwd: Path | None = None):
        self.config = config
        self.cwd = cwd or Path.cwd()
        self._cache: dict[str, Skill] | None = None
        self._cache_signature: tuple | None = None

    def _search_paths(self) -> list[tuple[int, Path]]:
        paths: list[tuple[int, Path]] = [
            (1, self.cwd / ".aegis" / "skills"),
            (1, self.cwd / "skills"),
            (2, cfg.sub("skills")),
        ]
        for p in self.config.get("skills.paths", []) or []:
            paths.append((3, Path(p).expanduser()))
        paths.append((4, _bundled_dir()))
        return paths

    def _skill_files(self, base: Path) -> list[Path]:
        if not base.exists():
            return []
        files: list[Path] = []
        if (base / "SKILL.md").is_file():
            files.append(base / "SKILL.md")
        for root, dirs, names in os.walk(base, followlinks=False):
            dirs[:] = sorted(
                d for d in dirs
                if d not in EXCLUDED_DISCOVERY_DIRS and not d.startswith(".aegis-archive")
            )
            if "SKILL.md" in names:
                skill_md = Path(root) / "SKILL.md"
                if skill_md != base / "SKILL.md":
                    files.append(skill_md)
                dirs[:] = []
        return sorted(set(files), key=lambda p: str(p))

    def discover(self) -> dict[str, Skill]:
        signature = self._discovery_signature()
        if self._cache is not None and self._cache_signature == signature:
            return self._cache
        found: dict[str, Skill] = {}
        for tier, base in self._search_paths():
            if not base.exists():
                continue
            for skill_md in self._skill_files(base):
                raw = read_text(skill_md)
                fm = _parse_frontmatter(raw)
                name = fm.get("name") or skill_md.parent.name
                desc = fm.get("description", "").strip()
                if not desc:
                    continue
                # higher tier (lower number) wins; skip if a better one exists
                existing = found.get(name)
                if existing and existing.tier <= tier:
                    continue
                found[name] = Skill(
                    name=name,
                    description=desc,
                    path=skill_md,
                    metadata=fm.get("metadata", {}) or {},
                    requires=fm.get("requires", {}) or {},
                    allowed_tools=_skill_allowed_tools(fm),
                    toolsets=_skill_toolsets(fm),
                    tier=tier,
                )
        self._cache = found
        self._cache_signature = signature
        return found

    def is_stale(self) -> bool:
        if self._cache is None:
            return False
        return self._cache_signature != self._discovery_signature()

    def _discovery_signature(self) -> tuple:
        entries: list[tuple[str, int, int]] = []
        for _tier, base in self._search_paths():
            try:
                stat = base.stat()
                entries.append((str(base), int(stat.st_mtime_ns), int(stat.st_size)))
            except OSError:
                entries.append((str(base), 0, 0))
                continue
            for skill_md in self._skill_files(base):
                try:
                    stat = skill_md.stat()
                except OSError:
                    continue
                entries.append((str(skill_md), int(stat.st_mtime_ns), int(stat.st_size)))
        return tuple(entries)

    def _enabled(self, skill: Skill) -> tuple[bool, str]:
        disabled = {
            _skill_command_name(s)
            for s in self.config.get("skills.disabled", []) or []
            if str(s).strip()
        }
        if _skill_command_name(skill.name) in disabled:
            return False, "disabled"
        allowlist = {
            _skill_command_name(s)
            for s in self.config.get("skills.allowlist", []) or []
            if str(s).strip()
        }
        if allowlist and _skill_command_name(skill.name) not in allowlist:
            return False, "not in skills.allowlist"
        ok, why = skill.satisfied()
        if not ok:
            return False, why
        required_sets = {_skill_command_name(s) for s in skill.toolsets if str(s).strip()}
        if required_sets:
            active_sets = {
                _skill_command_name(s)
                for s in self.config.get("tools.toolsets", []) or []
                if str(s).strip()
            }
            missing = sorted(required_sets - active_sets)
            if missing:
                return False, "missing toolset " + ", ".join(missing)
        return True, ""

    def available(self) -> list[Skill]:
        return [s for s in self.discover().values() if self._enabled(s)[0]]

    def unavailable_reason(self, skill: Skill) -> str:
        return self._enabled(skill)[1]

    def _available_by_slug(self) -> dict[str, Skill]:
        out: dict[str, Skill] = {}
        for skill in self.available():
            out.setdefault(_skill_command_name(skill.name), skill)
            out.setdefault(skill.name, skill)
        return out

    def _bundle_map(self) -> dict[str, list[str]]:
        raw = self.config.get("skills.bundles", {}) or {}
        if not isinstance(raw, dict):
            return {}
        bundles: dict[str, list[str]] = {}
        for name, members in raw.items():
            slug = _skill_command_name(name)
            if not slug:
                continue
            bundles[slug] = _as_str_list(members)
        return bundles

    def resolve_requested(self, requested: list[str]) -> tuple[list[Skill], list[str], list[str]]:
        by_slug = self._available_by_slug()
        bundles = self._bundle_map()
        skills: list[Skill] = []
        loaded: set[str] = set()
        missing: list[str] = []

        def add_one(raw: str, *, bundle: str = "") -> None:
            slug = _skill_command_name(raw)
            skill = by_slug.get(raw) or by_slug.get(slug)
            if skill is None:
                missing.append(f"{bundle}:{raw}" if bundle else raw)
                return
            if skill.name in loaded:
                return
            skills.append(skill)
            loaded.add(skill.name)

        for raw in [str(item).strip() for item in requested if str(item).strip()]:
            slug = _skill_command_name(raw)
            members = bundles.get(slug)
            if members is not None:
                if not members:
                    missing.append(raw)
                    continue
                for member in members:
                    add_one(member, bundle=raw)
                continue
            add_one(raw)
        return skills, [skill.name for skill in skills], missing

    def preload_block(
        self,
        requested: list[str],
        *,
        source: str = "turn",
        user_instruction: str = "",
        max_chars: int | None = None,
    ) -> tuple[str, list[str], list[str]]:
        skills, loaded, missing = self.resolve_requested(requested)
        remaining = int(max_chars or 0) if max_chars is not None else None
        blocks: list[str] = []
        for skill in skills:
            if remaining is not None and remaining <= 0:
                break
            note = (
                f'[IMPORTANT: The "{skill.name}" skill was preloaded for this {source}. '
                "Follow its instructions unless the user explicitly overrides them.]"
            )
            block = self.activation_message(
                skill,
                note,
                user_instruction=user_instruction,
                max_chars=remaining,
            )
            if not block:
                continue
            blocks.append(block)
            self.record_use(skill.name)
            if remaining is not None:
                remaining -= len(block) + 2
        return "\n\n".join(blocks), loaded, missing

    def index_block(self) -> str:
        skills = self.available()
        if not skills:
            return ""
        lines = [s.metadata_summary() for s in sorted(skills, key=lambda s: s.name)]
        return ("# Available skills\n"
                "Before acting, scan this list. If any skill is even partially relevant, "
                "you MUST load it or rely on an AEGIS-preloaded skill body already present "
                "in the user turn. Err on the side of loading; only skip skills when none "
                "are genuinely relevant. To load manually, call the `skill` tool with "
                "action=view.\n"
                + "\n".join(lines))

    def _supporting_files(self, skill: Skill) -> list[str]:
        files: list[str] = []
        for subdir in SUPPORT_DIRS:
            root = skill.dir / subdir
            if not root.exists():
                continue
            for path in sorted(root.rglob("*")):
                if path.is_file() and not path.is_symlink():
                    files.append(str(path.relative_to(skill.dir)))
        return files

    def activation_message(
        self,
        skill: Skill,
        activation_note: str,
        *,
        user_instruction: str = "",
        max_chars: int | None = None,
    ) -> str:
        ok, why = skill.satisfied()
        parts = [activation_note, "", f"# Skill: {skill.name}", skill.full_body().strip()]
        if not ok:
            parts.insert(2, f"[Skill requirement not met: {why}]")
        parts.extend([
            "",
            f"[Skill directory: {skill.dir}]",
            "Resolve relative paths in this skill against that directory.",
        ])
        supporting = self._supporting_files(skill)
        if supporting:
            parts.append("")
            parts.append("[This skill has supporting files:]")
            parts.extend(f"- {rel} -> {skill.dir / rel}" for rel in supporting[:60])
        if user_instruction:
            parts.append("")
            parts.append("The user task for this skill invocation is:")
            parts.append(user_instruction)
        text = "\n".join(parts).strip()
        if max_chars is not None and max_chars > 0 and len(text) > max_chars:
            return text[:max_chars].rstrip() + "\n\n[Skill content truncated by skills.auto_load_max_chars.]"
        return text

    def skill_for_slash(self, text: str) -> Skill | None:
        stripped = str(text or "").strip()
        if not stripped.startswith("/"):
            return None
        first = stripped.partition(" ")[0]
        command = _skill_command_name(first.lstrip("/").replace("_", "-"))
        if not command:
            return None
        for skill in sorted(self.available(), key=lambda s: s.name):
            if _skill_command_name(skill.name) != command:
                continue
            return skill
        return None

    def slash_invocation_exists(self, text: str) -> bool:
        stripped = str(text or "").strip()
        if not stripped.startswith("/"):
            return False
        first = stripped.partition(" ")[0]
        command = _skill_command_name(first.lstrip("/").replace("_", "-"))
        return bool(command and (command in self._bundle_map() or self.skill_for_slash(text) is not None))

    def invocation_from_slash(self, text: str) -> tuple[str, list[str]] | None:
        stripped = str(text or "").strip()
        if not stripped.startswith("/"):
            return None
        first, _sep, rest = stripped.partition(" ")
        command = _skill_command_name(first.lstrip("/").replace("_", "-"))
        if command and command in self._bundle_map():
            block, loaded, missing = self.preload_block(
                [command],
                source=f"{command} skill bundle",
                user_instruction=rest,
            )
            if block:
                if missing:
                    block += "\n\n[Missing bundled skills: " + ", ".join(missing) + "]"
                return block, loaded
            if missing:
                return "[Missing bundled skills: " + ", ".join(missing) + "]", []
        skill = self.skill_for_slash(stripped)
        if skill is not None:
            self.record_use(skill.name)
            note = (
                f'[IMPORTANT: The user invoked the "{skill.name}" skill. '
                "Follow its instructions for this turn unless the user explicitly overrides them.]"
            )
            return self.activation_message(skill, note, user_instruction=rest), [skill.name]
        return None

    def relevant(self, text: str, *, limit: int = 3, min_score: int = 6) -> list[tuple[Skill, int]]:
        query = str(text or "").strip()
        if not query or query.startswith("/"):
            return []
        query_lower = query.lower()
        query_words = _tokenize(query_lower)
        if not query_words:
            return []
        scored: list[tuple[Skill, int]] = []
        for skill in self.available():
            score = 0
            name_phrase = skill.name.lower().replace("-", " ")
            if skill.name.lower() in query_lower or name_phrase in query_lower:
                score += 10
            name_words = _tokenize(skill.name.replace("-", " "))
            desc_words = _tokenize(skill.description)
            score += 4 * len(query_words & name_words)
            score += len(query_words & desc_words)
            if score >= min_score:
                scored.append((skill, score))
        scored.sort(key=lambda item: (-item[1], item[0].tier, item[0].name))
        return scored[: max(0, limit)]

    def autoload_block(
        self,
        text: str,
        *,
        limit: int = 3,
        min_score: int = 6,
        max_chars: int = 24000,
        exclude: set[str] | None = None,
    ) -> tuple[str, list[str]]:
        remaining = max(0, int(max_chars or 0))
        blocks: list[str] = []
        names: list[str] = []
        excluded = {_skill_command_name(name) for name in (exclude or set())}
        for skill, _score in self.relevant(text, limit=limit, min_score=min_score):
            if _skill_command_name(skill.name) in excluded:
                continue
            if remaining <= 0:
                break
            note = (
                f'[IMPORTANT: AEGIS selected the "{skill.name}" skill as relevant to this turn. '
                "Treat its instructions as active task guidance unless the user overrides them.]"
            )
            block = self.activation_message(skill, note, max_chars=remaining)
            if not block:
                continue
            blocks.append(block)
            names.append(skill.name)
            self.record_use(skill.name)
            remaining -= len(block) + 2
        return "\n\n".join(blocks), names

    # -- usage tracking (the self-improvement loop) -------------------------
    def _usage_path(self):
        return cfg.skills_dir() / "usage.json"

    def usage(self) -> dict:
        import json
        raw = read_text(self._usage_path())
        try:
            return json.loads(raw) if raw.strip() else {}
        except Exception:  # noqa: BLE001
            return {}

    def _bump(self, name: str, count_key: str, ts_key: str) -> None:
        """Increment one telemetry counter (use/view/patch) and its timestamp."""
        import json
        from ._locks import STORE_LOCK
        from .util import atomic_write, now_iso
        with STORE_LOCK:                       # serialize read-modify-write on usage.json
            data = self.usage()
            entry = data.setdefault(name, {"count": 0, "last_used": ""})
            entry[count_key] = int(entry.get(count_key, 0)) + 1
            entry[ts_key] = now_iso()
            atomic_write(self._usage_path(), json.dumps(data, indent=2))

    def record_use(self, name: str) -> None:
        # AEGIS use_count: skill loaded into a conversation's prompt. `count`/`last_used`
        # stay the canonical use counters for back-compat with existing readers.
        self._bump(name, "count", "last_used")

    def record_view(self, name: str) -> None:
        # AEGIS view_count: the agent inspected the skill via skill_manage view.
        self._bump(name, "view_count", "last_viewed_at")

    def record_patch(self, name: str) -> None:
        # AEGIS patch_count: skill_manage patch/edit/write_file/remove_file ran on the skill.
        self._bump(name, "patch_count", "last_patched_at")

    def improve(self, name: str, note: str) -> Path | None:
        """Append a learned note to a skill's body (closing the create→use→improve loop)."""
        from .util import atomic_write
        skill = self.discover().get(name)
        if not skill:
            return None
        body = read_text(skill.path)
        marker = "\n## Learned Notes\n"
        addition = f"- {note.strip()}\n"
        if marker in body:
            body = body.replace(marker, marker + addition, 1)
        else:
            body = body.rstrip() + "\n" + marker + addition
        atomic_write(skill.path, body)
        self.invalidate()
        return skill.path

    def activate(self, name: str) -> str | None:
        skill = self._available_by_slug().get(name) or self._available_by_slug().get(_skill_command_name(name))
        if not skill:
            return None
        self.record_use(name)
        note = (
            f'[IMPORTANT: The "{skill.name}" skill has been loaded. '
            "Follow its instructions for this task unless the user overrides them.]"
        )
        return self.activation_message(skill, note)

    def create(self, name: str, description: str, body: str,
               extra_frontmatter: dict | None = None, origin: str = "user") -> Path:
        """Write a new personal skill. ``origin='agent'`` marks it curatable (self-improvement
        path); ``origin='user'`` (default, manual/CLI) keeps it protected from auto-curation."""
        from .util import atomic_write

        name = normalize_skill_name(name)
        d = cfg.skills_dir() / name
        d.mkdir(parents=True, exist_ok=True)
        fm = {"name": name, "description": description.strip(), "version": "1.0.0"}
        fm.update(extra_frontmatter or {})
        # Serialize the whole mapping with yaml.safe_dump so values containing colons,
        # quotes, or newlines (e.g. "description: Operations: do X") are quoted/escaped
        # into valid YAML — naive "key: value" interpolation produced unparseable
        # frontmatter ("mapping values are not allowed here").
        front = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True,
                               default_flow_style=False).strip()
        atomic_write(d / "SKILL.md", f"---\n{front}\n---\n\n{body.strip()}\n")
        self.invalidate()
        try:
            from . import provenance
            # explicit origin wins; otherwise inherit the active context (agent during review)
            provenance.record(name, origin if origin != "user" else provenance.current_origin())
        except Exception:  # noqa: BLE001
            pass
        return d / "SKILL.md"

    def invalidate(self) -> None:
        self._cache = None
        self._cache_signature = None
