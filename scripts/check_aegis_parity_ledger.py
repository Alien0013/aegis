#!/usr/bin/env python3
"""Validate the AEGIS parity ledger.

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
    "reference_counterpart",
    "reference_lines",
    "match_reason",
    "parity_action",
]
LEDGER_COLUMNS = ["aegis_path", "subsystem", "phase", "status", "evidence", "notes"]
ALLOWED_STATUSES = {"pending", "partial", "complete", "blocked", "not-needed-aegis-specific"}
FINAL_STATUSES = {"complete", "not-needed-aegis-specific"}
DEFAULT_EXTERNAL_MAP = Path("/home/alienai/AEGIS_Code_To_Code_Map.csv")
MATRIX_UNRESOLVED_MARKERS = ("partial", "needs audit", "missing")
MATRIX_CLOSED_STATUSES = {
    "present",
    "done",
    "complete",
    "not-needed-aegis-specific",
    "out-of-scope",
    "credential-bound",
}


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_map_path(root: Path) -> Path:
    repo_copy = root / "docs" / "aegis-code-map.csv"
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
            "Generated Next.js build output; tracked by release/build verification, not runtime source parity.",
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


def _split_markdown_table_row(line: str) -> list[str]:
    raw = line.strip()
    if not raw.startswith("|") or not raw.endswith("|"):
        return []
    return [cell.strip().replace("<br>", " ") for cell in raw.strip("|").split("|")]


def _is_markdown_separator(cells: list[str]) -> bool:
    if not cells:
        return False
    return all(cell and set(cell.replace(":", "").replace("-", "").strip()) == set() for cell in cells)


def parse_feature_matrix(path: Path) -> list[dict[str, str]]:
    """Return capability rows from the feature parity markdown tables."""

    if not path.exists():
        raise FileNotFoundError(str(path))
    rows: list[dict[str, str]] = []
    current_section = ""
    headers: list[str] = []
    in_feature_table = False
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("## "):
            current_section = stripped.lstrip("#").strip()
            headers = []
            in_feature_table = False
            continue
        cells = _split_markdown_table_row(line)
        if not cells:
            headers = []
            in_feature_table = False
            continue
        normalized = [cell.lower() for cell in cells]
        if "capability" in normalized and any("status" in cell for cell in normalized):
            headers = normalized
            in_feature_table = True
            continue
        if _is_markdown_separator(cells):
            continue
        if not in_feature_table or not headers:
            continue
        try:
            capability_idx = headers.index("capability")
        except ValueError:
            continue
        status_idx = next((idx for idx, header in enumerate(headers) if "status" in header), -1)
        evidence_idx = next((idx for idx, header in enumerate(headers) if "evidence" in header), -1)
        gap_idx = next((idx for idx, header in enumerate(headers) if "gap" in header), -1)
        if status_idx < 0 or len(cells) <= max(capability_idx, status_idx):
            continue
        rows.append(
            {
                "line": str(lineno),
                "section": current_section,
                "capability": cells[capability_idx],
                "status": cells[status_idx],
                "evidence": cells[evidence_idx] if evidence_idx >= 0 and evidence_idx < len(cells) else "",
                "gap": cells[gap_idx] if gap_idx >= 0 and gap_idx < len(cells) else "",
            }
        )
    return rows


def validate_feature_matrix(path: Path, *, final: bool = False) -> tuple[list[str], dict[str, object]]:
    rows = parse_feature_matrix(path)
    errors: list[str] = []
    status_counts: Counter[str] = Counter()
    unresolved: list[dict[str, str]] = []
    closure_exceptions: list[dict[str, str]] = []

    for row in rows:
        status = row["status"].strip()
        status_key = status.lower()
        status_counts[status] += 1
        haystack = f"{status} {row['evidence']} {row['gap']}".lower()
        has_unresolved_marker = any(marker in status_key for marker in MATRIX_UNRESOLVED_MARKERS)
        is_closed = status_key in MATRIX_CLOSED_STATUSES
        has_exception = any(marker in haystack for marker in ("not-needed-aegis-specific", "out-of-scope"))
        is_credential_bound = "credential-bound" in status_key or (
            "credential-bound" in haystack and any(marker in haystack for marker in ("live", "credential", "external"))
        )
        if has_unresolved_marker and not has_exception:
            unresolved.append(row)
        elif has_exception or is_credential_bound:
            closure_exceptions.append(row)
        elif not is_closed:
            unresolved.append(row)

    if final and unresolved:
        for row in unresolved:
            errors.append(
                "feature matrix unresolved row "
                f"{row['line']}: {row['capability']} has status {row['status']!r}"
            )
    summary = {
        "rows": len(rows),
        "statuses": dict(sorted(status_counts.items())),
        "unresolved": len(unresolved),
        "closed_exceptions": len(closure_exceptions),
    }
    return errors, summary


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
    matrix = summary.get("feature_matrix")
    if isinstance(matrix, dict):
        print("Feature matrix:")
        print(f"  rows={matrix.get('rows', 0)} unresolved={matrix.get('unresolved', 0)}")
        for status, count in (matrix.get("statuses") or {}).items():
            print(f"  {status:<28} {count}")


def build_parser() -> argparse.ArgumentParser:
    root = repo_root()
    parser = argparse.ArgumentParser(description="Validate AEGIS/AEGIS parity ledger coverage")
    parser.add_argument("--map", dest="map_path", default=str(default_map_path(root)), help="code-to-code CSV path")
    parser.add_argument(
        "--ledger",
        dest="ledger_path",
        default=str(root / "docs" / "aegis-parity-ledger.csv"),
        help="parity ledger CSV path",
    )
    parser.add_argument("--sync", action="store_true", help="create/update ledger rows from the code map")
    parser.add_argument("--final", action="store_true", help="require final complete/not-needed statuses")
    parser.add_argument(
        "--matrix",
        dest="matrix_path",
        default=str(root / "docs" / "feature-parity-matrix.md"),
        help="feature parity matrix markdown path checked in --final mode",
    )
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
    if args.final:
        try:
            matrix_errors, matrix_summary = validate_feature_matrix(Path(args.matrix_path), final=True)
        except FileNotFoundError as exc:
            print(f"feature matrix error: {exc}", file=sys.stderr)
            return 2
        errors.extend(matrix_errors)
        summary["feature_matrix"] = matrix_summary
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
