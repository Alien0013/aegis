from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_hermes_parity_ledger.py"


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    columns = [
        "aegis_path",
        "subsystem",
        "aegis_lines",
        "hermes_counterpart",
        "hermes_lines",
        "match_reason",
        "parity_action",
    ]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, check=False)


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
                "hermes_counterpart": "agent/prompt_builder.py",
                "hermes_lines": "20",
                "match_reason": "manual architecture map",
                "parity_action": "Align behavior contract",
            },
            {
                "aegis_path": "site-next/.next/server/app/page.js",
                "subsystem": "site",
                "aegis_lines": "1",
                "hermes_counterpart": "",
                "hermes_lines": "",
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
                "hermes_counterpart": "gateway/platforms/api_server.py",
                "hermes_lines": "",
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
                "hermes_counterpart": "agent/prompt_builder.py",
                "hermes_lines": "20",
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
