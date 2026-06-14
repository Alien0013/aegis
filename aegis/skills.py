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


def validate_skill_name(name: str) -> str:
    value = str(name or "").strip()
    if not SKILL_NAME_RE.match(value):
        raise ValueError("skill name must be lowercase-with-hyphens")
    return value


def normalize_skill_name(name: str) -> str:
    return validate_skill_name(str(name or "").strip().lower())


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

    def discover(self) -> dict[str, Skill]:
        signature = self._discovery_signature()
        if self._cache is not None and self._cache_signature == signature:
            return self._cache
        found: dict[str, Skill] = {}
        for tier, base in self._search_paths():
            if not base.exists():
                continue
            for skill_md in base.glob("*/SKILL.md"):
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
                    allowed_tools=(fm.get("allowed-tools") or "").split() or None
                    if isinstance(fm.get("allowed-tools"), str) else fm.get("allowed-tools"),
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
            for skill_md in sorted(base.glob("*/SKILL.md")):
                try:
                    stat = skill_md.stat()
                except OSError:
                    continue
                entries.append((str(skill_md), int(stat.st_mtime_ns), int(stat.st_size)))
        return tuple(entries)

    def available(self) -> list[Skill]:
        return [s for s in self.discover().values() if s.satisfied()[0]]

    def index_block(self) -> str:
        skills = self.available()
        if not skills:
            return ""
        lines = [s.metadata_summary() for s in sorted(skills, key=lambda s: s.name)]
        return ("# Available skills (call the `skill` tool with action=view to load one)\n"
                + "\n".join(lines))

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
        # Hermes use_count: skill loaded into a conversation's prompt. `count`/`last_used`
        # stay the canonical use counters for back-compat with existing readers.
        self._bump(name, "count", "last_used")

    def record_view(self, name: str) -> None:
        # Hermes view_count: the agent inspected the skill via skill_manage view.
        self._bump(name, "view_count", "last_viewed_at")

    def record_patch(self, name: str) -> None:
        # Hermes patch_count: skill_manage patch/edit/write_file/remove_file ran on the skill.
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
        skill = self.discover().get(name)
        if not skill:
            return None
        self.record_use(name)
        ok, why = skill.satisfied()
        header = f"# Skill: {skill.name}\n"
        if not ok:
            header += f"> ⚠ requirement not met: {why}\n\n"
        return header + skill.full_body()

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
        front = "\n".join(f"{k}: {v}" if not isinstance(v, (dict, list))
                          else f"{k}: {yaml.safe_dump(v).strip()}" for k, v in fm.items())
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
