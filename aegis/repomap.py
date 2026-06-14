"""Repository map: a ranked, budgeted outline of a codebase's structure.

This is AEGIS's answer to the Aider/Cursor "repo map" — a bird's-eye index of the
most important files and the symbols they define, so the agent can orient on an
unfamiliar codebase without reading every file. No tree-sitter dependency: Python
files are parsed with the stdlib :mod:`ast`; other languages use small, anchored
regexes for their top-level definitions.

Ranking is a cheap approximation of Aider's PageRank: a symbol that is *referenced*
in many places is important, so files that define widely-referenced symbols float to
the top. The rendered map is character-budgeted so it always fits in context.
"""

from __future__ import annotations

import ast
import re
import subprocess
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

# Extensions we attempt to outline, mapped to a coarse language label.
_LANGS = {
    ".py": "python", ".pyi": "python",
    ".js": "js", ".jsx": "js", ".mjs": "js", ".cjs": "js",
    ".ts": "ts", ".tsx": "ts",
    ".go": "go", ".rs": "rust", ".rb": "ruby", ".java": "java",
    ".c": "c", ".h": "c", ".cc": "cpp", ".cpp": "cpp", ".hpp": "cpp",
    ".cs": "csharp", ".php": "php", ".swift": "swift", ".kt": "kotlin",
    ".scala": "scala", ".sh": "shell", ".lua": "lua",
}

_IGNORE_DIRS = frozenset({
    ".git", "node_modules", "__pycache__", ".venv", "venv", "env", "dist",
    "build", ".next", ".nuxt", "target", "vendor", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", "coverage", ".tox", ".idea", ".vscode", "web_dist", "static",
})

_MAX_FILES = 4000
_MAX_FILE_BYTES = 1_000_000
_IDENT_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")

