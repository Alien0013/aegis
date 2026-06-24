#!/usr/bin/env python3
"""Generate and verify release artifact hashes and a small artifact SBOM."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


PROVENANCE_FILENAMES = {"SHA256SUMS", "sbom.cdx.json", "release-summary.json"}


@dataclass(frozen=True)
class Artifact:
    path: Path
    name: str
    size: int
    sha256: str


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def collect_artifacts(artifact_dirs: Iterable[Path], *, out_dir: Path | None = None) -> list[Artifact]:
    roots = [root.resolve() for root in artifact_dirs]
    multi_root = len(roots) > 1
    artifacts: list[Artifact] = []
    for root in roots:
        if not root.exists():
            continue
        if root.is_file():
            files = [root]
            base = root.parent
        else:
            files = sorted(path for path in root.rglob("*") if path.is_file())
            base = root
        for file in files:
            if file.name in PROVENANCE_FILENAMES:
                continue
            if out_dir is not None and _is_relative_to(file, out_dir):
                continue
            rel = file.relative_to(base).as_posix()
            name = f"{root.name}/{rel}" if multi_root else rel
            stat = file.stat()
            artifacts.append(Artifact(path=file, name=name, size=stat.st_size, sha256=_sha256(file)))
    return sorted(artifacts, key=lambda item: item.name)


def _summary(artifacts: list[Artifact], *, generated_at: str) -> dict:
    return {
        "schema": "aegis.release-provenance.v1",
        "generated_at": generated_at,
        "artifact_count": len(artifacts),
        "total_bytes": sum(item.size for item in artifacts),
        "artifacts": [
            {"name": item.name, "size": item.size, "sha256": item.sha256}
            for item in artifacts
        ],
    }


def _sbom(artifacts: list[Artifact], *, generated_at: str) -> dict:
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": f"urn:uuid:{hashlib.sha256(generated_at.encode()).hexdigest()[:32]}",
        "version": 1,
        "metadata": {
            "timestamp": generated_at,
            "tools": [
                {
                    "vendor": "AEGIS",
                    "name": "scripts/release_provenance.py",
                    "version": "1",
                }
            ],
            "component": {
                "type": "application",
                "name": "aegis-release-artifacts",
            },
        },
        "components": [
            {
                "type": "file",
                "name": item.name,
                "hashes": [{"alg": "SHA-256", "content": item.sha256}],
                "properties": [
                    {"name": "aegis:relative_path", "value": item.name},
                    {"name": "aegis:size_bytes", "value": str(item.size)},
                ],
            }
            for item in artifacts
        ],
    }


def write_provenance(artifacts: list[Artifact], out_dir: Path, *, now: datetime | None = None) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    generated_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    sums = "\n".join(f"{item.sha256}  {item.name}" for item in artifacts) + ("\n" if artifacts else "")
    (out_dir / "SHA256SUMS").write_text(sums, encoding="utf-8")
    summary = _summary(artifacts, generated_at=generated_at)
    (out_dir / "release-summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (out_dir / "sbom.cdx.json").write_text(json.dumps(_sbom(artifacts, generated_at=generated_at), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def read_sha256sums(path: Path) -> dict[str, str]:
    rows: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, _, name = line.partition("  ")
        if not digest or not name:
            raise ValueError(f"invalid SHA256SUMS line: {line!r}")
        rows[name] = digest
    return rows


def verify_provenance(artifacts: list[Artifact], out_dir: Path) -> list[str]:
    sums_path = out_dir / "SHA256SUMS"
    if not sums_path.exists():
        return [f"missing {sums_path}"]
    expected = read_sha256sums(sums_path)
    current = {item.name: item.sha256 for item in artifacts}
    errors: list[str] = []
    for name, digest in expected.items():
        if name not in current:
            errors.append(f"missing artifact listed in SHA256SUMS: {name}")
        elif current[name] != digest:
            errors.append(f"sha256 mismatch for {name}")
    for name in current:
        if name not in expected:
            errors.append(f"artifact missing from SHA256SUMS: {name}")
    for required in ("release-summary.json", "sbom.cdx.json"):
        if not (out_dir / required).exists():
            errors.append(f"missing {required}")
    return errors


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate or verify release artifact hashes and SBOM.")
    parser.add_argument("--artifact-dir", action="append", default=[], help="Artifact directory or file to include.")
    parser.add_argument("--out", default="release-provenance", help="Output directory for SHA256SUMS/SBOM/summary.")
    parser.add_argument("--check", action="store_true", help="Verify existing provenance instead of writing it.")
    parser.add_argument("--allow-empty", action="store_true", help="Allow zero artifacts.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    artifact_dirs = [Path(item) for item in args.artifact_dir] or [Path("dist"), Path("desktop/release")]
    out_dir = Path(args.out)
    artifacts = collect_artifacts(artifact_dirs, out_dir=out_dir)
    if not artifacts and not args.allow_empty:
        print("release provenance: no artifacts found", file=sys.stderr)
        return 2
    if args.check:
        errors = verify_provenance(artifacts, out_dir)
        if errors:
            for error in errors:
                print(f"release provenance: {error}", file=sys.stderr)
            return 1
        print(f"release provenance: verified {len(artifacts)} artifact(s)")
        return 0
    summary = write_provenance(artifacts, out_dir)
    print(
        "release provenance: wrote "
        f"{summary['artifact_count']} artifact hash(es), {summary['total_bytes']} byte(s) -> {out_dir}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
