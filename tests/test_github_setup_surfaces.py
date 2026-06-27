from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _upstream_brand() -> str:
    return "".join(chr(n) for n in (72, 101, 114, 109, 101, 115))


def test_setup_help_issue_template_is_aegis_native():
    path = ROOT / ".github" / "ISSUE_TEMPLATE" / "setup_help.yml"
    text = path.read_text(encoding="utf-8")

    assert "Setup / Installation Help" in text
    assert "Having trouble installing or configuring AEGIS" in text
    assert "aegis debug share" in text
    assert "aegis update" in text
    assert "aegis setup" in text
    assert "AEGIS_HOME" in text
    assert _upstream_brand() not in text
    assert _upstream_brand().lower() not in text


def test_nix_setup_action_is_aegis_native_and_cache_aware():
    path = ROOT / ".github" / "actions" / "nix-setup" / "action.yml"
    text = path.read_text(encoding="utf-8")

    assert "Setup Nix" in text
    assert "DeterminateSystems/nix-installer-action" in text
    assert "cachix/cachix-action" in text
    assert "cachix-auth-token" in text
    assert "name: aegis-agent" in text
    assert _upstream_brand() not in text
    assert _upstream_brand().lower() not in text