# Anchored top-level definition patterns per language. Each yields (kind, name).
_REGEX_DEFS = {
    "js": [
        (re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)"), "fn"),
        (re.compile(r"^\s*(?:export\s+)?class\s+([A-Za-z_$][\w$]*)"), "class"),
        (re.compile(r"^\s*(?:export\s+)?(?:const|let)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\("), "fn"),
    ],
    "go": [
        (re.compile(r"^func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)"), "fn"),
        (re.compile(r"^type\s+([A-Za-z_]\w*)\s"), "type"),
    ],
    "rust": [
        (re.compile(r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+([A-Za-z_]\w*)"), "fn"),
        (re.compile(r"^\s*(?:pub\s+)?(?:struct|enum|trait)\s+([A-Za-z_]\w*)"), "type"),
    ],
    "ruby": [
        (re.compile(r"^\s*def\s+([A-Za-z_][\w?!]*)"), "def"),
        (re.compile(r"^\s*(?:class|module)\s+([A-Za-z_]\w*)"), "class"),
    ],
    "java": [(re.compile(r"^\s*(?:public|private|protected)?\s*(?:static\s+)?(?:final\s+)?(?:class|interface|enum)\s+([A-Za-z_]\w*)"), "class")],
    "ruby_like": [],
}
# ts shares js patterns; c-family + others get a generic function/type sweep.
_REGEX_DEFS["ts"] = _REGEX_DEFS["js"]
for _l in ("c", "cpp", "csharp", "php", "swift", "kotlin", "scala", "lua", "shell"):
    _REGEX_DEFS.setdefault(_l, [
        (re.compile(r"^\s*(?:public|private|static|func|def|sub|function|local)\b[^\n(]*?\b([A-Za-z_]\w*)\s*\("), "fn"),
    ])


@dataclass
class Symbol:
    kind: str
    name: str
    line: int


def list_source_files(root: Path) -> list[Path]:
    """Source files under *root*. Uses ``git ls-files`` when available so .gitignore is
    respected exactly; otherwise walks the tree skipping the usual heavy directories."""
    root = root.resolve()
    files: list[Path] = []
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--cached", "--others", "--exclude-standard"],
            capture_output=True, text=True, timeout=10, check=True,
        ).stdout
        for line in out.splitlines():
            p = root / line
            if p.suffix in _LANGS and p.is_file():
                files.append(p)
        if files:
            return files[:_MAX_FILES]
    except (subprocess.SubprocessError, OSError):
        pass
    for p in root.rglob("*"):
        if p.is_dir():
            continue
        if any(part in _IGNORE_DIRS for part in p.parts):
            continue
        if p.suffix in _LANGS:
            files.append(p)
        if len(files) >= _MAX_FILES:
            break
    return files


def _python_symbols(text: str) -> list[Symbol]:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return []
    syms: list[Symbol] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            syms.append(Symbol("def", node.name, node.lineno))
        elif isinstance(node, ast.ClassDef):
            syms.append(Symbol("class", node.name, node.lineno))
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and not child.name.startswith("_"):
                    syms.append(Symbol("method", f"{node.name}.{child.name}", child.lineno))
    return syms


def _regex_symbols(lang: str, text: str) -> list[Symbol]:
    patterns = _REGEX_DEFS.get(lang, [])
    if not patterns:
        return []
    syms: list[Symbol] = []
    seen: set[tuple[str, str]] = set()
    for i, line in enumerate(text.splitlines(), 1):
        if len(line) > 400:
            continue
        for rx, kind in patterns:
            m = rx.match(line)
            if m:
                name = m.group(1)
                key = (kind, name)
                if key not in seen:
                    seen.add(key)
                    syms.append(Symbol(kind, name, i))
                break
    return syms


def extract_symbols(path: Path, text: str) -> list[Symbol]:
    lang = _LANGS.get(path.suffix, "")
    if lang == "python":
        return _python_symbols(text)
    return _regex_symbols(lang, text)


def _read(path: Path) -> str:
    try:
        if path.stat().st_size > _MAX_FILE_BYTES:
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


@dataclass
class FileEntry:
    rel: str
    symbols: list[Symbol]
    score: float


def build_index(root: Path, *, query: str = "") -> list[FileEntry]:
    """Parse every source file, then rank files by how often the symbols they define
    are referenced across the whole tree (a cheap stand-in for Aider's PageRank)."""
    root = root.resolve()
    files = list_source_files(root)
    parsed: list[tuple[Path, str, list[Symbol]]] = []
    ref_counts: Counter[str] = Counter()
    for path in files:
        text = _read(path)
        if not text:
            continue
        syms = extract_symbols(path, text)
        parsed.append((path, text, syms))
        ref_counts.update(_IDENT_RE.findall(text))

    q = query.strip().lower()
    entries: list[FileEntry] = []
    for path, _text, syms in parsed:
        if not syms:
            continue
        # A symbol's weight is how often its bare name appears elsewhere; subtract 1 so a
        # symbol referenced only at its own definition contributes nothing.
        score = 0.0
        for s in syms:
            base = s.name.split(".")[-1]
            score += max(0, ref_counts.get(base, 0) - 1)
        try:
            rel = str(path.relative_to(root))
        except ValueError:
            rel = str(path)
        if q and q not in rel.lower() and not any(q in s.name.lower() for s in syms):
            continue
        entries.append(FileEntry(rel=rel, symbols=syms, score=score))
    entries.sort(key=lambda e: (-e.score, e.rel))
    return entries


def render_map(root: Path, *, query: str = "", max_chars: int = 6000,
               max_symbols_per_file: int = 12) -> str:
    """A compact, character-budgeted outline of the most important files + symbols."""
    entries = build_index(root, query=query)
    if not entries:
        return "(no source files found to map)"
    lines: list[str] = [f"Repo map — {len(entries)} files" + (f" matching '{query}'" if query else "")
                        + " (ranked by reference weight):", ""]
    used = sum(len(line) + 1 for line in lines)
    shown = 0
    for e in entries:
        header = e.rel
        block = [header]
        for s in e.symbols[:max_symbols_per_file]:
            block.append(f"  {s.kind:<7} {s.name}  :{s.line}")
        extra = len(e.symbols) - max_symbols_per_file
        if extra > 0:
            block.append(f"  … +{extra} more")
        chunk = "\n".join(block) + "\n"
        if used + len(chunk) > max_chars and shown > 0:
            lines.append(f"… +{len(entries) - shown} more files (narrow with a query)")
            break
        lines.append(chunk.rstrip("\n"))
        used += len(chunk)
        shown += 1
    return "\n".join(lines)


def find_symbol(root: Path, name: str) -> list[tuple[str, int, str]]:
    """Locate where *name* is defined. Returns (rel_path, line, kind) for each match."""
    root = root.resolve()
    target = name.strip().lower()
    hits: list[tuple[str, int, str]] = []
    for path in list_source_files(root):
        text = _read(path)
        if not text or target.split(".")[-1] not in text.lower():
            continue
        for s in extract_symbols(path, text):
            if s.name.lower() == target or s.name.split(".")[-1].lower() == target:
                try:
                    rel = str(path.relative_to(root))
                except ValueError:
                    rel = str(path)
                hits.append((rel, s.line, s.kind))
    return hits
