#!/usr/bin/env python3
"""Bootstrap the native AEGIS kanban pipeline surface."""

from __future__ import annotations

import json

import aegis.kanban  # noqa: F401
import aegis.kanban_auto  # noqa: F401


def main() -> int:
    print(json.dumps({"ok": True, "module": "aegis.kanban", "automation": "aegis.kanban_auto"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
