from __future__ import annotations

import json
from datetime import datetime, timezone

from scripts.release_provenance import (
    collect_artifacts,
    main,
    verify_provenance,
    write_provenance,
)


def test_release_provenance_writes_hashes_summary_and_sbom(tmp_path):
    artifacts_dir = tmp_path / "dist"
    artifacts_dir.mkdir()
    (artifacts_dir / "aegis.whl").write_text("wheel", encoding="utf-8")
    (artifacts_dir / "aegis.tar.gz").write_text("sdist", encoding="utf-8")
    out = tmp_path / "provenance"

    artifacts = collect_artifacts([artifacts_dir], out_dir=out)
    summary = write_provenance(
        artifacts,
        out,
        now=datetime(2026, 6, 23, 12, tzinfo=timezone.utc),
    )

    assert summary["artifact_count"] == 2
    assert (out / "SHA256SUMS").read_text(encoding="utf-8").count("\n") == 2
    sbom = json.loads((out / "sbom.cdx.json").read_text(encoding="utf-8"))
    assert sbom["bomFormat"] == "CycloneDX"
    assert {component["name"] for component in sbom["components"]} == {"aegis.tar.gz", "aegis.whl"}
    assert verify_provenance(artifacts, out) == []


def test_release_provenance_check_fails_on_artifact_drift(tmp_path):
    artifacts_dir = tmp_path / "release"
    artifacts_dir.mkdir()
    artifact = artifacts_dir / "AEGIS.AppImage"
    artifact.write_text("first", encoding="utf-8")
    out = tmp_path / "provenance"

    artifacts = collect_artifacts([artifacts_dir], out_dir=out)
    write_provenance(artifacts, out)
    artifact.write_text("changed", encoding="utf-8")

    errors = verify_provenance(collect_artifacts([artifacts_dir], out_dir=out), out)
    assert errors == ["sha256 mismatch for AEGIS.AppImage"]


def test_release_provenance_main_requires_artifacts_unless_allowed(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()

    assert main(["--artifact-dir", str(empty), "--out", str(tmp_path / "out")]) == 2
    assert main(["--artifact-dir", str(empty), "--out", str(tmp_path / "out"), "--allow-empty"]) == 0
