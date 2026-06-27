#!/usr/bin/env python3
"""aegis bootstrap compatibility entry point."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AEGIS bootstrap compatibility launcher")
    parser.add_argument("args", nargs=argparse.REMAINDER)
    ns = parser.parse_args(argv)
    root = Path(__file__).resolve().parent
    cmd = [str(root / "install.sh"), *ns.args]
    return subprocess.call(cmd)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
