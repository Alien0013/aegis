#!/usr/bin/env python3
"""Android compatibility helper.

AEGIS avoids a psutil runtime dependency by reading host stats from Python stdlib
and /proc where available. This script is kept for install-surface parity and
prints the native AEGIS policy instead of installing unnecessary packages.
"""

from __future__ import annotations

import json


def main() -> int:
    print(json.dumps({"ok": True, "package": "psutil", "action": "not_required", "reason": "AEGIS avoids a psutil runtime dependency"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
