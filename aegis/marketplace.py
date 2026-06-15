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
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import httpx

from . import config as cfg
from .skills import validate_skill_name
from .util import atomic_write, now_iso, read_text

# Curated registry index URLs (well-known agentskills format). Override via config.
DEFAULT_REGISTRIES = [
    "https://agentskills.io/.well-known/agent-skills/index.json",
]
MAX_SKILL_FILE_COUNT = 100
MAX_SKILL_TOTAL_BYTES = 2 * 1024 * 1024
MAX_SKILL_SINGLE_FILE_BYTES = 512 * 1024
SCANNABLE_EXTENSIONS = {
    ".bash", ".cfg", ".conf", ".css", ".html", ".ini", ".js", ".json", ".md",
    ".php", ".pl", ".py", ".rb", ".r", ".sh", ".toml", ".ts", ".txt", ".xml",
    ".yaml", ".yml",
}
SUSPICIOUS_BINARY_EXTENSIONS = {
    ".app", ".bin", ".com", ".dat", ".deb", ".dll", ".dmg", ".dylib", ".exe",
    ".msi", ".rpm", ".so",
}
SKILL_THREAT_PATTERNS = [
    (re.compile(r"\b(?:curl|wget)\b[^\n]*\$\{?\w*(?:KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|API)", re.I),
     "network command interpolates a secret environment variable"),
    (re.compile(r"\b(?:requests|httpx)\.(?:get|post|put|patch)\s*\([^\n]*(?:KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)", re.I),
     "HTTP client code references a secret variable"),
    (re.compile(r"\bos\.environ\s*\.get\s*\(\s*['\"][^'\"]*(?:KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)", re.I),
     "Python code reads a secret environment variable"),
    (re.compile(r"\brm\s+-rf\s+/", re.I), "recursive delete from filesystem root"),
    (re.compile(r"\b(?:nc|ncat)\s+-[lp]|\bsocat\b|/dev/tcp/", re.I),
     "possible reverse shell or raw network tunnel"),
    (re.compile(r"\b(?:crontab|systemctl\s+enable|launchctl\s+load)\b", re.I),
     "modifies persistent startup jobs"),
    (re.compile(r"authorized_keys|/etc/sudoers|NOPASSWD", re.I),
     "possible SSH or sudo persistence"),
    (re.compile(r"AGENTS\.md|CLAUDE\.md|\.cursorrules|\.clinerules|\.codex/config", re.I),
     "references persistent agent instruction/config files"),
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
    try:
        return validate_skill_name(name)
    except ValueError as exc:
        raise ValueError(f"invalid skill name '{name}' (must be lowercase-with-hyphens)") from exc


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


def _scan_body(body: str, rel: str = "SKILL.md") -> str | None:
    try:
        from .security_scan import scan_findings, scan_text_findings
        findings = scan_text_findings(body) + scan_findings(body)
    except Exception:  # noqa: BLE001
        findings = []
    if findings:
        return f"{rel}: {findings[0]}"
    for pattern, reason in SKILL_THREAT_PATTERNS:
        if pattern.search(body):
            return f"{rel}: {reason}"
    return None


def _hard_block_skill_tree(skill_dir: Path) -> str | None:
    """Return a non-overridable install blocker for path escapes."""
    root = skill_dir.resolve()
    for path in skill_dir.rglob("*"):
        if not path.is_symlink():
            continue
        try:
            path.resolve(strict=True).relative_to(root)
        except FileNotFoundError:
            return f"{path.relative_to(skill_dir)}: broken symlink"
        except ValueError:
            return f"{path.relative_to(skill_dir)}: symlink escapes skill directory"
    return None


def _scan_skill(skill_dir: Path) -> str | None:
    """Security-scan all text/support files in a skill package."""
    hard = _hard_block_skill_tree(skill_dir)
    if hard:
        return hard

    count = 0
    total = 0
    for path in sorted(skill_dir.rglob("*"), key=lambda p: str(p)):
        if not path.is_file():
            continue
        count += 1
        if count > MAX_SKILL_FILE_COUNT:
            return f"too many files in skill package (>{MAX_SKILL_FILE_COUNT})"
        try:
            size = path.stat().st_size
        except OSError:
            continue
        total += size
        rel = str(path.relative_to(skill_dir))
        if size > MAX_SKILL_SINGLE_FILE_BYTES:
            return f"{rel}: file is too large for a skill package"
        if path.suffix.lower() in SUSPICIOUS_BINARY_EXTENSIONS:
            return f"{rel}: suspicious binary file in skill package"
        if total > MAX_SKILL_TOTAL_BYTES:
            return f"skill package is too large (>{MAX_SKILL_TOTAL_BYTES // 1024}KB)"
        if path.suffix.lower() not in SCANNABLE_EXTENSIONS:
            continue
        try:
            reason = _scan_body(path.read_text(encoding="utf-8"), rel)
        except UnicodeDecodeError:
            continue
        if reason:
            return reason
    return None


def _scan_skill_text(body: str) -> str | None:
    try:
        return _scan_body(body, "SKILL.md")
    except Exception:  # noqa: BLE001
        return None


def install(source: str, force: bool = False) -> list[str]:
    """Install one or more skills from a source spec. Returns installed names.

    Each SKILL.md is security-scanned; flagged skills are skipped unless ``force``.
    """
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
            hard_block = _hard_block_skill_tree(d)
            if hard_block:
                raise ValueError(f"blocked '{name}': security scan flagged ({hard_block})")
            flagged = _scan_skill(d)
            if flagged and not force:
                print(f"  ⚠ skipped '{name}': security scan flagged ({flagged}); use --force")
                continue
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
        try:
            name = validate_skill_name(name)
        except ValueError as exc:
            raise ValueError(f"invalid skill name '{name}' (must be lowercase-with-hyphens)") from exc
        flagged = _scan_skill_text(body)
        if flagged and not force:
            print(f"  ⚠ skipped '{name}': security scan flagged ({flagged}); use --force")
            return []
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
        hard_block = _hard_block_skill_tree(skill_dir)
        if hard_block:
            raise ValueError(f"blocked '{name}': security scan flagged ({hard_block})")
        flagged = _scan_skill(skill_dir)
        if flagged and not force:
            print(f"  ⚠ skipped '{name}': security scan flagged ({flagged}); use --force to install")
            continue
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
    try:
        name = validate_skill_name(name)
    except ValueError:
        return False
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
    "anthropic": "anthropics/skills",
}

