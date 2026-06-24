from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_aegis_parity_ledger.py"
MAP_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "generate_aegis_code_map.py"


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    columns = [
        "aegis_path",
        "subsystem",
        "aegis_lines",
        "reference_counterpart",
        "reference_lines",
        "match_reason",
        "parity_action",
    ]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, check=False)


def _run_map_script(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(MAP_SCRIPT), *args], capture_output=True, text=True, check=False)


def _write_closed_ledger(code_map: Path, ledger: Path, rows: list[dict[str, str]]) -> None:
    _write_csv(code_map, rows)
    with ledger.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["aegis_path", "subsystem", "phase", "status", "evidence", "notes"])
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "aegis_path": row["aegis_path"],
                    "subsystem": row["subsystem"],
                    "phase": "phase-test",
                    "status": "complete",
                    "evidence": f"focused evidence for {row['aegis_path']}",
                    "notes": "closed for test",
                }
            )


def _write_matrix(path: Path, status: str, *, gap: str = "closed by focused evidence") -> None:
    path.write_text(
        "# Matrix\n\n"
        "## Runtime\n\n"
        "| Capability | AEGIS status | Evidence / likely files | Gap to close |\n"
        "|---|---:|---|---|\n"
        f"| Mid-turn steering | {status} | tests/test_surface.py | {gap} |\n",
        encoding="utf-8",
    )


def test_parity_ledger_sync_creates_accounted_rows(tmp_path):
    code_map = tmp_path / "map.csv"
    ledger = tmp_path / "ledger.csv"
    _write_csv(
        code_map,
        [
            {
                "aegis_path": "aegis/agent/context.py",
                "subsystem": "agent-core",
                "aegis_lines": "10",
                "reference_counterpart": "agent/prompt_builder.py",
                "reference_lines": "20",
                "match_reason": "manual architecture map",
                "parity_action": "Align behavior contract",
            },
            {
                "aegis_path": "site-next/.next/server/app/page.js",
                "subsystem": "site",
                "aegis_lines": "1",
                "reference_counterpart": "",
                "reference_lines": "",
                "match_reason": "generated",
                "parity_action": "Decide",
            },
        ],
    )

    result = _run("--map", str(code_map), "--ledger", str(ledger), "--sync")

    assert result.returncode == 0, result.stderr
    assert "Parity ledger rows: 2" in result.stdout
    rows = list(csv.DictReader(ledger.open(newline="", encoding="utf-8")))
    assert rows[0]["aegis_path"] == "aegis/agent/context.py"
    assert rows[0]["phase"] == "phase-1-prompt"
    assert rows[0]["status"] == "pending"
    assert rows[1]["status"] == "not-needed-aegis-specific"
    assert rows[1]["notes"]


def test_parity_ledger_reports_missing_rows(tmp_path):
    code_map = tmp_path / "map.csv"
    ledger = tmp_path / "ledger.csv"
    _write_csv(
        code_map,
        [
            {
                "aegis_path": "aegis/server.py",
                "subsystem": "runtime-other",
                "aegis_lines": "100",
                "reference_counterpart": "gateway/platforms/api_server.py",
                "reference_lines": "",
                "match_reason": "manual architecture map",
                "parity_action": "Align",
            }
        ],
    )
    ledger.write_text("aegis_path,subsystem,phase,status,evidence,notes\n", encoding="utf-8")

    result = _run("--map", str(code_map), "--ledger", str(ledger))

    assert result.returncode == 1
    assert "missing ledger row: aegis/server.py" in result.stderr


def test_parity_ledger_final_mode_requires_closed_statuses(tmp_path):
    code_map = tmp_path / "map.csv"
    ledger = tmp_path / "ledger.csv"
    _write_csv(
        code_map,
        [
            {
                "aegis_path": "aegis/agent/context.py",
                "subsystem": "agent-core",
                "aegis_lines": "10",
                "reference_counterpart": "agent/prompt_builder.py",
                "reference_lines": "20",
                "match_reason": "manual architecture map",
                "parity_action": "Align behavior contract",
            }
        ],
    )
    ledger.write_text(
        "aegis_path,subsystem,phase,status,evidence,notes\n"
        "aegis/agent/context.py,agent-core,phase-1-prompt,pending,,not done\n",
        encoding="utf-8",
    )

    result = _run("--map", str(code_map), "--ledger", str(ledger), "--final")

    assert result.returncode == 1
    assert "not allowed in --final mode" in result.stderr


def test_parity_ledger_final_mode_rejects_unresolved_feature_matrix_rows(tmp_path):
    code_map = tmp_path / "map.csv"
    ledger = tmp_path / "ledger.csv"
    matrix = tmp_path / "matrix.md"
    rows = [
        {
            "aegis_path": "aegis/agent/context.py",
            "subsystem": "agent-core",
            "aegis_lines": "10",
            "reference_counterpart": "agent/prompt_builder.py",
            "reference_lines": "20",
            "match_reason": "manual architecture map",
            "parity_action": "Align behavior contract",
        }
    ]
    _write_closed_ledger(code_map, ledger, rows)
    _write_matrix(matrix, "Partial", gap="Verify every surface supports it consistently.")

    result = _run("--map", str(code_map), "--ledger", str(ledger), "--matrix", str(matrix), "--final")

    assert result.returncode == 1
    assert "feature matrix unresolved row" in result.stderr
    assert "Mid-turn steering" in result.stderr


def test_parity_ledger_final_mode_allows_credential_bound_feature_matrix_rows(tmp_path):
    code_map = tmp_path / "map.csv"
    ledger = tmp_path / "ledger.csv"
    matrix = tmp_path / "matrix.md"
    rows = [
        {
            "aegis_path": "aegis/gateway/channels.py",
            "subsystem": "gateway",
            "aegis_lines": "10",
            "reference_counterpart": "gateway/platforms/telegram.py",
            "reference_lines": "20",
            "match_reason": "manual architecture map",
            "parity_action": "Align behavior contract",
        }
    ]
    _write_closed_ledger(code_map, ledger, rows)
    _write_matrix(
        matrix,
        "Credential-bound",
        gap="Implementation is local; live platform smoke is credential-bound external proof.",
    )

    result = _run("--map", str(code_map), "--ledger", str(ledger), "--matrix", str(matrix), "--final")

    assert result.returncode == 0, result.stderr
    assert "Feature matrix:" in result.stdout
    assert "unresolved=0" in result.stdout


def test_code_map_generator_includes_cjs_and_excludes_generated_paths(tmp_path):
    code_map = tmp_path / "map.csv"
    code_map.write_text(
        "aegis_path,subsystem,aegis_lines,reference_counterpart,reference_lines,match_reason,parity_action\n",
        encoding="utf-8",
    )
    root = Path.cwd()
    generated = root / "site-next" / ".next" / "parity-test-generated.js"
    cjs = root / "desktop" / "electron" / "parity-test-source.cjs"
    try:
        generated.parent.mkdir(parents=True, exist_ok=True)
        generated.write_text("console.log('generated')\n", encoding="utf-8")
        cjs.write_text("module.exports = {}\n", encoding="utf-8")

        result = _run_map_script("--map", str(code_map), "--write")

        assert result.returncode == 0, result.stderr
        rows = list(csv.DictReader(code_map.open(newline="", encoding="utf-8")))
        paths = {row["aegis_path"] for row in rows}
        assert "desktop/electron/parity-test-source.cjs" in paths
        assert "site-next/.next/parity-test-generated.js" not in paths
    finally:
        generated.unlink(missing_ok=True)
        cjs.unlink(missing_ok=True)
