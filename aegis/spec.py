"""Spec-driven development — a persistent requirements → design → tasks artifact.

`/architect` plans then implements in one shot, but the plan evaporates. A *spec*
is a durable plan that survives across sessions and lives in the repo. Each spec is
a markdown file under ``<workspace>/.aegis/specs/<slug>/spec.md`` with three
sections — Requirements, Design, and a Tasks checklist — plus a small ``meta.json``
(title, status, timestamps). Because tasks are GitHub-style ``- [ ]`` / ``- [x]``
checkboxes, progress is just text the agent (and you) can read and tick off.

This module owns the filesystem CRUD only; drafting a spec from a one-line goal is a
provider call wired in the REPL (`/spec new`), the same way `/architect` works.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .util import atomic_write, now_iso, read_text

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_TASK_RE = re.compile(r"^\s*[-*]\s+\[( |x|X)\]\s+(.*\S)\s*$")

SECTIONS = ("Requirements", "Design", "Tasks")
STATUSES = ("draft", "approved", "in_progress", "done")


def slugify(title: str) -> str:
    s = _SLUG_RE.sub("-", title.strip().lower()).strip("-")
    return s or "spec"


@dataclass
class Spec:
    slug: str
    title: str
    body: str
    meta: dict = field(default_factory=dict)
    path: Path | None = None

    @property
    def status(self) -> str:
        return str(self.meta.get("status", "draft"))

    def tasks(self) -> list[tuple[bool, str]]:
        out: list[tuple[bool, str]] = []
        in_tasks = False
        for line in self.body.splitlines():
            if line.lstrip().startswith("#"):
                in_tasks = "task" in line.lower()
                continue
            m = _TASK_RE.match(line)
            if m and in_tasks:
                out.append((m.group(1).lower() == "x", m.group(2)))
        return out

    def progress(self) -> tuple[int, int]:
        tasks = self.tasks()
        return sum(1 for done, _ in tasks if done), len(tasks)


def _skeleton(title: str) -> str:
    return (f"# {title}\n\n"
            "## Requirements\n\n- \n\n"
            "## Design\n\n- \n\n"
            "## Tasks\n\n- [ ] \n")


class SpecStore:
    """Specs under ``<root>/<spec.dir>`` (default ``.aegis/specs``)."""

    def __init__(self, root: str | Path | None = None, *, subdir: str = ".aegis/specs"):
        base = Path(root).expanduser() if root else Path.cwd()
        self.dir = base / subdir
        self.subdir = subdir
        self.base = base

    @classmethod
    def from_config(cls, config, cwd: str | Path | None = None) -> "SpecStore":
        subdir = str((config.get("spec.dir", "") if config else "") or ".aegis/specs")
        return cls(cwd, subdir=subdir)

    def _spec_dir(self, slug: str) -> Path:
        return self.dir / slug

    def create(self, title: str, body: str | None = None, *, status: str = "draft") -> Spec:
        slug = slugify(title)
        d = self._spec_dir(slug)
        d.mkdir(parents=True, exist_ok=True)
        meta = {"slug": slug, "title": title, "status": status,
                "created_at": now_iso(), "updated_at": now_iso()}
        spec = Spec(slug=slug, title=title, body=body or _skeleton(title), meta=meta, path=d)
        self.save(spec)
        return spec

    def save(self, spec: Spec) -> Spec:
        d = self._spec_dir(spec.slug)
        d.mkdir(parents=True, exist_ok=True)
        spec.meta["updated_at"] = now_iso()
        spec.meta.setdefault("slug", spec.slug)
        spec.meta.setdefault("title", spec.title)
        atomic_write(d / "spec.md", spec.body)
        atomic_write(d / "meta.json", json.dumps(spec.meta, indent=2))
        spec.path = d
        return spec

    def get(self, slug: str) -> Spec | None:
        slug = slugify(slug)
        d = self._spec_dir(slug)
        body = read_text(d / "spec.md")
        if body is None:
            return None
        try:
            meta = json.loads(read_text(d / "meta.json") or "{}")
        except json.JSONDecodeError:
            meta = {}
        title = str(meta.get("title") or _first_heading(body) or slug)
        return Spec(slug=slug, title=title, body=body, meta=meta, path=d)

    def list(self) -> list[Spec]:
        if not self.dir.exists():
            return []
        specs = [s for s in (self.get(p.name) for p in sorted(self.dir.iterdir())
                             if p.is_dir()) if s]
        return specs

    def set_status(self, slug: str, status: str) -> Spec | None:
        spec = self.get(slug)
        if spec is None:
            return None
        spec.meta["status"] = status
        return self.save(spec)

    def set_body(self, slug: str, body: str) -> Spec | None:
        spec = self.get(slug)
        if spec is None:
            return None
        spec.body = body
        return self.save(spec)

    def mark_task(self, slug: str, index: int, done: bool = True) -> Spec | None:
        """Tick (or untick) the Nth task checkbox (0-based across the Tasks section)."""
        spec = self.get(slug)
        if spec is None:
            return None
        lines = spec.body.splitlines()
        in_tasks = False
        seen = -1
        for i, line in enumerate(lines):
            if line.lstrip().startswith("#"):
                in_tasks = "task" in line.lower()
                continue
            if in_tasks and _TASK_RE.match(line):
                seen += 1
                if seen == index:
                    mark = "x" if done else " "
                    lines[i] = re.sub(r"\[( |x|X)\]", f"[{mark}]", line, count=1)
                    break
        spec.body = "\n".join(lines) + ("\n" if spec.body.endswith("\n") else "")
        return self.save(spec)


def _first_heading(body: str) -> str:
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def implementation_prompt(spec: Spec) -> str:
    """The execution directive fed back into the agent for `/spec implement`."""
    done, total = spec.progress()
    return (
        "<system-reminder>Implement the spec below on the real workspace: make the real "
        "edits, run the real verification, and tick off each task in the spec's Tasks "
        "checklist (edit .aegis/specs/{slug}/spec.md, ` - [ ]` → ` - [x]`) as you complete it. "
        "Work task by task; do the actual work, don't just restate the plan."
        "</system-reminder>\n\n"
        f"SPEC: {spec.title}  ({done}/{total} tasks done, status={spec.status})\n\n"
        f"{spec.body}"
    ).replace("{slug}", spec.slug)


def cmd_spec(args, config) -> int:
    """`aegis spec [list|show] [slug]`."""
    store = SpecStore.from_config(config)
    action = getattr(args, "action", "list") or "list"
    if action == "show":
        slug = getattr(args, "slug", None)
        if not slug:
            print("usage: aegis spec show <slug>")
            return 1
        spec = store.get(slug)
        if not spec:
            print(f"no spec: {slug}")
            return 1
        print(spec.body)
        return 0
    specs = store.list()
    for s in specs:
        done, total = s.progress()
        print(f"  {s.slug:<28} [{s.status}]  {done}/{total} tasks")
    if not specs:
        print("(no specs — create one with /spec new <title> in chat)")
    return 0
