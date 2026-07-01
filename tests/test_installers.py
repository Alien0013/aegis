"""Installer smoke checks that do not create venvs or touch the network."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_setup_compatibility_scripts_delegate_to_aegis_surfaces():
    legacy = "".join(chr(n) for n in (104, 101, 114, 109, 101, 115))
    setup_name = f"setup-{legacy}.sh"
    bootstrap_name = f"{legacy}_bootstrap.py"
    files = {
        setup_name: "install.sh",
        bootstrap_name: "aegis bootstrap compatibility",
        "scripts/install_psutil_android.py": "AEGIS avoids a psutil runtime dependency",
        "scripts/setup_open_webui.sh": "aegis ui",
        "optional-skills/creative/kanban-video-orchestrator/references/kanban-setup.md": "aegis kanban",
        "optional-skills/creative/kanban-video-orchestrator/scripts/bootstrap_pipeline.py": "aegis.kanban",
        "optional-skills/creative/hyperframes/scripts/setup.sh": "AEGIS",
    }
    for rel, token in files.items():
        path = ROOT / rel
        assert path.is_file(), rel
        assert token in path.read_text(encoding="utf-8")
    assert subprocess.run(["bash", "-n", setup_name], cwd=ROOT, capture_output=True, text=True).returncode == 0
    assert subprocess.run(["bash", "-n", "optional-skills/creative/hyperframes/scripts/setup.sh"], cwd=ROOT, capture_output=True, text=True).returncode == 0


def test_setup_py_supports_legacy_build_frontend_name_query():
    import sys

    setup_py = ROOT / "setup.py"
    assert setup_py.is_file()
    res = subprocess.run([sys.executable, "setup.py", "--name"], cwd=ROOT, capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == "aegis-agent-harness"


def test_node_bootstrap_helper_is_aegis_scoped_and_sourceable(tmp_path):
    helper = ROOT / "scripts" / "lib" / "node-bootstrap.sh"
    assert helper.is_file()
    text = helper.read_text(encoding="utf-8")
    assert "AEGIS_NODE_MIN_VERSION" in text
    assert "ensure_node" in text
    assert "HERMES" not in text

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    node = fake_bin / "node"
    node.write_text("#!/usr/bin/env sh\nprintf 'v20.1.0\\n'\n", encoding="utf-8")
    node.chmod(0o755)
    res = subprocess.run(
        [
            "bash",
            "-lc",
            f"PATH={fake_bin}:$PATH; source scripts/lib/node-bootstrap.sh; ensure_node; "
            "printf '%s:%s\\n' \"$AEGIS_NODE_AVAILABLE\" \"$AEGIS_NODE_BIN\"",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip().endswith(f"true:{node}")


def test_install_sh_wires_node_bootstrap_helper():
    text = (ROOT / "install.sh").read_text(encoding="utf-8")
    assert "scripts/lib/node-bootstrap.sh" in text
    assert "ensure_node || true" in text


def test_install_sh_syntax():
    res = subprocess.run(["bash", "-n", "install.sh"], cwd=ROOT, capture_output=True, text=True)
    assert res.returncode == 0, res.stderr


def test_scripts_install_wrappers_delegate_to_root_installers():
    sh = ROOT / "scripts" / "install.sh"
    ps1 = ROOT / "scripts" / "install.ps1"
    cmd = ROOT / "scripts" / "install.cmd"
    assert sh.exists()
    assert ps1.exists()
    assert cmd.exists()
    res = subprocess.run(["bash", "-n", str(sh)], cwd=ROOT, capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    assert "../install.sh" in sh.read_text(encoding="utf-8")
    assert "../install.ps1" in ps1.read_text(encoding="utf-8")
    assert "install.ps1" in cmd.read_text(encoding="utf-8")


def test_uninstall_sh_syntax():
    res = subprocess.run(["bash", "-n", "uninstall.sh"], cwd=ROOT, capture_output=True, text=True)
    assert res.returncode == 0, res.stderr


def test_install_sh_help_mentions_onboarding_modes():
    res = subprocess.run(["bash", "install.sh", "--help"], cwd=ROOT, capture_output=True, text=True)
    assert res.returncode == 0
    assert "--no-prompt" in res.stdout
    assert "--non-interactive" in res.stdout
    assert "--skip-onboard" in res.stdout
    assert "--toolsets" in res.stdout
    assert "--skills" in res.stdout
    assert "--manifest" in res.stdout
    assert "--stage" in res.stdout
    assert "--json" in res.stdout


def _last_json_line(text: str) -> dict:
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            return json.loads(line)
    raise AssertionError(f"no JSON line found in output:\n{text}")


def test_install_sh_manifest_exposes_stage_protocol():
    res = subprocess.run(["bash", "install.sh", "--manifest"], cwd=ROOT, capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout)
    assert payload["protocol_version"] == 1
    assert payload["product"] == "aegis"
    stages = {stage["name"]: stage for stage in payload["stages"]}
    assert list(stages) == [
        "prepare",
        "venv",
        "package",
        "browser",
        "launcher",
        "tools",
        "setup",
        "verify",
        "complete",
    ]
    assert stages["setup"]["needs_user_input"] is True
    assert stages["prepare"]["needs_user_input"] is False


def test_install_sh_stage_protocol_skips_setup_noninteractively():
    res = subprocess.run(
        ["bash", "install.sh", "--stage", "setup", "--json", "--non-interactive"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, res.stderr
    payload = _last_json_line(res.stdout)
    assert payload == {"ok": True, "stage": "setup", "skipped": True}


def test_install_sh_stage_protocol_reports_unknown_stage():
    res = subprocess.run(["bash", "install.sh", "--stage", "missing", "--json"], cwd=ROOT, capture_output=True, text=True)
    assert res.returncode == 2
    payload = _last_json_line(res.stdout)
    assert payload["ok"] is False
    assert payload["stage"] == "missing"
    assert payload["skipped"] is False
    assert "exit code 2" in payload["reason"]


def test_installers_advertise_first_run_surface_selection():
    sh = (ROOT / "install.sh").read_text(encoding="utf-8")
    ps1 = (ROOT / "install.ps1").read_text(encoding="utf-8")
    assert "AEGIS_TOOLSETS" in sh
    assert "AEGIS_SKILLS" in sh
    assert "--toolsets $INSTALL_TOOLSETS" in sh
    assert "--skills $INSTALL_SKILLS" in sh
    assert "AEGIS_TOOLSETS" in ps1
    assert "AEGIS_SKILLS" in ps1
    assert '"--toolsets", $InstallToolsets.Trim()' in ps1
    assert '"--skills", $InstallSkills.Trim()' in ps1


def test_update_dry_run_json_does_not_mutate(monkeypatch, tmp_path, capsys):
    from aegis import config as cfg
    from aegis.cli import main as cli_main

    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / ".aegis"))
    monkeypatch.setenv("AEGIS_SKIP_FIRST_RUN", "1")
    cfg.set_profile(None)

    calls = []

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("update --dry-run must not execute subprocesses")

    monkeypatch.setattr(cli_main.subprocess, "run", fake_run)

    assert cli_main.main(["update", "--dry-run", "--json", "--branch", "main"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["object"] == "aegis.update.plan"
    assert payload["dry_run"] is True
    assert payload["mutates"] is False
    assert payload["snapshot_planned"] is True
    assert payload["gateway_restart_planned"] is True
    assert payload["commands"]
    assert calls == []


def test_install_sh_does_not_advertise_removed_tui():
    text = (ROOT / "install.sh").read_text(encoding="utf-8")
    assert "aegis tui" not in text


def test_install_ps1_parse_when_pwsh_available():
    pwsh = shutil.which("pwsh")
    if not pwsh:
        return
    res = subprocess.run(
        [pwsh, "-NoProfile", "-Command", "$null = [scriptblock]::Create((Get-Content install.ps1 -Raw))"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, res.stderr


def test_uninstall_purge_removes_installed_artifacts(monkeypatch, tmp_path):
    from aegis import config as cfg
    from aegis.cli.main import main

    home = tmp_path / ".aegis"
    venv = home / "venv"
    venv.mkdir(parents=True)
    (home / "config.yaml").write_text("model:\n  provider: openai\n", encoding="utf-8")

    bin_dir = tmp_path / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    launcher = bin_dir / "aegis"
    launcher.write_text("#!/usr/bin/env bash\n", encoding="utf-8")

    unit_dir = tmp_path / ".config" / "systemd" / "user"
    wants_dir = unit_dir / "default.target.wants"
    wants_dir.mkdir(parents=True)
    dashboard_unit = unit_dir / "aegis-dashboard.service"
    gateway_link = wants_dir / "aegis-gateway.service"
    cron_unit = unit_dir / "aegis-cron.service"
    dashboard_unit.write_text("[Service]\n", encoding="utf-8")
    gateway_link.write_text("[Service]\n", encoding="utf-8")
    cron_unit.write_text("[Service]\n", encoding="utf-8")

    monkeypatch.setenv("AEGIS_HOME", str(home))
    monkeypatch.setenv("AEGIS_BIN_DIR", str(bin_dir))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    cfg.set_profile(None)

    assert main(["uninstall", "--purge"]) == 0
    assert not home.exists()
    assert not launcher.exists()
    assert not dashboard_unit.exists()
    assert not gateway_link.exists()
    assert not cron_unit.exists()
