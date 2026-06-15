"""Skill & tool marketplace: install / search / remove from git, URLs, and registries.

Sources supported:
  * ``git:owner/repo[@ref][/sub/dir]``  — clone a repo (or subdir) of SKILL.md packages
  * ``owner/repo``                        — GitHub shorthand (same as git:)
  * ``https://…/SKILL.md``                — a single skill file
  * ``https://…/*.zip`` / registry downloads — zip packages containing SKILL.md
  * ``skills-sh:owner/repo/skill-id``     — resolve a skills.sh result through GitHub
  * ``lobehub:<id>`` / ``clawhub:<slug>`` — install from LobeHub / ClawHub registries
  * ``https://host/.well-known/agent-skills/index.json`` — a well-known registry index

Installed skills land in ``~/.aegis/skills/<name>/`` and are tracked in
``~/.aegis/skills/.lock.json`` (source, digest, installed_at) for drift detection.
Search queries well-known registry indexes plus GitHub official repos, skills.sh,
LobeHub, and ClawHub.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote, urlparse
import zipfile

import httpx
import yaml

from . import config as cfg
from .skills import validate_skill_name
from .util import atomic_write, now_iso, read_text

# Curated registry index URLs (well-known agentskills format). Override via config.
DEFAULT_REGISTRIES = [
    "https://agentskills.io/.well-known/agent-skills/index.json",
]
SKILLS_SH_SEARCH_URL = "https://skills.sh/api/search"
SKILLS_SH_SITEMAP_URL = "https://www.skills.sh/sitemap.xml"
LOBEHUB_INDEX_URL = "https://chat-agents.lobehub.com/index.json"
CLAWHUB_API_BASE = "https://clawhub.ai"
MAX_SKILL_FILE_COUNT = 100
MAX_SKILL_TOTAL_BYTES = 2 * 1024 * 1024
MAX_SKILL_SINGLE_FILE_BYTES = 512 * 1024
SCANNABLE_EXTENSIONS = {
    ".bash", ".cfg", ".conf", ".css", ".html", ".ini", ".js", ".json", ".md",
    ".php", ".pl", ".py", ".rb", ".r", ".sh", ".toml", ".ts", ".txt", ".xml",
    ".yaml", ".yml",
}
EXECUTABLE_LIKE_EXTENSIONS = {
    ".bash", ".js", ".mjs", ".cjs", ".php", ".pl", ".py", ".rb", ".r", ".sh", ".ts",
}
SUSPICIOUS_BINARY_EXTENSIONS = {
    ".app", ".bin", ".com", ".dat", ".deb", ".dll", ".dmg", ".dylib", ".exe",
    ".msi", ".rpm", ".so",
}
IGNORED_SCAN_FILENAMES = {
    "license", "license.md", "license.txt", "copying", "copying.md", "copying.txt",
    "notice", "notice.md", "notice.txt",
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


def _raise_for_status(res) -> None:
    fn = getattr(res, "raise_for_status", None)
    if callable(fn):
        fn()


def _frontmatter(body: str) -> dict:
    if body.startswith("---"):
        parts = body.split("---", 2)
        if len(parts) >= 3:
            try:
                parsed = yaml.safe_load(parts[1]) or {}
                return parsed if isinstance(parsed, dict) else {}
            except Exception:  # noqa: BLE001
                return {}
    return {}


def _frontmatter_value(body: str, key: str) -> str:
    fm = _frontmatter(body)
    value = fm.get(key)
    if value is not None:
        return str(value).strip()
    m = re.search(rf"^{re.escape(key)}:\s*(.+)$", body, re.MULTILINE)
    return m.group(1).strip().strip("\"'") if m else ""


def _slugify_skill_name(value: str, fallback: str = "downloaded-skill") -> str:
    value = (value or fallback).strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    value = re.sub(r"-{2,}", "-", value)
    return value or fallback


def _coerce_skill_name(value: str, fallback: str = "downloaded-skill") -> str:
    try:
        return validate_skill_name(value)
    except ValueError:
        return validate_skill_name(_slugify_skill_name(value, fallback))


def _replace_frontmatter_name(body: str, name: str) -> str:
    if re.search(r"^name:\s*.+$", body, re.MULTILINE):
        return re.sub(r"^name:\s*.+$", f"name: {name}", body, count=1, flags=re.MULTILINE)
    if body.startswith("---"):
        return body.replace("---\n", f"---\nname: {name}\n", 1)
    return f"---\nname: {name}\ndescription: Downloaded skill.\n---\n\n{body.lstrip()}"


def _validate_skill_dir(d: Path) -> str:
    """Ensure d has a valid SKILL.md; return the skill name."""
    md = d / "SKILL.md"
    if not md.exists():
        raise ValueError(f"no SKILL.md in {d}")
    fm = read_text(md)
    name = _frontmatter_value(fm, "name") or d.name
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
        findings = scan_text_findings(body) if Path(rel).name == "SKILL.md" else []
        if Path(rel).suffix.lower() in EXECUTABLE_LIKE_EXTENSIONS:
            findings += scan_findings(body)
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
        if path.name.lower() in IGNORED_SCAN_FILENAMES:
            continue
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


def _install_skill_dirs(dirs: list[Path], source: str, force: bool = False,
                        fallback_name: str | None = None) -> list[str]:
    dest_root = cfg.skills_dir()
    installed: list[str] = []
    for d in dirs:
        try:
            name = _validate_skill_dir(d)
        except ValueError:
            if not fallback_name:
                raise
            name = _coerce_skill_name(fallback_name)
            skill_md = d / "SKILL.md"
            skill_md.write_text(_replace_frontmatter_name(read_text(skill_md), name), encoding="utf-8")
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
        _record(name, source, _digest(read_text(target / "SKILL.md")))
        installed.append(name)
    return installed


def _parse_git_spec(source: str) -> tuple[str, str | None, str | None]:
    spec = source[4:] if source.startswith("git:") else source
    ref = None
    subdir = None
    parts = spec.split("/")
    if len(parts) < 2 or not parts[0] or not parts[1]:
        raise ValueError(f"invalid git skill source: {source}")
    repo = "/".join(parts[:2])
    rest = "/".join(parts[2:])
    if "@" in parts[1]:
        owner = parts[0]
        repo_name, ref = parts[1].split("@", 1)
        repo = f"{owner}/{repo_name}"
        subdir = rest or None
    elif "@" in spec:
        before, after = spec.split("@", 1)
        before_parts = before.split("/")
        repo = "/".join(before_parts[:2])
        prefix_subdir = "/".join(before_parts[2:])
        after_parts = after.split("/")
        ref = after_parts[0] or None
        suffix_subdir = "/".join(after_parts[1:])
        subdir = "/".join(p for p in (prefix_subdir, suffix_subdir) if p) or None
    else:
        subdir = rest or None
    return repo, ref, subdir


def _github_source_from_url(source: str) -> str | None:
    parsed = urlparse(source)
    host = parsed.netloc.lower()
    parts = [p for p in parsed.path.split("/") if p]
    if host == "raw.githubusercontent.com" and len(parts) >= 5 and parts[-1] == "SKILL.md":
        owner, repo, ref = parts[:3]
        path = "/".join(parts[3:])
        return f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{path}"
    if host != "github.com" or len(parts) < 2:
        return None
    owner, repo = parts[:2]
    if len(parts) == 2:
        return f"git:{owner}/{repo}"
    if len(parts) >= 5 and parts[2] in {"blob", "raw"} and parts[-1] == "SKILL.md":
        ref = parts[3]
        path = "/".join(parts[4:])
        return f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{path}"
    if len(parts) >= 5 and parts[2] == "tree":
        ref = parts[3]
        subdir = "/".join(parts[4:])
        return f"git:{owner}/{repo}@{ref}/{subdir}"
    return None


def _safe_extract_zip(body: bytes, target: Path) -> None:
    with zipfile.ZipFile(io.BytesIO(body)) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            dest = (target / info.filename).resolve()
            try:
                dest.relative_to(target.resolve())
            except ValueError as exc:
                raise ValueError(f"zip member escapes skill directory: {info.filename}") from exc
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, dest.open("wb") as out:
                shutil.copyfileobj(src, out)


def _skill_dirs_under(base: Path) -> list[Path]:
    if (base / "SKILL.md").exists():
        return [base]
    found = sorted({md.parent for md in base.rglob("SKILL.md")}, key=lambda p: str(p))
    return found[:500]


def _install_zip_url(source: str, force: bool = False, fallback_name: str | None = None) -> list[str]:
    res = httpx.get(source, timeout=45, follow_redirects=True)
    _raise_for_status(res)
    tmp = Path(tempfile.mkdtemp(prefix="aegis-skill-zip-"))
    _safe_extract_zip(res.content, tmp)
    installed = _install_skill_dirs(_skill_dirs_under(tmp), source, force=force, fallback_name=fallback_name)
    if not installed:
        raise ValueError("no installable SKILL.md packages found in zip")
    return installed


def _install_lobehub(identifier: str, force: bool = False) -> list[str]:
    identifier = identifier.strip().strip("/")
    if not identifier:
        raise ValueError("lobehub skill id is required")
    url = f"https://chat-agents.lobehub.com/{quote(identifier)}.json"
    res = httpx.get(url, timeout=30, follow_redirects=True)
    _raise_for_status(res)
    data = res.json()
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    config = data.get("config") if isinstance(data.get("config"), dict) else {}
    name = _coerce_skill_name(identifier)
    title = str(meta.get("title") or data.get("title") or identifier).strip()
    description = str(meta.get("description") or title or f"LobeHub agent {identifier}").strip()
    system_role = str(config.get("systemRole") or data.get("systemRole") or "").strip()
    opening = str(config.get("openingMessage") or "").strip()
    body_parts = [
        "---",
        f"name: {name}",
        "description: " + json.dumps(description[:500], ensure_ascii=False),
        f"source: {json.dumps(url, ensure_ascii=False)}",
        f"homepage: {json.dumps(f'https://lobehub.com/agent/{identifier}', ensure_ascii=False)}",
        "---",
        "",
        f"# {title}",
        "",
        system_role or description,
    ]
    if opening:
        body_parts += ["", "## Opening Message", "", opening]
    body = "\n".join(body_parts).rstrip() + "\n"
    flagged = _scan_skill_text(body)
    if flagged and not force:
        print(f"  ⚠ skipped '{name}': security scan flagged ({flagged}); use --force")
        return []
    d = cfg.skills_dir() / name
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(body, encoding="utf-8")
    _record(name, f"lobehub:{identifier}", _digest(body))
    return [name]


def _install_clawhub(slug: str, force: bool = False) -> list[str]:
    slug = _slugify_skill_name(slug)
    url = f"{CLAWHUB_API_BASE}/api/v1/download?slug={quote(slug)}"
    return _install_zip_url(url, force=force, fallback_name=slug)


def _github_find_skill_source(repo: str, skill_id: str) -> str:
    meta = httpx.get(f"https://api.github.com/repos/{repo}", headers=_github_headers(), timeout=15)
    _raise_for_status(meta)
    branch = meta.json().get("default_branch", "main")
    tree = httpx.get(f"https://api.github.com/repos/{repo}/git/trees/{branch}?recursive=1",
                     headers=_github_headers(), timeout=25)
    _raise_for_status(tree)
    candidates: list[str] = []
    suffix = f"/{skill_id}/SKILL.md"
    for node in tree.json().get("tree", []):
        path = node.get("path", "")
        if path == f"{skill_id}/SKILL.md" or path.endswith(suffix):
            candidates.append(path[: -len("/SKILL.md")])
    if not candidates:
        raise ValueError(f"skill '{skill_id}' not found in {repo}")
    candidates.sort(key=lambda p: (0 if p == f"skills/{skill_id}" else 1, len(p), p))
    return f"git:{repo}/{candidates[0]}"


def _install_skills_sh(source: str, force: bool = False) -> list[str]:
    spec = source.removeprefix("skills-sh:").strip("/")
    parts = spec.split("/")
    if len(parts) < 3:
        raise ValueError("skills.sh source must be skills-sh:owner/repo/skill-id")
    repo = "/".join(parts[:2])
    skill_id = parts[-1]
    resolved = _github_find_skill_source(repo, skill_id)
    return install(resolved, force=force)


def install(source: str, force: bool = False) -> list[str]:
    """Install one or more skills from a source spec. Returns installed names.

    Each SKILL.md is security-scanned; flagged skills are skipped unless ``force``.
    """
    source = (source or "").strip()
    normalized = _github_source_from_url(source) if source.startswith("http") else None
    if normalized:
        source = normalized

    if source.startswith("lobehub:"):
        return _install_lobehub(source.removeprefix("lobehub:"), force=force)
    if source.startswith("clawhub:"):
        return _install_clawhub(source.removeprefix("clawhub:"), force=force)
    if source.startswith("skills-sh:"):
        return _install_skills_sh(source, force=force)

    # local directory (a skill dir, or a dir of skill dirs)
    local = Path(source).expanduser()
    if local.exists() and local.is_dir():
        dirs = [local] if (local / "SKILL.md").exists() else \
            [p for p in local.iterdir() if p.is_dir() and (p / "SKILL.md").exists()]
        if not dirs:
            raise ValueError(f"no SKILL.md packages under {local}")
        return _install_skill_dirs(dirs, str(local), force=force)

    if source.startswith("http") and source.endswith("SKILL.md"):
        res = httpx.get(source, timeout=30, follow_redirects=True)
        _raise_for_status(res)
        body = res.text
        name = _frontmatter_value(body, "name") or "downloaded-skill"
        try:
            name = validate_skill_name(name)
        except ValueError as exc:
            raise ValueError(f"invalid skill name '{name}' (must be lowercase-with-hyphens)") from exc
        flagged = _scan_skill_text(body)
        if flagged and not force:
            print(f"  ⚠ skipped '{name}': security scan flagged ({flagged}); use --force")
            return []
        d = cfg.skills_dir() / name
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(body, encoding="utf-8")
        _record(name, source, _digest(body))
        return [name]

    if source.startswith("http") and (source.endswith(".zip") or "/download" in urlparse(source).path):
        return _install_zip_url(source, force=force)

    # git / shorthand
    repo, ref, subdir = _parse_git_spec(source)
    installed = _install_skill_dirs(_git_clone(repo, ref, subdir), source, force=force)
    if not installed:
        raise ValueError("no installable SKILL.md packages found at source")
    return installed


def remove(name: str) -> bool:
    try:
        name = validate_skill_name(name)
    except ValueError:
        return False
    target = cfg.skills_dir() / name
    existed = target.exists()
    if target.exists():
        shutil.rmtree(target)
    lock = _load_lock()
    if name in lock:
        del lock[name]
        _save_lock(lock)
        return True
    return existed


def installed() -> dict:
    return _load_lock()


# Known skill hubs (taps). `aegis skills hub install <name>` installs all SKILL.md packages.
DEFAULT_TAPS = {
    "anthropic": "anthropics/skills",
}

# GitHub repos searched/installed by the Browse Hub. Each holds SKILL.md packages
# under `path/`. These are the same official sources AEGIS pulls from.
GITHUB_SKILL_REPOS = [
    {"hub": "anthropic", "repo": "anthropics/skills", "path": "skills"},
    {"hub": "openai-curated", "repo": "openai/skills", "path": "skills/.curated"},
    {"hub": "openai-system", "repo": "openai/skills", "path": "skills/.system"},
]


def list_taps(config) -> dict:
    taps = dict(DEFAULT_TAPS)
    taps.update(config.get("skills.taps", {}) or {})
    return taps


def list_registries(config) -> list[dict]:
    """The skill sources the Browse Hub searches/installs from (drives the UI chips).
    Defaults: agentskills.io (well-known) + the official GitHub repos; extend via
    ``skills.registries`` (well-known index URLs) and ``skills.taps`` (git repos)."""
    regs: list[dict] = [
        {"name": "agentskills", "kind": "well-known", "ref": DEFAULT_REGISTRIES[0]},
        {"name": "skills.sh", "kind": "api+sitemap", "ref": SKILLS_SH_SEARCH_URL},
        {"name": "lobehub", "kind": "agent-index", "ref": LOBEHUB_INDEX_URL},
        {"name": "clawhub", "kind": "registry", "ref": CLAWHUB_API_BASE},
    ]
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
    _raise_for_status(meta)
    branch = meta.json().get("default_branch", "main")
    tree = httpx.get(f"https://api.github.com/repos/{repo}/git/trees/{branch}?recursive=1",
                     headers=_github_headers(), timeout=25)
    _raise_for_status(tree)
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


def _skillssh_result(sk: dict) -> dict | None:
    repo = str(sk.get("source") or "").strip().strip("/")
    skill_id = str(sk.get("skillId") or sk.get("name") or "").strip().strip("/")
    if not repo or "/" not in repo or not skill_id:
        return None
    name = _slugify_skill_name(skill_id)
    installs = sk.get("installs")
    desc = f"{installs} installs" if isinstance(installs, int) else ""
    return {
        "name": name,
        "description": desc,
        "source": f"skills-sh:{repo}/{skill_id}",
        "hub": "skills.sh",
        "detail_url": f"https://www.skills.sh/{repo}/{skill_id}",
    }


def _skillssh_search(query: str, limit: int = 40) -> list[dict]:
    url = f"{SKILLS_SH_SEARCH_URL}?q={quote(query)}"
    data = httpx.get(url, timeout=20, follow_redirects=True).json()
    out: list[dict] = []
    for sk in data.get("skills", []):
        item = _skillssh_result(sk)
        if item:
            out.append(item)
        if len(out) >= limit:
            break
    return out


def _skillssh_sitemap_search(query: str, limit: int = 40) -> list[dict]:
    q = query.lower().strip()
    if len(q) < 2:
        return []
    root = ET.fromstring(httpx.get(SKILLS_SH_SITEMAP_URL, timeout=20, follow_redirects=True).text)
    ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    sitemaps = [
        loc.text for loc in root.findall(".//s:loc", ns)
        if loc.text and "sitemap-skills" in loc.text
    ]
    out: list[dict] = []
    for sitemap in sitemaps:
        page = ET.fromstring(httpx.get(sitemap, timeout=20, follow_redirects=True).text)
        for loc in page.findall(".//s:loc", ns):
            detail = loc.text or ""
            parsed = urlparse(detail)
            parts = [p for p in parsed.path.split("/") if p]
            if len(parts) < 3:
                continue
            name = parts[-1]
            blob = " ".join(parts).lower()
            if q not in blob:
                continue
            repo = "/".join(parts[:2])
            out.append({
                "name": _slugify_skill_name(name),
                "description": "",
                "source": f"skills-sh:{repo}/{name}",
                "hub": "skills.sh",
                "detail_url": detail,
            })
            if len(out) >= limit:
                return out
    return out


def _lobehub_search(query: str, limit: int = 40) -> list[dict]:
    data = httpx.get(LOBEHUB_INDEX_URL, timeout=25, follow_redirects=True).json()
    q = query.lower()
    out: list[dict] = []
    for agent in data.get("agents", []):
        ident = str(agent.get("identifier") or "").strip()
        if not ident:
            continue
        meta = agent.get("meta") if isinstance(agent.get("meta"), dict) else {}
        tags = " ".join(str(t) for t in (meta.get("tags") or []))
        blob = f"{ident} {meta.get('title', '')} {meta.get('description', '')} {tags}".lower()
        if q and q not in blob:
            continue
        out.append({
            "name": _slugify_skill_name(ident),
            "description": str(meta.get("description") or meta.get("title") or ""),
            "source": f"lobehub:{ident}",
            "hub": "lobehub",
            "detail_url": f"https://lobehub.com/agent/{ident}",
        })
        if len(out) >= limit:
            break
    return out


def _clawhub_search(query: str, limit: int = 40) -> list[dict]:
    if query.strip():
        url = f"{CLAWHUB_API_BASE}/api/v1/search?q={quote(query)}&family=skill&limit={limit}"
    else:
        url = f"{CLAWHUB_API_BASE}/api/v1/skills?limit={limit}"
    data = httpx.get(url, timeout=20, follow_redirects=True,
                     headers={"Accept": "application/json", "User-Agent": "aegis-marketplace"}).json()
    rows = data.get("results") or data.get("items") or []
    out: list[dict] = []
    for row in rows:
        package = row.get("package") if isinstance(row, dict) and isinstance(row.get("package"), dict) else row
        if not isinstance(package, dict):
            continue
        slug = str(package.get("slug") or package.get("name") or "").strip()
        if not slug:
            continue
        out.append({
            "name": _slugify_skill_name(slug),
            "description": str(package.get("summary") or package.get("description") or ""),
            "source": f"clawhub:{slug}",
            "hub": "clawhub",
            "detail_url": f"{CLAWHUB_API_BASE}/skills/{slug}",
        })
        if len(out) >= limit:
            break
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
        for fn in (_skillssh_search, _skillssh_sitemap_search, _lobehub_search, _clawhub_search):
            try:
                _add(fn(query))
            except Exception:  # noqa: BLE001
                continue
    return out
