"""Skill & tool marketplace: install / search / remove from git, URLs, and registries.

Sources supported:
  * ``git:owner/repo[@ref][/sub/dir]``  — clone a repo (or subdir) of SKILL.md packages
  * ``owner/repo``                        — GitHub shorthand (same as git:)
  * ``https://…/SKILL.md``                — a single skill file
  * ``https://host/.well-known/agent-skills/index.json`` — a well-known registry index

Installed skills land in ``~/.aegis/skills/<name>/`` and are tracked in
``~/.aegis/skills/.lock.json`` (source, digest, installed_at) for drift detection.
Search queries the agentskills.io / well-known registry indexes.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import httpx

from . import config as cfg
from .util import atomic_write, now_iso, read_text

NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
# Curated registry index URLs (well-known agentskills format). Override via config.
DEFAULT_REGISTRIES = [
    "https://agentskills.io/.well-known/agent-skills/index.json",
]


def _lock_path() -> Path:
    return cfg.skills_dir() / ".lock.json"


def _load_lock() -> dict:
    raw = read_text(_lock_path())
    return json.loads(raw) if raw.strip() else {}


def _save_lock(data: dict) -> None:
    atomic_write(_lock_path(), json.dumps(data, indent=2))


def _digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


def _record(name: str, source: str, digest: str) -> None:
    lock = _load_lock()
    lock[name] = {"source": source, "digest": digest, "installed_at": now_iso()}
    _save_lock(lock)


def _validate_skill_dir(d: Path) -> str:
    """Ensure d has a valid SKILL.md; return the skill name."""
    md = d / "SKILL.md"
    if not md.exists():
        raise ValueError(f"no SKILL.md in {d}")
    fm = read_text(md)
    m = re.search(r"name:\s*(.+)", fm)
    name = (m.group(1).strip() if m else d.name)
    if not NAME_RE.match(name):
        raise ValueError(f"invalid skill name '{name}' (must be lowercase-with-hyphens)")
    return name


def _git_clone(repo: str, ref: str | None, subdir: str | None) -> list[Path]:
    """Clone a repo into a temp dir; return the skill directories to install."""
    tmp = Path(tempfile.mkdtemp(prefix="aegis-skill-"))
    url = repo if repo.startswith(("http", "git@")) else f"https://github.com/{repo}.git"
    cmd = ["git", "clone", "--depth", "1"]
    if ref:
        cmd += ["--branch", ref]
    cmd += [url, str(tmp)]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"git clone failed: {res.stderr[:300]}")
    base = tmp / subdir if subdir else tmp
    if (base / "SKILL.md").exists():
        return [base]
    # otherwise every directory containing a SKILL.md (recursive — handles nested hub layouts)
    found = sorted({md.parent for md in base.rglob("SKILL.md")}, key=lambda p: str(p))
    return found[:500]


def install(source: str) -> list[str]:
    """Install one or more skills from a source spec. Returns installed names."""
    dest_root = cfg.skills_dir()
    installed: list[str] = []

    # local directory (a skill dir, or a dir of skill dirs)
    local = Path(source).expanduser()
    if local.exists() and local.is_dir():
        dirs = [local] if (local / "SKILL.md").exists() else \
            [p for p in local.iterdir() if p.is_dir() and (p / "SKILL.md").exists()]
        if not dirs:
            raise ValueError(f"no SKILL.md packages under {local}")
        for d in dirs:
            name = _validate_skill_dir(d)
            target = dest_root / name
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(d, target)
            _record(name, str(local), _digest(read_text(target / "SKILL.md")))
            installed.append(name)
        return installed

    if source.startswith("http") and source.endswith("SKILL.md"):
        body = httpx.get(source, timeout=30, follow_redirects=True).text
        m = re.search(r"name:\s*(.+)", body)
        name = m.group(1).strip() if m else "downloaded-skill"
        d = dest_root / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(body, encoding="utf-8")
        _record(name, source, _digest(body))
        return [name]

    # git / shorthand
    spec = source[4:] if source.startswith("git:") else source
    ref = None
    if "@" in spec:
        spec, ref = spec.rsplit("@", 1)
    parts = spec.split("/")
    repo = "/".join(parts[:2])
    subdir = "/".join(parts[2:]) or None

    for skill_dir in _git_clone(repo, ref, subdir):
        name = _validate_skill_dir(skill_dir)
        target = dest_root / name
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(skill_dir, target)
        _record(name, source, _digest(read_text(target / "SKILL.md")))
        installed.append(name)
    if not installed:
        raise ValueError("no installable SKILL.md packages found at source")
    return installed


def remove(name: str) -> bool:
    target = cfg.skills_dir() / name
    if target.exists():
        shutil.rmtree(target)
    lock = _load_lock()
    if name in lock:
        del lock[name]
        _save_lock(lock)
        return True
    return target.exists()


def installed() -> dict:
    return _load_lock()


# Known skill hubs (taps). `aegis skills hub install <name>` installs all SKILL.md packages.
DEFAULT_TAPS = {
    "hermeshub": "amanning3390/hermeshub",
    "openclaw": "VoltAgent/awesome-openclaw-skills",
    "anthropic": "anthropics/skills",
}


def list_taps(config) -> dict:
    taps = dict(DEFAULT_TAPS)
    taps.update(config.get("skills.taps", {}) or {})
    return taps


def install_hub(name: str, config) -> list[str]:
    """Install every SKILL.md package from a known/configured hub (tap)."""
    taps = list_taps(config)
    if name not in taps:
        raise ValueError(f"unknown hub '{name}'. Known: {', '.join(taps)}")
    return install(f"git:{taps[name]}")


def search(query: str, registries: list[str] | None = None) -> list[dict]:
    """Search well-known registry indexes. Returns [{name, description, source}]."""
    out: list[dict] = []
    for url in (registries or DEFAULT_REGISTRIES):
        try:
            data = httpx.get(url, timeout=20, follow_redirects=True).json()
        except Exception:  # noqa: BLE001
            continue
        for sk in data.get("skills", []):
            blob = f"{sk.get('name','')} {sk.get('description','')}".lower()
            if query.lower() in blob:
                out.append({
                    "name": sk.get("name"),
                    "description": sk.get("description", ""),
                    "source": sk.get("source") or sk.get("repository") or url,
                })
    return out
