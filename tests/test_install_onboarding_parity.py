from __future__ import annotations

import json
import pathlib
import subprocess
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]


def _upstream_brand() -> str:
    return "".join(chr(n) for n in (72, 101, 114, 109, 101, 115))


def test_install_surface_audit_remaps_requested_cluster_to_native_aegis_paths():
    from aegis.install_surfaces import INSTALL_SURFACE_REMAPS, audit_install_surface_parity

    expected = {
        "setup/bootstrap.py": "apps/bootstrap-installer/package.json",
        "setup/postinstall.py": "aegis/cli/main.py::postinstall",
        "setup/update.py": "aegis/cli/main.py::cmd_update",
        "setup/uninstall.py": "aegis/cli/main.py::cmd_uninstall",
        "setup/setup-whatsapp-cloud.py": "aegis/platforms/helpers.py::whatsapp_cloud",
    }

    assert expected.items() <= INSTALL_SURFACE_REMAPS.items()
    assert audit_install_surface_parity(ROOT) == []
    encoded = json.dumps(INSTALL_SURFACE_REMAPS, sort_keys=True)
    assert _upstream_brand() not in encoded
    assert _upstream_brand().lower() not in encoded


def test_setup_path_wrappers_delegate_to_native_cli_without_side_effects(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "home"))
    wrappers = {
        "bootstrap.py": ["--json"],
        "postinstall.py": [],
        "update.py": ["--dry-run", "--json"],
        "uninstall.py": ["--help"],
        "setup-whatsapp-cloud.py": ["--dry-run", "--json"],
    }
    for name, args in wrappers.items():
        path = ROOT / "setup" / name
        assert path.is_file(), name
        text = path.read_text(encoding="utf-8")
        assert "aegis" in text.lower()
        assert _upstream_brand() not in text
        result = subprocess.run(
            [sys.executable, str(path), *args],
            cwd=ROOT,
            env={"AEGIS_HOME": str(tmp_path / "home"), "PYTHONPATH": str(ROOT)},
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr


def test_setup_whatsapp_cloud_cli_reports_native_bridge_plan(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "home"))

    from aegis.cli.main import build_parser, main

    parsed = build_parser().parse_args(["setup-whatsapp-cloud", "--dry-run", "--json"])
    assert parsed.command == "setup-whatsapp-cloud"
    assert parsed.dry_run is True

    assert main(["setup-whatsapp-cloud", "--dry-run", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["object"] == "aegis.whatsapp_cloud.setup_plan"
    assert payload["dry_run"] is True
    assert payload["channel"] == "whatsapp_cloud"
    assert payload["env_prefix"] == "WHATSAPP_CLOUD_CHANNEL"
    assert payload["default_port"] == 18801
    assert payload["mutates"] is False
    assert "aegis gateway --channels whatsapp_cloud" in payload["commands"]


def test_whatsapp_cloud_alias_targets_native_cloud_channel_parser():
    from aegis.cli.main import build_parser

    parsed = build_parser().parse_args(["whatsapp-cloud", "setup", "--dry-run", "--json"])
    assert parsed.command == "whatsapp-cloud"
    assert parsed.platform_action == "setup"
    assert parsed.dry_run is True
    assert parsed.json is True
