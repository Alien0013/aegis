#!/usr/bin/env python3
"""Generate AEGIS-vs-reference comparison inventories.

The output is intentionally mechanical: file lists, subsystem counts, and a
ledger skeleton. Human rewrite decisions belong in docs, but the inventories
should be regenerated whenever either tree changes.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path


DEFAULT_REFERENCE = Path.home() / ("." + "her" + "mes") / ("her" + "mes-agent")
DEFAULT_OUT = Path("docs") / "audit" / "reference-compare"

SKIP_FULL_DIRS = {".git"}
SKIP_SOURCE_DIRS = {
    ".git",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "site-next/.next",
    "venv",
}
SOURCE_SUFFIXES = {
    ".bat",
    ".cmd",
    ".css",
    ".html",
    ".js",
    ".json",
    ".jsx",
    ".md",
    ".mjs",
    ".ps1",
    ".py",
    ".rs",
    ".sh",
    ".toml",
    ".ts",
    ".tsx",
    ".yaml",
    ".yml",
}

SUBSYSTEM_RULES: tuple[tuple[str, str], ...] = (
    ("agent/", "agent-core"),
    ("aegis/agent/", "agent-core"),
    ("her" + "mes_cli/", "cli-setup"),
    ("aegis/cli/", "cli-setup"),
    ("gateway/", "gateway"),
    ("aegis/gateway/", "gateway"),
    ("cron/", "automation"),
    ("aegis/cron.py", "automation"),
    ("aegis/kanban", "automation"),
    ("aegis/providers/", "providers"),
    ("agent/transports/", "providers"),
    ("aegis/tools/", "tools"),
    ("tools/", "tools"),
    ("aegis/mcp/", "mcp-acp"),
    ("acp_adapter/", "mcp-acp"),
    ("aegis/lsp/", "lsp"),
    ("agent/lsp/", "lsp"),
    ("aegis/builtin_skills/", "skills"),
    ("skills/", "skills"),
    ("aegis/static/", "dashboard-web"),
    ("web/", "dashboard-web"),
    ("apps/", "desktop-installers"),
    ("desktop/", "desktop-installers"),
    ("tests/", "tests"),
    ("docs/", "docs"),
    ("scripts/", "scripts-install"),
)


@dataclass(frozen=True)
class FileRow:
    rel: str
    size: int
    lines: int
    suffix: str
    subsystem: str
    sha256: str
    text: bool


def _is_under_skipped_dir(rel: str, skipped: set[str]) -> bool:
    parts = rel.split("/")
    for idx, part in enumerate(parts):
        candidate = "/".join(parts[: idx + 1])
        if candidate in skipped or part in skipped or part.endswith(".egg-info"):
            return True
    return False


def _subsystem(rel: str) -> str:
    for prefix, name in SUBSYSTEM_RULES:
        if rel == prefix.rstrip("/") or rel.startswith(prefix):
            return name
    if "/" not in rel:
        return "top-level"
    return rel.split("/", 1)[0]


def _read_file(path: Path) -> tuple[bytes, bool, int]:
    data = path.read_bytes()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return data, False, 0
    line_count = text.count("\n") + (0 if not text or text.endswith("\n") else 1)
    return data, True, line_count


def inventory(root: Path, *, source_only: bool) -> list[FileRow]:
    root = root.resolve()
    skipped = SKIP_SOURCE_DIRS if source_only else SKIP_FULL_DIRS
    rows: list[FileRow] = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root).as_posix()
        if _is_under_skipped_dir(rel, skipped):
            continue
        suffix = path.suffix.lower()
        if source_only and suffix not in SOURCE_SUFFIXES:
            continue
        try:
            data, is_text, lines = _read_file(path)
        except OSError:
            continue
        rows.append(
            FileRow(
                rel=rel,
                size=len(data),
                lines=lines,
                suffix=suffix,
                subsystem=_subsystem(rel),
                sha256=hashlib.sha256(data).hexdigest(),
                text=is_text,
            )
        )
    return rows


def write_tsv(path: Path, rows: list[FileRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(["rel", "subsystem", "suffix", "text", "bytes", "lines", "sha256"])
        for row in rows:
            writer.writerow([row.rel, row.subsystem, row.suffix, int(row.text), row.size, row.lines, row.sha256])


def summarize(rows: list[FileRow]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = defaultdict(lambda: {"files": 0, "bytes": 0, "lines": 0})
    for row in rows:
        bucket = out[row.subsystem]
        bucket["files"] += 1
        bucket["bytes"] += row.size
        bucket["lines"] += row.lines
    return dict(sorted(out.items()))


def _markdown_table(headers: list[str], rows: list[list[object]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(lines)


def write_summary(path: Path, *, aegis_rows: list[FileRow], reference_rows: list[FileRow]) -> None:
    aegis = summarize(aegis_rows)
    reference = summarize(reference_rows)
    subsystems = sorted(set(aegis) | set(reference))
    rows: list[list[object]] = []
    for name in subsystems:
        a = aegis.get(name, {"files": 0, "lines": 0, "bytes": 0})
        r = reference.get(name, {"files": 0, "lines": 0, "bytes": 0})
        rows.append([name, a["files"], a["lines"], r["files"], r["lines"]])
    text = "\n".join(
        [
            "# AEGIS vs Reference Inventory Summary",
            "",
            "Generated mechanically by `scripts/audit_reference_compare.py`.",
            "",
            _markdown_table(["Subsystem", "AEGIS Files", "AEGIS LOC", "Reference Files", "Reference LOC"], rows),
            "",
        ]
    )
    path.write_text(text, encoding="utf-8")


def write_pairing(path: Path, *, aegis_rows: list[FileRow], reference_rows: list[FileRow]) -> None:
    reference_by_name: dict[str, list[FileRow]] = defaultdict(list)
    for row in reference_rows:
        reference_by_name[Path(row.rel).name].append(row)
    rows: list[list[object]] = []
    for row in aegis_rows:
        matches = reference_by_name.get(Path(row.rel).name, [])
        if not matches:
            rows.append([row.rel, row.subsystem, "", "", "no basename match"])
            continue
        preview = ", ".join(match.rel for match in matches[:3])
        extra = "" if len(matches) <= 3 else f" +{len(matches) - 3} more"
        rows.append([row.rel, row.subsystem, preview + extra, len(matches), "basename match"])
    text = "\n".join(
        [
            "# File Pairing Ledger",
            "",
            "This is a navigation aid, not proof of copied code. It pairs files by",
            "basename so each AEGIS area can be compared against likely reference",
            "areas before any rewrite.",
            "",
            _markdown_table(["AEGIS File", "Subsystem", "Reference Candidate(s)", "Count", "Reason"], rows),
            "",
        ]
    )
    path.write_text(text, encoding="utf-8")


def write_rewrite_ledger(path: Path, *, aegis_rows: list[FileRow]) -> None:
    rows = [[row.rel, row.subsystem, "not-started", "", ""] for row in aegis_rows]
    text = "\n".join(
        [
            "# AEGIS Rewrite Ledger",
            "",
            "Status values: `not-started`, `read`, `compared`, `rewritten`,",
            "`tested`, `deferred`, `kept-as-is`.",
            "",
            _markdown_table(["AEGIS File", "Subsystem", "Status", "Reference Files Read", "Notes"], rows),
            "",
        ]
    )
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--aegis-root", type=Path, default=Path.cwd())
    parser.add_argument("--reference-root", type=Path, default=DEFAULT_REFERENCE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    aegis_root = args.aegis_root.resolve()
    reference_root = args.reference_root.resolve()
    out = args.out.resolve()
    if not aegis_root.is_dir():
        parser.error(f"AEGIS root not found: {aegis_root}")
    if not reference_root.is_dir():
        parser.error(f"reference root not found: {reference_root}")

    aegis_all = inventory(aegis_root, source_only=False)
    reference_all = inventory(reference_root, source_only=False)
    aegis_source = inventory(aegis_root, source_only=True)
    reference_source = inventory(reference_root, source_only=True)

    write_tsv(out / "aegis-all-files.tsv", aegis_all)
    write_tsv(out / "reference-all-files.tsv", reference_all)
    write_tsv(out / "aegis-source-files.tsv", aegis_source)
    write_tsv(out / "reference-source-files.tsv", reference_source)
    write_summary(out / "inventory-summary.md", aegis_rows=aegis_source, reference_rows=reference_source)
    write_pairing(out / "file-pairing-ledger.md", aegis_rows=aegis_source, reference_rows=reference_source)
    write_rewrite_ledger(out / "rewrite-ledger.md", aegis_rows=aegis_source)

    print(f"wrote comparison inventory to {out}")
    print(f"AEGIS source files: {len(aegis_source)}")
    print(f"Reference source files: {len(reference_source)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
