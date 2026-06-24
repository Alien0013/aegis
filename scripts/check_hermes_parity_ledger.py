#!/usr/bin/env python3
"""Validate the AEGIS -> Hermes parity ledger.

The code-to-code map is the no-missing-code source of truth. The ledger records
the working status for each mapped AEGIS file so parity work can move phase by
phase without losing rows.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path


MAP_COLUMNS = [
    "aegis_path",
    "subsystem",
    "aegis_lines",
    "hermes_counterpart",
    "hermes_lines",
    "match_reason",
    "parity_action",
]
LEDGER_COLUMNS = ["aegis_path", "subsystem", "phase", "status", "evidence", "notes"]
ALLOWED_STATUSES = {"pending", "partial", "complete", "blocked", "not-needed-aegis-specific"}
FINAL_STATUSES = {"complete", "not-needed-aegis-specific"}
DEFAULT_EXTERNAL_MAP = Path("/home/alienai/AEGIS_Hermes_Code_To_Code_Map.csv")


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_map_path(root: Path) -> Path:
    repo_copy = root / "docs" / "hermes-code-map.csv"
    if repo_copy.exists():
        return repo_copy
    return DEFAULT_EXTERNAL_MAP


def load_csv(path: Path, required_columns: list[str]) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(str(path))
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        fieldnames = reader.fieldnames or []
        missing = [col for col in required_columns if col not in fieldnames]
        if missing:
            raise ValueError(f"{path} is missing columns: {', '.join(missing)}")
        return [{key: (value or "") for key, value in row.items()} for row in reader]


def write_csv(path: Path, columns: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({col: row.get(col, "") for col in columns})


def infer_phase(row: dict[str, str]) -> str:
    path = row["aegis_path"]
    subsystem = row["subsystem"]
    if path in {"aegis/agent/context.py", "aegis/agent/agent.py", "aegis/agent/loop.py"}:
        return "phase-1-prompt"
    if path in {"aegis/tracing.py", "aegis/runs.py", "aegis/trajectory.py"}:
        return "phase-2-trace"
    if path.startswith("aegis/tools/") or subsystem == "tools":
        return "phase-3-tools"
    if path.startswith("aegis/providers/") or subsystem == "providers":
        return "phase-4-providers"
    if subsystem in {"cli", "docs"} or path.startswith("docs/") or path == "README.md":
        return "phase-5-docs"
    if path in {"aegis/session.py", "aegis/tools/recall.py", "aegis/session_checks.py"}:
        return "phase-6-sessions"
    if path.startswith("aegis/gateway/") or path == "aegis/webhook.py" or subsystem == "gateway":
        return "phase-7-gateway"
    if any(path.startswith(prefix) for prefix in ("aegis/cron", "aegis/background", "aegis/kanban")):
        return "phase-8-automation"
    if any(path.startswith(prefix) for prefix in ("aegis/memory", "aegis/skills", "aegis/curator", "aegis/learn")):
        return "phase-9-memory-skills"
    if path.startswith(("aegis/mcp/", "aegis/acp.py", "aegis/plugins.py")) or subsystem == "mcp":
        return "phase-10-extensions"
    if any(path.startswith(prefix) for prefix in ("aegis/redact", "aegis/security", "aegis/net_safety")):
        return "phase-11-security"
    if subsystem in {"dashboard-ui", "desktop"} or path.startswith(("web/", "desktop/")):
        return "phase-12-product"
    if subsystem in {"ci", "scripts", "repo-root"} or path.startswith((".github/", "scripts/")):
        return "phase-13-release"
    if subsystem == "site":
        return "phase-13-release"
    if subsystem == "tests" or path.startswith("tests/"):
        return "phase-tests"
    if subsystem == "skills-bundled":
        return "phase-9-memory-skills"
    return "phase-triage"


def default_status(row: dict[str, str]) -> tuple[str, str, str]:
    path = row["aegis_path"]
    if path.startswith("site-next/.next/"):
        return (
            "not-needed-aegis-specific",
            "Generated Next.js build output; tracked by release/build verification, not Hermes source parity.",
            "Generated artifact row accounted for in Phase 0.",
        )
    return "pending", "", "Awaiting phase implementation and evidence."


def sync_ledger(map_rows: list[dict[str, str]], ledger_rows: list[dict[str, str]] | None) -> list[dict[str, str]]:
    old_by_path = {row["aegis_path"]: row for row in ledger_rows or []}
    synced: list[dict[str, str]] = []
    for map_row in map_rows:
        old = old_by_path.get(map_row["aegis_path"], {})
        status, evidence, notes = default_status(map_row)
        synced.append(
            {
                "aegis_path": map_row["aegis_path"],
                "subsystem": map_row["subsystem"],
                "phase": old.get("phase") or infer_phase(map_row),
                "status": old.get("status") or status,
                "evidence": old.get("evidence") or evidence,
                "notes": old.get("notes") or notes,
            }
        )
    return synced


def duplicates(values: list[str]) -> list[str]:
    counts = Counter(values)
    return sorted(value for value, count in counts.items() if count > 1)


def validate(
    map_rows: list[dict[str, str]],
    ledger_rows: list[dict[str, str]],
    *,
    final: bool = False,
) -> tuple[list[str], dict[str, object]]:
    errors: list[str] = []
    map_paths = [row["aegis_path"] for row in map_rows]
    ledger_paths = [row["aegis_path"] for row in ledger_rows]

    for value in duplicates(map_paths):
        errors.append(f"duplicate code-map row: {value}")
    for value in duplicates(ledger_paths):
        errors.append(f"duplicate ledger row: {value}")

    map_set = set(map_paths)
    ledger_set = set(ledger_paths)
    for value in sorted(map_set - ledger_set):
        errors.append(f"missing ledger row: {value}")
    for value in sorted(ledger_set - map_set):
        errors.append(f"ledger row has no code-map row: {value}")

    status_counts: Counter[str] = Counter()
    subsystem_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for row in ledger_rows:
        status = (row.get("status") or "").strip()
        status_counts[status] += 1
        subsystem_counts[row.get("subsystem", "")][status] += 1
        if status not in ALLOWED_STATUSES:
            errors.append(f"{row.get('aegis_path')}: invalid status {status!r}")
            continue
        if status == "not-needed-aegis-specific" and not row.get("notes", "").strip():
            errors.append(f"{row.get('aegis_path')}: not-needed rows require notes")
        if status == "complete" and final and not row.get("evidence", "").strip():
            errors.append(f"{row.get('aegis_path')}: complete rows require evidence in --final mode")
        if final and status not in FINAL_STATUSES:
            errors.append(f"{row.get('aegis_path')}: status {status!r} is not allowed in --final mode")

    summary = {
        "rows": len(ledger_rows),
        "statuses": dict(sorted(status_counts.items())),
        "subsystems": {name: dict(sorted(counts.items())) for name, counts in sorted(subsystem_counts.items())},
    }
    return errors, summary


def print_summary(summary: dict[str, object], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(summary, indent=2, sort_keys=True))
        return
    print(f"Parity ledger rows: {summary['rows']}")
    print("Statuses:")
    for status, count in summary["statuses"].items():  # type: ignore[index,union-attr]
        print(f"  {status or '(blank)':<28} {count}")
    print("Subsystems:")
    for subsystem, counts in summary["subsystems"].items():  # type: ignore[index,union-attr]
        parts = ", ".join(f"{status or '(blank)'}={count}" for status, count in counts.items())
        print(f"  {subsystem:<18} {parts}")


def build_parser() -> argparse.ArgumentParser:
    root = repo_root()
    parser = argparse.ArgumentParser(description="Validate AEGIS/Hermes parity ledger coverage")
    parser.add_argument("--map", dest="map_path", default=str(default_map_path(root)), help="code-to-code CSV path")
    parser.add_argument(
        "--ledger",
        dest="ledger_path",
        default=str(root / "docs" / "hermes-parity-ledger.csv"),
        help="parity ledger CSV path",
    )
    parser.add_argument("--sync", action="store_true", help="create/update ledger rows from the code map")
    parser.add_argument("--final", action="store_true", help="require final complete/not-needed statuses")
    parser.add_argument("--json", action="store_true", help="print summary as JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    map_path = Path(args.map_path)
    ledger_path = Path(args.ledger_path)

    try:
        map_rows = load_csv(map_path, MAP_COLUMNS)
        ledger_rows: list[dict[str, str]] = []
        if ledger_path.exists():
            ledger_rows = load_csv(ledger_path, LEDGER_COLUMNS)
        elif not args.sync:
            raise FileNotFoundError(str(ledger_path))
        if args.sync:
            ledger_rows = sync_ledger(map_rows, ledger_rows)
            write_csv(ledger_path, LEDGER_COLUMNS, ledger_rows)
    except (FileNotFoundError, ValueError) as exc:
        print(f"parity ledger error: {exc}", file=sys.stderr)
        return 2

    errors, summary = validate(map_rows, ledger_rows, final=args.final)
    print_summary(summary, as_json=args.json)
    if errors:
        if args.json:
            print(json.dumps({"errors": errors}, indent=2, sort_keys=True), file=sys.stderr)
        else:
            print("Errors:", file=sys.stderr)
            for error in errors[:50]:
                print(f"  - {error}", file=sys.stderr)
            if len(errors) > 50:
                print(f"  ... {len(errors) - 50} more", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
