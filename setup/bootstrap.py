#!/usr/bin/env python3
"""Print the AEGIS-native bootstrap/install surface plan."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aegis.install_surfaces import print_bootstrap_plan  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Show AEGIS bootstrap/install surfaces.")
    parser.add_argument("--json", action="store_true", help="print machine-readable output")
    args = parser.parse_args(argv)
    print_bootstrap_plan(json_output=args.json, root=ROOT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