# GitHub repos searched/installed by the Browse Hub. Each holds SKILL.md packages
# under `path/`. These are the same official sources Hermes pulls from.
GITHUB_SKILL_REPOS = [
    {"hub": "anthropic", "repo": "anthropics/skills", "path": "skills"},
    {"hub": "openai", "repo": "openai/skills", "path": "skills/.curated"},
]


def list_taps(config) -> dict:
    taps = dict(DEFAULT_TAPS)
    taps.update(config.get("skills.taps", {}) or {})
    return taps


def list_registries(config) -> list[dict]:
    """The skill sources the Browse Hub searches/installs from (drives the UI chips).
    Defaults: agentskills.io (well-known) + the official GitHub repos; extend via
    ``skills.registries`` (well-known index URLs) and ``skills.taps`` (git repos)."""
    regs: list[dict] = [{"name": "agentskills", "kind": "well-known", "ref": DEFAULT_REGISTRIES[0]}]
    for g in GITHUB_SKILL_REPOS:
        regs.append({"name": g["hub"], "kind": "github", "ref": g["repo"]})
    for url in (config.get("skills.registries", []) or []):
        regs.append({"name": (urlparse(url).hostname or url), "kind": "well-known", "ref": url})
    known = {g["hub"] for g in GITHUB_SKILL_REPOS}
    for name, repo in (list_taps(config) or {}).items():
        if name not in known:
            regs.append({"name": name, "kind": "github", "ref": repo})
    return regs


def install_hub(name: str, config, force: bool = False) -> list[str]:
    """Install every SKILL.md package from a known/configured hub (tap)."""
    taps = list_taps(config)
    if name not in taps:
        raise ValueError(f"unknown hub '{name}'. Known: {', '.join(taps)}")
    return install(f"git:{taps[name]}", force=force)


def _github_headers() -> dict:
    h = {"Accept": "application/vnd.github+json", "User-Agent": "aegis-marketplace"}
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return h


def _github_search(repo: str, path: str, query: str, hub: str, limit: int = 40) -> list[dict]:
    """List SKILL.md packages under ``repo/path`` via the GitHub tree API, filtered
    by ``query`` (name match). Each result installs via ``git:repo/<dir>``."""
    meta = httpx.get(f"https://api.github.com/repos/{repo}", headers=_github_headers(), timeout=15)
    meta.raise_for_status()
    branch = meta.json().get("default_branch", "main")
    tree = httpx.get(f"https://api.github.com/repos/{repo}/git/trees/{branch}?recursive=1",
                     headers=_github_headers(), timeout=25)
    tree.raise_for_status()
    base = path.rstrip("/")
    q = query.lower()
    out: list[dict] = []
    for node in tree.json().get("tree", []):
        p = node.get("path", "")
        if not p.endswith("/SKILL.md"):
            continue
        skill_dir = p[: -len("/SKILL.md")]
        if base and not (skill_dir == base or skill_dir.startswith(base + "/")):
            continue
        name = skill_dir.rsplit("/", 1)[-1]
        if name.startswith("."):
            continue
        if q and q not in name.lower():
            continue
        out.append({
            "name": name,
            "description": "",
            "source": f"git:{repo}/{skill_dir}",
            "hub": hub,
            "detail_url": f"https://github.com/{repo}/tree/{branch}/{skill_dir}",
        })
        if len(out) >= limit:
            break
    return out


def _wellknown_search(url: str, query: str, hub: str = "agentskills") -> list[dict]:
    data = httpx.get(url, timeout=20, follow_redirects=True).json()
    q = query.lower()
    out: list[dict] = []
    for sk in data.get("skills", []):
        blob = f"{sk.get('name', '')} {sk.get('description', '')}".lower()
        if q in blob:
            out.append({
                "name": sk.get("name"),
                "description": sk.get("description", ""),
                "source": sk.get("source") or sk.get("repository") or url,
                "hub": hub,
                "detail_url": sk.get("homepage") or sk.get("source") or "",
            })
    return out


def search(query: str, registries: list[str] | None = None) -> list[dict]:
    """Aggregate skill search across the connected sources — well-known registry
    indexes + the official GitHub repos. Per-source failures (timeouts, rate
    limits) are swallowed so one slow hub doesn't sink the search. Each result is
    tagged with its ``hub`` and an ``installed`` flag; ``source`` feeds install()."""
    have = set(installed().keys())
    out: list[dict] = []
    seen: set[str] = set()

    def _add(items: list[dict]) -> None:
        for it in items:
            nm = it.get("name") or ""
            key = f"{it.get('hub')}/{nm}"
            if not nm or key in seen:
                continue
            seen.add(key)
            it["installed"] = nm in have
            out.append(it)

    for url in (registries or DEFAULT_REGISTRIES):
        try:
            _add(_wellknown_search(url, query))
        except Exception:  # noqa: BLE001
            continue
    if registries is None:
        for g in GITHUB_SKILL_REPOS:
            try:
                _add(_github_search(g["repo"], g["path"], query, g["hub"]))
            except Exception:  # noqa: BLE001
                continue
    return out
