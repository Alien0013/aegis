#!/usr/bin/env python3
"""Generate/check the AEGIS source coverage map used by the parity ledger."""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path


COLUMNS = [
    "aegis_path",
    "subsystem",
    "aegis_lines",
    "reference_counterpart",
    "reference_lines",
    "match_reason",
    "parity_action",
]
INCLUDE_SUFFIXES = {
    ".cjs",
    ".css",
    ".csv",
    ".html",
    ".js",
    ".json",
    ".md",
    ".mjs",
    ".ps1",
    ".py",
    ".sh",
    ".svg",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}
EXCLUDED_PARTS = {
    ".git",
    ".mypy_cache",
    ".next",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "audit-artifacts",
    "build",
    "dist",
    "node_modules",
}
EXCLUDED_PREFIXES = (
    "aegis_agent_harness.egg-info/",
    "aegis/static/web_dist/",
)
SUBSYSTEM_PREFIXES = [
    (".github/", "ci"),
    ("aegis/agent/", "agent-core"),
    ("aegis/builtin_skills/", "skills-bundled"),
    ("aegis/cli/", "cli"),
    ("aegis/desktop_app/", "runtime-other"),
    ("aegis/gateway/", "gateway"),
    ("aegis/lsp/", "lsp"),
    ("aegis/mcp/", "mcp"),
    ("aegis/providers/", "providers"),
    ("aegis/tools/", "tools"),
    ("desktop/", "desktop"),
    ("docs/", "docs"),
    ("scripts/", "scripts"),
    ("site-next/", "site"),
    ("tests/", "tests"),
    ("web/src/", "dashboard-ui"),
]


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def is_source_path(path: Path, root: Path) -> bool:
    rel = path.relative_to(root).as_posix()
    if any(part in EXCLUDED_PARTS for part in path.relative_to(root).parts):
        return False
    if any(rel.startswith(prefix) for prefix in EXCLUDED_PREFIXES):
        return False
    return path.is_file() and path.suffix.lower() in INCLUDE_SUFFIXES


def infer_subsystem(rel: str) -> str:
    for prefix, subsystem in SUBSYSTEM_PREFIXES:
        if rel.startswith(prefix):
            return subsystem
    if rel.startswith("web/"):
        return "repo-root"
    if rel.startswith("aegis/"):
        return "runtime-other"
    return "repo-root"


def line_count(path: Path) -> int:
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as fh:
            return sum(1 for _ in fh)
    except OSError:
        return 0


def load_existing(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        return {row.get("aegis_path", ""): {key: value or "" for key, value in row.items()} for row in reader}


def clean_legacy_wording(value: str) -> str:
    """Normalize old comparison wording kept in generated CSV metadata."""
    legacy_brand = "AEGIS"
    return (
        value.replace(f"no close {legacy_brand} path by simple scan", "no close reference path by simple scan")
        .replace(f"{legacy_brand}-parity equivalent/tests", "parity equivalent/tests")
        .replace(f"{legacy_brand}-parity", "parity")
        .replace(f"Match {legacy_brand} UX affordance", "Match reference UX affordance")
    )


def build_rows(root: Path, existing: dict[str, dict[str, str]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for path in sorted(root.rglob("*")):
        if not is_source_path(path, root):
            continue
        rel = path.relative_to(root).as_posix()
        old = existing.get(rel, {})
        rows.append(
            {
                "aegis_path": rel,
                "subsystem": old.get("subsystem") or infer_subsystem(rel),
                "aegis_lines": str(line_count(path)),
                "reference_counterpart": clean_legacy_wording(old.get("reference_counterpart", "")),
                "reference_lines": old.get("reference_lines", ""),
                "match_reason": clean_legacy_wording(old.get("match_reason") or "current AEGIS source path"),
                "parity_action": clean_legacy_wording(
                    old.get("parity_action") or "Track source coverage in parity ledger."
                ),
            }
        )
    return rows


def write_rows(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as fh:
        return [{key: value or "" for key, value in row.items()} for row in csv.DictReader(fh)]


def diff_rows(current: list[dict[str, str]], expected: list[dict[str, str]]) -> list[str]:
    current_by_path = {row["aegis_path"]: row for row in current}
    expected_by_path = {row["aegis_path"]: row for row in expected}
    errors: list[str] = []
    for path in sorted(set(expected_by_path) - set(current_by_path)):
        errors.append(f"missing code-map row: {path}")
    for path in sorted(set(current_by_path) - set(expected_by_path)):
        errors.append(f"stale/generated code-map row: {path}")
    for path in sorted(set(current_by_path) & set(expected_by_path)):
        current_row = current_by_path[path]
        expected_row = expected_by_path[path]
        for field in ("subsystem", "aegis_lines"):
            if current_row.get(field, "") != expected_row.get(field, ""):
                errors.append(
                    f"{path}: {field} is {current_row.get(field, '')!r}, expected {expected_row.get(field, '')!r}"
                )
                break
    return errors


def build_parser() -> argparse.ArgumentParser:
    root = repo_root()
    parser = argparse.ArgumentParser(description="Generate/check docs/aegis-code-map.csv from current source files")
    parser.add_argument("--map", default=str(root / "docs" / "aegis-code-map.csv"), help="code-map CSV path")
    parser.add_argument("--check", action="store_true", help="fail if the code map is stale")
    parser.add_argument("--write", action="store_true", help="write the generated code map")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = repo_root()
    map_path = Path(args.map)
    expected = build_rows(root, load_existing(map_path))
    if args.write:
        write_rows(map_path, expected)
        print(f"wrote {len(expected)} code-map rows -> {map_path}")
        return 0
    if args.check:
        if not map_path.exists():
            print(f"code-map missing: {map_path}", file=sys.stderr)
            return 2
        errors = diff_rows(read_rows(map_path), expected)
        if errors:
            print("code-map drift:", file=sys.stderr)
            for error in errors[:50]:
                print(f"  - {error}", file=sys.stderr)
            if len(errors) > 50:
                print(f"  ... {len(errors) - 50} more", file=sys.stderr)
            return 1
        print(f"code-map rows verified: {len(expected)}")
        return 0
    print(f"code-map rows: {len(expected)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
